# Builds the Census place (CDP) table and its overlap with counties, districts and ZCTAs from block assignments.
import re
import unicodedata
import zipfile
from pathlib import Path

import httpx
import pandas as pd
import yaml

from klear import config
from klear.etl.fetch import fetch

BAF_URL = "https://www2.census.gov/geo/docs/maps-data/data/baf2020/BlockAssign_ST15_HI.zip"
BAF_MEMBER = "BlockAssign_ST15_HI_INCPLACE_CDP.txt"
GAZ_URL = "https://www2.census.gov/geo/docs/maps-data/data/gazetteer/2020_Gazetteer/2020_gaz_place_15.txt"
OVERRIDES_PATH = config.ROOT / "data" / "places" / "hawaiian_names.yaml"
GEO_TYPES = ("county", "house", "senate", "zcta")
OKINA = "ʻ"
_LOOKALIKES = str.maketrans({"‘": OKINA, "’": OKINA, "'": OKINA, "`": OKINA})
_CDP = re.compile(r"\s+CDP$")


def display_name(text: str) -> str:
    return unicodedata.normalize("NFC", text.translate(_LOOKALIKES)).strip()


def plain_name(text: str) -> str:
    s = unicodedata.normalize("NFD", display_name(text).casefold())
    s = "".join(c for c in s if not unicodedata.combining(c) and c != OKINA)
    return " ".join(s.replace("-", " ").split())


def fetch_baf(client: httpx.Client | None = None) -> Path:
    return fetch(BAF_URL, "blocks/BlockAssign_ST15_HI.zip", client=client)


def fetch_names(client: httpx.Client | None = None) -> Path:
    return fetch(GAZ_URL, "blocks/2020_gaz_place_15.txt", client=client)


def read_baf(path: Path) -> pd.Series:
    with zipfile.ZipFile(path) as z, z.open(BAF_MEMBER) as f:
        df = pd.read_csv(f, sep="|", dtype=str, keep_default_na=False)
    df = df[df["PLACEFP"] != ""]
    return pd.Series("15" + df["PLACEFP"].to_numpy(), index=df["BLOCKID"].to_numpy(), name="place_id")


def read_census_names(path: Path) -> dict[str, str]:
    df = pd.read_csv(path, sep="\t", dtype=str, keep_default_na=False)
    df.columns = [c.strip() for c in df.columns]
    return {r.GEOID.strip(): _CDP.sub("", r.NAME.strip()) for r in df.itertuples()}


def load_overrides(path: Path = OVERRIDES_PATH) -> dict[str, str]:
    data = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    return {str(k): display_name(v) for k, v in data.items()}


def place_names(census: dict[str, str], overrides: dict[str, str]) -> dict[str, str]:
    return {pid: overrides.get(pid, display_name(name)) for pid, name in census.items()}


def build_places(
    con, baf_path: Path, names_path: Path, overrides: dict[str, str] | None = None
) -> tuple[pd.DataFrame, pd.DataFrame]:
    # returns the place and place_overlap frames for the blocks already in the store
    if overrides is None:
        overrides = load_overrides()
    names = place_names(read_census_names(names_path), overrides)
    baf = read_baf(baf_path)
    blocks = con.execute("SELECT block, county, zcta, house, senate, pop2020 FROM blocks").df()
    blocks["place_id"] = blocks["block"].map(baf)
    blocks = blocks[blocks["place_id"].notna()]
    unknown = sorted(set(blocks["place_id"]) - set(names))
    if unknown:
        raise ValueError(f"places: {len(unknown)} place ids without a name: {unknown[:3]}")
    pop = blocks.groupby("place_id")["pop2020"].sum()
    pop = pop[pop > 0]
    place = pd.DataFrame(
        {
            "place_id": pop.index,
            "name": [names[p] for p in pop.index],
            "plain": [plain_name(names[p]) for p in pop.index],
            "pop2020": pop.to_numpy(dtype="int64"),
        }
    )
    parts = []
    for geo_type in GEO_TYPES:
        g = blocks[blocks["place_id"].isin(pop.index) & blocks[geo_type].notna()]
        s = g.groupby(["place_id", geo_type])["pop2020"].sum()
        s = s[s > 0].rename("pop2020").reset_index().rename(columns={geo_type: "geo_id"})
        s.insert(1, "geo_type", geo_type)
        parts.append(s)
    overlap = pd.concat(parts, ignore_index=True)
    overlap["share"] = overlap["pop2020"] / overlap["place_id"].map(pop)
    return place, overlap
