# Tests for the place gazetteer tables built from block assignments.
import zipfile

import pandas as pd
import pytest

from klear.etl.gazetteer_names import build_overrides
from klear.etl.places_gazetteer import build_places, load_overrides, plain_name, read_baf
from klear.store.db import add_blocks, add_places, open_store

GAZ = (
    "USPS\tGEOID\tANSICODE\tNAME\tLSAD\tFUNCSTAT\tALAND\tAWATER\tINTPTLAT\tINTPTLONG   \n"
    "HI\t1528250\t1\tKaneohe CDP\t57\tS\t1\t1\t21.4\t-157.8   \n"
    "HI\t1514650\t2\tHilo CDP\t57\tS\t1\t1\t19.7\t-155.0   \n"
    "HI\t1599999\t3\tEmpty CDP\t57\tS\t1\t1\t19.7\t-155.0   \n"
)
BAF = (
    "BLOCKID|PLACEFP\n"
    "150030001001000|28250\n150030001001001|28250\n150030001001002|28250\n"
    "150010001001000|14650\n150010001001001|\n"
)
ROWS = [
    ("150030001001000", "15003", "96744", "022", "012", 100),
    ("150030001001001", "15003", "96744", "023", "012", 50),
    ("150030001001002", "15003", "96744", "023", "013", 25),
    ("150010001001000", "15001", "96720", "001", "001", 70),
    ("150010001001001", "15001", "96720", "001", "001", 9),
]


@pytest.fixture
def paths(tmp_path):
    baf = tmp_path / "baf.zip"
    with zipfile.ZipFile(baf, "w") as z:
        z.writestr("BlockAssign_ST15_HI_INCPLACE_CDP.txt", BAF)
    names = tmp_path / "gaz.txt"
    names.write_text(GAZ, encoding="utf-8")
    return baf, names


@pytest.fixture
def store(tmp_path, paths):
    con = open_store(tmp_path / "k.duckdb")
    df = pd.DataFrame(ROWS, columns=["block", "county", "zcta", "house", "senate", "pop2020"])
    df["tract"] = df["block"].str[:11]
    add_blocks(con, df)
    overrides = {"1528250": "Kāneʻohe"}
    place, overlap = build_places(con, *paths, overrides=overrides)
    add_places(con, place, overlap)
    return con


def test_plain_name():
    assert plain_name("Kāneʻohe") == plain_name("kaneohe") == plain_name("Kane'ohe") == "kaneohe"
    assert plain_name("Haiku-Pauwela") == plain_name("Haiku  Pauwela") == "haiku pauwela"
    assert plain_name("Kane‘ohe") == "kaneohe"


def test_overlap_shares_sum_to_one_per_type(store):
    rows = store.execute("SELECT place_id, geo_type, sum(share) FROM place_overlap GROUP BY ALL").fetchall()
    assert len(rows) == 8
    assert all(abs(s - 1) < 1e-9 for *_, s in rows)


def test_names_keep_okina_and_kahako(store):
    assert store.execute("SELECT name FROM place WHERE plain='kaneohe'").fetchone()[0] == "Kāneʻohe"
    assert store.execute("SELECT name FROM place WHERE plain='hilo'").fetchone()[0] == "Hilo"


def test_values(store):
    assert store.execute("SELECT pop2020 FROM place WHERE place_id='1528250'").fetchone()[0] == 175
    got = store.execute(
        "SELECT pop2020, share FROM place_overlap WHERE place_id='1528250' AND geo_type='house' AND geo_id='023'"
    ).fetchone()
    assert got == (75, 75 / 175)


def test_zero_population_places_dropped_and_unmapped_blocks_ignored(store):
    assert store.execute("SELECT count(*) FROM place").fetchone()[0] == 2
    assert store.execute("SELECT pop2020 FROM place WHERE plain='hilo'").fetchone()[0] == 70


def test_unnamed_place_fails(tmp_path, paths):
    con = open_store(tmp_path / "k.duckdb")
    df = pd.DataFrame(ROWS, columns=["block", "county", "zcta", "house", "senate", "pop2020"])
    df["tract"] = df["block"].str[:11]
    add_blocks(con, df)
    baf, names = paths
    names.write_text(GAZ.replace("1514650", "1514651"), encoding="utf-8")
    with pytest.raises(ValueError, match="without a name"):
        build_places(con, baf, names, overrides={})


def test_read_baf_skips_blank(paths):
    s = read_baf(paths[0])
    assert len(s) == 4 and s["150010001001000"] == "1514650"


def test_build_overrides_matches_by_plain_name_and_skips_ambiguous():
    census = {"1": "Kaneohe", "2": "Hilo", "3": "Kailua", "4": "Lanai City"}
    out = build_overrides(census, ["Kāneʻohe", "Hilo", "Lānaʻi City"])
    assert out == {"1": "Kāneʻohe", "4": "Lānaʻi City"}
    assert build_overrides(census, ["Lānaʻi City", "Lanaʻi City"]) == {}


def test_checked_in_overrides():
    o = load_overrides()
    assert o["1528250"] == "Kāneʻohe"
    assert o["1574450"] == "Waiʻanae"
    assert not any(c in v for v in o.values() for c in "'‘’")
