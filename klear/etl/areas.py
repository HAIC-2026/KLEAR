# Builds islands, Honolulu neighborhood board areas and Census county divisions, with their overlap with counties,
# districts, ZIP code areas and Census places, from the internal point of each 2020 block, plus search aliases.
import re
from dataclasses import dataclass
from pathlib import Path

import geopandas as gpd
import httpx
import pandas as pd
import pyogrio
import yaml

from klear import config
from klear.etl.blocks import spatial_membership
from klear.etl.boundaries import _check_page
from klear.etl.fetch import fetch
from klear.etl.places_gazetteer import display_name, search_plain

NAMES_PATH = config.ROOT / "data" / "places" / "areas.yaml"
COUSUB_URL = "https://www2.census.gov/geo/tiger/TIGER2020/COUSUB/tl_2020_15_cousub.zip"
BOARDS_LAYER = "https://services.arcgis.com/tNJpAOha4mODLkXz/arcgis/rest/services/AdministrativePolitical/FeatureServer/5"
# Step 0: layer 5 holds the 128 board subdistricts (under the 2000-feature page limit), with the board in board_num.
BOARDS_URL = (
    f"{BOARDS_LAYER}/query?where=1%3D1&outFields=board_num&outSR=4326&f=geojson&orderByFields=objectid"
)
HONOLULU = "15003"
# Neighbor-island counties searched by county division. Kalawao is a county of its own, and Oʻahu uses boards.
DIVISION_COUNTIES = ("15001", "15007", "15009")
# Island boxes (west, south, east, north) with the map's zoom group. The channels between islands are wider than the
# overlap of any two boxes, and a populated block inside no box or inside two fails the areas check.
ISLANDS = {
    "Niʻihau": ("kauai", (-160.3, 21.7, -160.0, 22.1)),
    "Kauaʻi": ("kauai", (-159.85, 21.8, -159.25, 22.3)),
    "Oʻahu": ("oahu", (-158.35, 21.2, -157.6, 21.75)),
    "Molokaʻi": ("maui", (-157.35, 21.0, -156.7, 21.25)),
    "Lānaʻi": ("maui", (-157.1, 20.7, -156.78, 20.95)),
    "Kahoʻolawe": ("maui", (-156.72, 20.48, -156.5, 20.62)),
    "Maui": ("maui", (-156.72, 20.55, -155.95, 21.05)),
    "Hawaiʻi": ("hawaii", (-156.1, 18.85, -154.75, 20.3)),
}
# Search and result name of each island, where the bare name would mean the state.
ISLAND_NAMES = {name: "Hawaiʻi Island" if name == "Hawaiʻi" else name for name in ISLANDS}
ISLAND_IDS = {name: search_plain(name).replace(" ", "") for name in ISLANDS}
KINDS = ("island", "neighborhood", "division")
GEO_TYPES = ("county", "house", "senate", "zcta", "place")
_QUALIFIED = re.compile(r"^(.*?)\s*\((.+)\)$")


@dataclass
class Names:
    divisions: dict[str, str]
    neighborhoods: dict[int, str]
    # other names by their display spelling, each with its targets by kind
    aliases: dict[str, dict[str, list[str]]]


@dataclass
class Areas:
    area: pd.DataFrame
    overlap: pd.DataFrame
    alias: pd.DataFrame
    blocks: pd.DataFrame
    # alias targets that matched no area or place, or several: alias, kind, target, matches and whether the
    # name is a known one (an area with no residents in this build) rather than a misspelling
    unmatched: list[dict]


def load_names(path: Path = NAMES_PATH) -> Names:
    data = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    return Names(
        divisions={str(k): display_name(v) for k, v in (data.get("divisions") or {}).items()},
        neighborhoods={int(k): display_name(v) for k, v in (data.get("neighborhoods") or {}).items()},
        aliases={
            display_name(str(alias)): {
                kind: [display_name(t) for t in targets] for kind, targets in spec.items()
            }
            for alias, spec in (data.get("aliases") or {}).items()
        },
    )


def fetch_divisions(client: httpx.Client | None = None) -> Path:
    return fetch(COUSUB_URL, "areas/tl_2020_15_cousub.zip", client=client)


def fetch_boards(client: httpx.Client | None = None) -> Path:
    return fetch(
        BOARDS_URL, "areas/neighborhood_board_subdistricts.geojson", client=client, validate=_check_page
    )


def read_divisions(path: Path) -> gpd.GeoDataFrame:
    """Census county divisions of the neighbor-island counties, as geo_id (GEOID), census_name and geometry."""
    raw = pyogrio.read_dataframe(path, columns=["GEOID", "NAME"])
    raw = raw[("15" + raw["GEOID"].str[2:5]).isin(DIVISION_COUNTIES)]
    return gpd.GeoDataFrame(
        {"geo_id": raw["GEOID"].to_numpy(), "census_name": raw["NAME"].to_numpy()},
        geometry=raw.geometry.to_numpy(),
        crs=raw.crs,
    ).to_crs("EPSG:4326")


def read_boards(path: Path) -> gpd.GeoDataFrame:
    """Neighborhood board areas, the City's subdistricts dissolved by board, with the board number in geo_id."""
    raw = gpd.read_file(path)
    raw = raw[raw["board_num"].notna()]
    raw = raw.assign(geo_id=raw["board_num"].astype(int))
    return (
        raw[["geo_id", "geometry"]]
        .dissolve(by="geo_id", as_index=False)
        .set_crs("EPSG:4326", allow_override=True)
    )


