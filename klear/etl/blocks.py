# Builds the 2020 Census block table with population, ZCTA and 2022-plan House and Senate membership.
import zipfile
from pathlib import Path

import geopandas as gpd
import httpx
import pandas as pd
import pyogrio

from klear.etl.census_api import get_pl_blocks
from klear.etl.fetch import fetch

COUNTIES = ["001", "003", "005", "007", "009"]
BEF_BASE = (
    "https://www2.census.gov/programs-surveys/decennial/rdo/mapping-files/2025/2024-state-legislative-bef"
)
# Step 0: Hawaii plans unchanged since 2022, so the national 2024 files carry the 2022 Reapportionment lines.
BEF = {
    "house": (f"{BEF_BASE}/sldl24.zip", "blocks/sldl24.zip", "NationalSLDL24.txt"),
    "senate": (f"{BEF_BASE}/sldu24.zip", "blocks/sldu24.zip", "NationalSLDU24.txt"),
}
ZCTA_URL = "https://www2.census.gov/geo/docs/maps-data/data/rel2020/zcta520/tab20_zcta520_tabblock20_natl.txt"
TIGER_URL = "https://www2.census.gov/geo/tiger/TIGER2020/TABBLOCK20/tl_2020_15_tabblock20.zip"
CHUNK_ROWS = 500_000
AREA_CRS = "ESRI:102007"
MAX_SNAP_M = 1000.0
COLUMNS = ["block", "county", "tract", "zcta", "house", "senate", "pop2020"]


def read_bef(path: Path, member: str) -> pd.Series:
    parts = []
    with zipfile.ZipFile(path) as z, z.open(member) as f:
        for chunk in pd.read_csv(f, dtype=str, chunksize=CHUNK_ROWS, keep_default_na=False):
            geoid, district = chunk.columns[:2]
            parts.append(chunk.loc[chunk[geoid].str.startswith("15"), [geoid, district]])
    df = pd.concat(parts, ignore_index=True)
    return pd.Series(df.iloc[:, 1].to_numpy(), index=df.iloc[:, 0].to_numpy(), name="district")


def read_zcta(path: Path) -> pd.Series:
    cols = ["GEOID_ZCTA5_20", "GEOID_TABBLOCK_20", "AREALAND_PART"]
    parts = []
    reader = pd.read_csv(
        path,
        sep="|",
        dtype=str,
        usecols=cols,
        chunksize=CHUNK_ROWS,
        encoding="utf-8-sig",
        keep_default_na=False,
    )
    for chunk in reader:
        keep = chunk["GEOID_TABBLOCK_20"].str.startswith("15") & (chunk["GEOID_ZCTA5_20"] != "")
        parts.append(chunk.loc[keep])
    df = pd.concat(parts, ignore_index=True)
    df["land"] = pd.to_numeric(df["AREALAND_PART"])
    # keep the ZCTA with the most land if a block is ever split
    df = df.sort_values(["GEOID_TABBLOCK_20", "land"], ascending=[True, False])
    df = df.drop_duplicates("GEOID_TABBLOCK_20")
    return pd.Series(df["GEOID_ZCTA5_20"].to_numpy(), index=df["GEOID_TABBLOCK_20"].to_numpy(), name="zcta")


def load_block_points(client: httpx.Client | None = None) -> pd.DataFrame:
    path = fetch(TIGER_URL, "blocks/tl_2020_15_tabblock20.zip", client=client)
    df = pyogrio.read_dataframe(path, columns=["GEOID20", "INTPTLAT20", "INTPTLON20"], read_geometry=False)
    return pd.DataFrame(
        {"block": df["GEOID20"], "lon": df["INTPTLON20"].astype(float), "lat": df["INTPTLAT20"].astype(float)}
    )


def spatial_membership(
    points: pd.DataFrame, districts: gpd.GeoDataFrame, max_snap_m: float = MAX_SNAP_M
) -> pd.DataFrame:
    pts = gpd.GeoDataFrame(
        {"block": points["block"].to_numpy()},
        geometry=gpd.points_from_xy(points["lon"], points["lat"]),
        crs="EPSG:4326",
    )
    polys = districts[["geo_id", "geometry"]].to_crs("EPSG:4326")
    hits = gpd.sjoin(pts, polys, predicate="intersects", how="inner")
    agg = hits.groupby("block")["geo_id"].agg(["min", "size"])
    out = pd.DataFrame({"block": pts["block"]})
    out["geo_id"] = out["block"].map(agg["min"])
    out["method"] = out["block"].map(agg["size"]).map(lambda n: "within" if n == 1 else "edge")
    out["dist_m"] = 0.0
    miss = out["geo_id"].isna().to_numpy()
    if miss.any():
        near = gpd.sjoin_nearest(
            pts.loc[miss].to_crs(AREA_CRS),
            polys.to_crs(AREA_CRS),
            how="inner",
            max_distance=max_snap_m,
            distance_col="dist_m",
        )
        near = near.sort_values(["block", "geo_id"]).drop_duplicates("block").set_index("block")
        idx = out.index[miss]
        out.loc[idx, "geo_id"] = out.loc[idx, "block"].map(near["geo_id"])
        out.loc[idx, "dist_m"] = out.loc[idx, "block"].map(near["dist_m"])
        snapped = out.loc[idx, "geo_id"].notna()
        out.loc[idx, "method"] = snapped.map({True: "snapped", False: "none"})
    return out.reset_index(drop=True)


def _check_index(name: str, s: pd.Series, blocks: pd.Index) -> None:
    if s.index.duplicated().any():
        raise ValueError(f"{name}: {int(s.index.duplicated().sum())} duplicate blocks")
    if s.isna().any() or (s == "").any():
        raise ValueError(f"{name}: blank values")
    missing = blocks.difference(s.index)
    extra = s.index.difference(blocks)
    if len(missing) or len(extra):
        raise ValueError(f"{name}: {len(missing)} blocks missing, {len(extra)} unknown blocks")


def assemble(pop: pd.Series, house: pd.Series, senate: pd.Series, zcta: pd.Series) -> pd.DataFrame:
    if pop.index.duplicated().any():
        raise ValueError("pop2020: duplicate blocks")
    blocks = pd.Index(sorted(pop.index))
    _check_index("house", house, blocks)
    _check_index("senate", senate, blocks)
    if zcta.index.duplicated().any() or len(zcta.index.difference(blocks)):
        raise ValueError("zcta: duplicate or unknown blocks")
    b = pd.Series(blocks, dtype=str)
    return pd.DataFrame(
        {
            "block": b,
            "county": b.str[:5],
            "tract": b.str[:11],
            "zcta": zcta.reindex(blocks).to_numpy(),
            "house": house.reindex(blocks).to_numpy(),
            "senate": senate.reindex(blocks).to_numpy(),
            "pop2020": pop.reindex(blocks).astype("int64").to_numpy(),
        },
        columns=COLUMNS,
    )


def build_block_table(client: httpx.Client | None = None) -> pd.DataFrame:
    pl = pd.concat([get_pl_blocks(c, client=client) for c in COUNTIES], ignore_index=True)
    pop = pd.Series(pl["pop2020"].to_numpy(), index=pl["block"].to_numpy())
    members = {}
    for chamber, (url, name, member) in BEF.items():
        members[chamber] = read_bef(fetch(url, name, client=client), member)
    zcta = read_zcta(fetch(ZCTA_URL, "blocks/tab20_zcta520_tabblock20_natl.txt", client=client))
    return assemble(pop, members["house"], members["senate"], zcta)
