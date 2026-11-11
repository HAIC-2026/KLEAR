# Tests for islands, neighborhood board areas, county divisions and other names built from block points.
import json

import geopandas as gpd
import pandas as pd
import pytest
from shapely.geometry import box

from klear.etl.areas import ISLAND_NAMES, Names, assign_islands, build_areas, load_names, read_boards
from klear.etl.places_gazetteer import plain_name, search_plain
from klear.store.db import add_areas, add_blocks, add_places, open_store
from klear.validation.areas import check_areas

# Two Oʻahu blocks in two boards, one Hawaiʻi Island block in a division, and an empty block on Oʻahu.
ROWS = [
    ("150030001001000", "15003", "96815", "024", "012", 100, -157.83, 21.28),
    ("150030001001001", "15003", "96822", "022", "011", 60, -157.81, 21.31),
    ("150030001001002", "15003", "96822", "022", "011", 0, -157.81, 21.32),
    ("150010001001000", "15001", "96740", "007", "003", 40, -155.99, 19.64),
]
BOARDS = gpd.GeoDataFrame(
    {"geo_id": [9, 7]},
    geometry=[box(-157.85, 21.26, -157.82, 21.30), box(-157.82, 21.30, -157.79, 21.34)],
    crs="EPSG:4326",
)
DIVISIONS = gpd.GeoDataFrame(
    {"geo_id": ["1500192610"], "census_name": ["North Kona"]},
    geometry=[box(-156.1, 19.4, -155.8, 19.9)],
    crs="EPSG:4326",
)
NAMES = Names(
    divisions={"1500192610": "North Kona"},
    neighborhoods={7: "Mānoa", 9: "Waikīkī"},
    aliases={
        "Kona": {"division": ["North Kona"]},
        "Kailua-Kona": {"place": ["Kailua (Hawaiʻi Island)"]},
        "Upcountry": {"division": ["Kula"]},
    },
)


@pytest.fixture
def store(tmp_path):
    con = open_store(tmp_path / "k.duckdb")
    df = pd.DataFrame(
        [r[:6] for r in ROWS], columns=["block", "county", "zcta", "house", "senate", "pop2020"]
    )
    df["tract"] = df["block"].str[:11]
    add_blocks(con, df)
    place = pd.DataFrame(
        [("1523000", "Kailua", "kailua", 40), ("1571550", "Urban Honolulu", "urban honolulu", 160)],
        columns=["place_id", "name", "plain", "pop2020"],
    )
    overlap = pd.DataFrame(
        [("1523000", "house", "007", 40, 1.0), ("1571550", "house", "024", 100, 0.625)],
        columns=["place_id", "geo_type", "geo_id", "pop2020", "share"],
    )
    add_places(con, place, overlap)
    return con


@pytest.fixture
def built(store):
    points = pd.DataFrame([(r[0], r[6], r[7]) for r in ROWS], columns=["block", "lon", "lat"])
    block_place = pd.Series(
        ["1571550", "1571550", "1523000"], index=["150030001001000", "150030001001001", "150010001001000"]
    )
    return build_areas(store, points, block_place, DIVISIONS, BOARDS, NAMES)


def test_assign_islands_by_box():
    points = pd.DataFrame(
        [("a", -157.8, 21.3), ("b", -157.0, 21.1), ("c", -155.5, 19.6), ("d", -150.0, 21.0)],
        columns=["block", "lon", "lat"],
    )
    got = assign_islands(points)
    assert got.loc[["a", "b", "c"]].tolist() == ["oahu", "molokai", "hawaii"]
    assert pd.isna(got["d"])