def assign_islands(points: pd.DataFrame) -> pd.Series:
    """Island id of each block from its internal point, blank when the point lies in no island box or in two."""
    hits = pd.DataFrame(
        {
            ISLAND_IDS[name]: points["lon"].between(w, e).to_numpy() & points["lat"].between(s, n).to_numpy()
            for name, (_, (w, s, e, n)) in ISLANDS.items()
        }
    )
    one = (hits.sum(axis=1) == 1).to_numpy()
    island = hits.idxmax(axis=1).where(one)
    return pd.Series(island.to_numpy(), index=points["block"].to_numpy(), name="island")


def _member(points: pd.DataFrame, polys: gpd.GeoDataFrame) -> pd.Series:
    if points.empty:
        return pd.Series(dtype=object)
    m = spatial_membership(points, polys)
    return pd.Series(m["geo_id"].to_numpy(), index=m["block"].to_numpy())


def _main_island(blocks: pd.DataFrame, column: str) -> pd.Series:
    g = blocks[blocks[column].notna() & blocks["island"].notna()].groupby([column, "island"])["pop2020"].sum()
    return (
        g[g > 0]
        .reset_index()
        .sort_values(["pop2020", "island"], ascending=[False, True])
        .groupby(column)["island"]
        .first()
    )


def _overlap(blocks: pd.DataFrame, column: str, pop: pd.Series) -> pd.DataFrame:
    parts = []
    for geo_type in GEO_TYPES:
        g = blocks[blocks[column].isin(pop.index) & blocks[geo_type].notna()]
        s = g.groupby([column, geo_type])["pop2020"].sum()
        s = s[s > 0].rename("pop2020").reset_index().rename(columns={column: "area_id", geo_type: "geo_id"})
        s.insert(1, "geo_type", geo_type)
        parts.append(s)
    out = pd.concat(parts, ignore_index=True)
    out["share"] = out["pop2020"] / out["area_id"].map(pop)
    return out


def _alias_rows(names: Names, area: pd.DataFrame, places: pd.DataFrame) -> tuple[pd.DataFrame, list[dict]]:
    """One row per alias target that names exactly one area or place, and the targets that do not."""
    by_kind = {kind: area[area["kind"] == kind] for kind in KINDS} | {"place": places}
    known = {
        "island": set(ISLAND_NAMES.values()),
        "neighborhood": set(names.neighborhoods.values()),
        "division": set(names.divisions.values()),
        "place": set(places["name"]),
    }
    rows, unmatched = [], []
    for alias, spec in names.aliases.items():
        plain = search_plain(alias)
        for kind, targets in spec.items():
            pool = by_kind.get(kind, area.iloc[0:0])
            for target in targets:
                m = _QUALIFIED.match(target)
                name, island = (m.group(1), m.group(2)) if m else (target, None)
                hit = pool[(pool["name"] == name) & ((pool["island"] == island) if island else True)]
                if len(hit) == 1:
                    rows.append((plain, alias, "place" if kind == "place" else "area", hit.iloc[0]["id"]))
                else:
                    known_name = name in known.get(kind, set())
                    unmatched.append(
                        {
                            "alias": alias,
                            "kind": kind,
                            "target": target,
                            "matches": len(hit),
                            "known": known_name,
                        }
                    )
    alias = pd.DataFrame(rows, columns=["plain", "name", "target_type", "target_id"]).drop_duplicates()
    return alias, unmatched


def build_areas(
    con,
    points: pd.DataFrame,
    block_place: pd.Series,
    divisions: gpd.GeoDataFrame,
    boards: gpd.GeoDataFrame,
    names: Names,
) -> Areas:
    """Area, overlap and alias frames for the blocks and places already in the store."""
    blocks = con.execute("SELECT block, county, zcta, house, senate, pop2020 FROM blocks").df()
    points = points[points["block"].isin(blocks["block"])]
    county = points["block"].map(blocks.set_index("block")["county"])
    blocks["island"] = blocks["block"].map(assign_islands(points))
    blocks["place"] = blocks["block"].map(block_place)
    nb = _member(points[(county == HONOLULU).to_numpy()], boards)
    blocks["neighborhood"] = blocks["block"].map(nb).map(lambda b: f"nb{int(b):02}", na_action="ignore")
    blocks["division"] = blocks["block"].map(
        _member(points[county.isin(DIVISION_COUNTIES).to_numpy()], divisions)
    )
    census = dict(zip(divisions["geo_id"], divisions["census_name"], strict=True))
    island_name = {ISLAND_IDS[n]: ISLAND_NAMES[n] for n in ISLANDS}
    namers = {
        "island": island_name.get,
        "neighborhood": lambda i: names.neighborhoods.get(int(i[2:]), f"Neighborhood board {int(i[2:])}"),
        "division": lambda i: names.divisions.get(i, display_name(census.get(i, i))),
    }
    areas, overlaps = [], []
    for kind in KINDS:
        pop = blocks.groupby(kind)["pop2020"].sum()
        pop = pop[pop > 0]
        main = None if kind == "island" else _main_island(blocks, kind)
        ids = pop.index.tolist()
        name = [namers[kind](i) for i in ids]
        areas.append(
            pd.DataFrame(
                {
                    "area_id": ids,
                    "kind": kind,
                    "name": name,
                    "plain": [search_plain(n) for n in name],
                    "island": [island_name[i if main is None else main[i]] for i in ids],
                    "pop2020": pop.to_numpy(dtype="int64"),
                }
            )
        )
        overlaps.append(_overlap(blocks, kind, pop))
    area = pd.concat(areas, ignore_index=True)
    overlap = pd.concat(overlaps, ignore_index=True)
    place_island = _main_island(blocks, "place").map(island_name)
    places = con.execute("SELECT place_id AS id, name FROM place").df()
    places["island"] = places["id"].map(place_island)
    alias, unmatched = _alias_rows(names, area.rename(columns={"area_id": "id"}), places)
    return Areas(area, overlap, alias, blocks, unmatched)
