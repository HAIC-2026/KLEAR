# Loads public housing, LIHTC and health center sites and places each point in a House and Senate district.
from pathlib import Path

import geopandas as gpd
import pandas as pd

from klear.etl.arcgis import query_features
from klear.etl.blocks import AREA_CRS
from klear.etl.fetch import fetch
from klear.etl.hud import HUD_BASE, HUD_PAGES
from klear.store.db import FACT_COLS, SITE_COLS
from klear.text import hawaiian

PUBLIC_HOUSING_URL = f"{HUD_BASE}/Public_Housing_Developments/FeatureServer/0"
PUBLIC_HOUSING_PAGE_URL = f"{HUD_PAGES}/HUD::public-housing-developments"
LIHTC_URL = f"{HUD_BASE}/LIHTC/FeatureServer/0"
LIHTC_PAGE_URL = f"{HUD_PAGES}/HUD::low-income-housing-tax-credit-properties-1"
HRSA_URL = (
    "https://data.hrsa.gov/DataDownload/DD_Files/Health_Center_Service_Delivery_and_LookAlike_Sites.csv"
)
# the HRSA download page, opened at its health center files
HRSA_PAGE_URL = "https://data.hrsa.gov/data/download?data=HCC"
SITE_SOURCES = {
    "public_housing": "hud_public_housing",
    "lihtc": "hud_lihtc",
    "health_center": "hrsa_health_centers",
}
SITE_INDICATORS = {
    "sites_public_housing": ("public_housing", "Public housing developments", False),
    "sites_lihtc": ("lihtc", "Low-income housing tax credit projects", False),
    "sites_health_center": ("health_center", "Health center sites", False),
    "units_public_housing": ("public_housing", "Public housing units", True),
    "units_lihtc": ("lihtc", "Low-income housing tax credit low-income units", True),
}
SITES_UNIVERSE = "Sites in the source file"
UNITS_UNIVERSE = "Housing units at sites in the source file"
HRSA_COLS = {
    "id": "BPHC Assigned Number",
    "name": "Site Name",
    "address": "Site Address",
    "city": "Site City",
    "state": "Site State Abbreviation",
    "status": "Site Status Description",
    "type": "Health Center Location Type Description",
    "lon": "Geocoding Artifact Address Primary X Coordinate",
    "lat": "Geocoding Artifact Address Primary Y Coordinate",
}


def site_indicator_rows() -> pd.DataFrame:
    return pd.DataFrame(
        [
            {
                "indicator_id": i,
                "label": label,
                "unit": "count",
                "universe": UNITS_UNIVERSE if units else SITES_UNIVERSE,
                "acs_var": None,
                "additive": True,
            }
            for i, (_, label, units) in SITE_INDICATORS.items()
        ]
    )


def _frame(kind: str, ids, names, addrs, lats, lons, units) -> pd.DataFrame:
    df = pd.DataFrame(
        {
            "site_id": [f"{kind}_{i}" for i in ids],
            "kind": kind,
            "name": [hawaiian(str(n).strip()) for n in names],
            "address": [None if pd.isna(a) else str(a).strip() for a in addrs],
            "lat": pd.to_numeric(pd.Series(list(lats)), errors="coerce"),
            "lon": pd.to_numeric(pd.Series(list(lons)), errors="coerce"),
            "units": pd.to_numeric(pd.Series(list(units)), errors="coerce").round().astype("Int64"),
            "source_id": SITE_SOURCES[kind],
        }
    )
    return df


def parse_hrsa(path: Path) -> pd.DataFrame:
    raw = pd.read_csv(path, dtype=str, encoding="utf-8-sig")
    c = HRSA_COLS
    df = raw[(raw[c["state"]].str.strip() == "HI") & (raw[c["status"]].str.strip() == "Active")]
    df = df[df[c["type"]].str.strip() != "Mobile Van"]
    addr = df[c["address"]].fillna("") + ", " + df[c["city"]].fillna("")
    return _frame(
        "health_center",
        df[c["id"]].str.strip(),
        df[c["name"]],
        addr,
        df[c["lat"]],
        df[c["lon"]],
        [None] * len(df),
    ).reset_index(drop=True)