def test_areas_with_their_island_and_shares(built):
    area = built.area.set_index("area_id")
    assert area.loc["oahu", ["kind", "name", "island", "pop2020"]].tolist() == [
        "island",
        "Oʻahu",
        "Oʻahu",
        160,
    ]
    assert area.loc["hawaii", "name"] == "Hawaiʻi Island" and area.loc["hawaii", "plain"] == "hawaii island"
    assert area.loc["nb09", ["kind", "name", "plain", "island"]].tolist() == [
        "neighborhood", "Waikīkī", "waikiki", "Oʻahu"
    ]  # fmt: skip
    assert area.loc["1500192610", ["kind", "island"]].tolist() == ["division", "Hawaiʻi Island"]
    assert "nb07" in area.index and len(area) == 5  # empty blocks make no area
    ov = built.overlap.set_index(["area_id", "geo_type", "geo_id"])["share"]
    assert ov[("oahu", "house", "024")] == pytest.approx(100 / 160)
    assert ov[("oahu", "place", "1571550")] == 1.0 and ov[("hawaii", "place", "1523000")] == 1.0
    assert ov[("nb07", "zcta", "96822")] == 1.0


def test_aliases_point_to_one_area_or_place(built):
    rows = set(built.alias.itertuples(index=False, name=None))
    assert rows == {
        ("kona", "Kona", "area", "1500192610"),
        ("kailua kona", "Kailua-Kona", "place", "1523000"),
    }
    # Kula is not among this test's division names, so it is reported as a misspelling.
    assert built.unmatched == [
        {"alias": "Upcountry", "kind": "division", "target": "Kula", "matches": 0, "known": False}
    ]


def test_check_passes_and_reports_problems(built, store):
    census = {"1500192610": "North Kona"}
    result = check_areas(built, NAMES, census=census, boards={7, 9})
    assert not result.passed and [d["check"] for d in result.details] == ["alias_target"]
    built.unmatched.clear()
    assert check_areas(built, NAMES, census=census, boards={7, 9}).passed
    bad = check_areas(built, NAMES, census={"1500192610": "South Kona"}, boards={7, 9, 12})
    assert {d["check"] for d in bad.details} == {"division_name", "board_name"}
    built.blocks.loc[built.blocks["block"] == "150030001001000", "neighborhood"] = None
    assert {d["check"] for d in check_areas(built, NAMES, census, {7, 9}).details} == {"no_neighborhood"}


def test_add_areas_round_trip(built, store):
    add_areas(store, built.area, built.overlap, built.alias)
    assert store.execute("SELECT count(*) FROM area").fetchone()[0] == 5
    assert (
        store.execute("SELECT target_id FROM area_alias WHERE plain = 'kona'").fetchone()[0] == "1500192610"
    )
    with pytest.raises(ValueError, match="kind not in"):
        add_areas(store, built.area.assign(kind="town"), built.overlap.iloc[0:0], built.alias.iloc[0:0])


def test_read_boards_dissolves_subdistricts(tmp_path):
    feats = [
        {
            "type": "Feature",
            "properties": {"board_num": n},
            "geometry": box(x, 21.3, x + 0.01, 21.31).__geo_interface__,
        }
        for n, x in ((9, -157.83), (9, -157.82), (7, -157.80), (None, -157.7))
    ]
    path = tmp_path / "boards.geojson"
    path.write_text(json.dumps({"type": "FeatureCollection", "features": feats}), encoding="utf-8")
    boards = read_boards(path)
    assert boards["geo_id"].tolist() == [7, 9]
    assert boards.geometry.iloc[1].area == pytest.approx(0.02 * 0.01)


def test_names_file_is_consistent():
    names = load_names()
    assert len(names.divisions) == 36 and len(names.neighborhoods) == 35
    assert all(len(geo_id) == 10 and geo_id[2:5] in ("001", "007", "009") for geo_id in names.divisions)
    known = {
        "island": set(ISLAND_NAMES.values()),
        "division": set(names.divisions.values()),
        "neighborhood": set(names.neighborhoods.values()),
    }
    plains = [search_plain(alias) for alias in names.aliases]
    assert len(plains) == len(set(plains)) and "st louis heights" in plains
    for alias, spec in names.aliases.items():
        for kind, targets in spec.items():
            assert kind in (*known, "place"), alias
            if kind in known:
                assert set(targets) <= known[kind], alias
    assert names.divisions["1500191170"] == "Kaʻū" and plain_name("Kaʻū") == "kau"
    assert names.neighborhoods[9] == "Waikīkī"