def load_hud_sites(client=None) -> pd.DataFrame:
    ph = pd.DataFrame(
        query_features(
            PUBLIC_HOUSING_URL,
            "STATE2KX='15'",
            ["DEVELOPMENT_CODE", "PROJECT_NAME", "STD_ADDR", "TOTAL_UNITS", "LAT", "LON"],
            "hud/public_housing",
            client=client,
        )
    )
    lihtc = pd.DataFrame(
        query_features(
            LIHTC_URL,
            "PROJ_ST='HI'",
            ["HUD_ID", "PROJECT", "PROJ_ADD", "PROJ_CTY", "LI_UNITS", "LAT", "LON"],
            "hud/lihtc",
            client=client,
        )
    )
    a = _frame(
        "public_housing", ph.DEVELOPMENT_CODE, ph.PROJECT_NAME, ph.STD_ADDR, ph.LAT, ph.LON, ph.TOTAL_UNITS
    )
    lihtc_addr = lihtc.PROJ_ADD.fillna("") + ", " + lihtc.PROJ_CTY.fillna("")
    b = _frame("lihtc", lihtc.HUD_ID, lihtc.PROJECT, lihtc_addr, lihtc.LAT, lihtc.LON, lihtc.LI_UNITS)
    return pd.concat([a, b], ignore_index=True)


def assign_points(df: pd.DataFrame, districts: gpd.GeoDataFrame, chamber: str) -> pd.DataFrame:
    # inside a polygon is distance 0, otherwise the nearest district with its metric distance
    # snapped_m keeps the larger distance when a column from the other chamber exists
    out = df.reset_index(drop=True).copy()
    pts = gpd.GeoDataFrame(
        {"i": out.index}, geometry=gpd.points_from_xy(out["lon"], out["lat"]), crs="EPSG:4326"
    )
    polys = districts[["geo_id", "geometry"]].to_crs("EPSG:4326")
    hits = gpd.sjoin(pts, polys, predicate="intersects", how="inner")
    inside = hits.groupby("i")["geo_id"].min()
    geo = out.index.to_series().map(inside)
    dist = pd.Series(0.0, index=out.index)
    miss = geo.isna()
    if miss.any():
        near = gpd.sjoin_nearest(
            pts[miss.to_numpy()].to_crs(AREA_CRS), polys.to_crs(AREA_CRS), how="inner", distance_col="dist_m"
        )
        near = near.sort_values(["i", "dist_m", "geo_id"]).drop_duplicates("i").set_index("i")
        geo[miss] = near["geo_id"].reindex(geo.index[miss])
        dist[miss] = near["dist_m"].reindex(dist.index[miss])
    out[chamber] = geo
    out["snapped_m"] = dist if "snapped_m" not in out else out["snapped_m"].combine(dist, max)
    return out


def load_sites(house: gpd.GeoDataFrame, senate: gpd.GeoDataFrame, client=None) -> pd.DataFrame:
    hrsa = parse_hrsa(fetch(HRSA_URL, "hrsa/health_center_sites.csv", client=client))
    sites = pd.concat([load_hud_sites(client), hrsa], ignore_index=True)
    no_xy = sites[sites[["lat", "lon"]].isna().any(axis=1)]
    if not no_xy.empty:
        raise ValueError(f"sites without coordinates: {', '.join(no_xy.site_id)}")
    sites = assign_points(assign_points(sites, house, "house"), senate, "senate")
    return sites.sort_values("site_id", ignore_index=True)[SITE_COLS]


def site_facts(sites: pd.DataFrame, districts: dict[str, list[str]] | None = None) -> pd.DataFrame:
    # counts are 0 for districts without sites, units are absent where no site reports units
    parts = []
    for chamber in ("house", "senate"):
        every = (
            sorted(set(districts[chamber]) | set(sites[chamber]))
            if districts
            else sorted(set(sites[chamber]))
        )
        for ind, (kind, _, is_units) in SITE_INDICATORS.items():
            sub = sites[sites.kind == kind]
            if is_units:
                g = sub.groupby(chamber)["units"].sum(min_count=1).dropna().astype(float)
            else:
                g = sub.groupby(chamber).size().reindex(every, fill_value=0).astype(float)
            parts.append(
                pd.DataFrame(
                    {
                        "geo_type": chamber,
                        "geo_id": g.index.to_numpy(),
                        "indicator_id": ind,
                        "value": g.to_numpy(),
                        "moe": float("nan"),
                        "value_status": "ok",
                        "moe_status": "not_applicable",
                        "tier": "P",
                        "source_id": SITE_SOURCES[kind],
                        "table_id": None,
                        "value_low": float("nan"),
                        "value_high": float("nan"),
                    }
                )
            )
    return pd.concat(parts, ignore_index=True)[FACT_COLS]
