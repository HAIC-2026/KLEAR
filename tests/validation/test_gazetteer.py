# Tests for the place gazetteer validation.
import pandas as pd

from klear.store.db import add_blocks, add_places, open_store
from klear.validation.gazetteer import check_gazetteer

NAMES = {"1": "Kāneʻohe", "2": "Waiʻanae", "3": "Hilo", "4": "Kailua"}
PLAIN = {"1": "kaneohe", "2": "waianae", "3": "hilo", "4": "kailua"}


def make(tmp_path, names=NAMES, shares=1.0):
    con = open_store(tmp_path / "k.duckdb")
    blocks = pd.DataFrame(
        {
            "block": [f"1500100010010{i}" for i in "1234"],
            "county": "15001",
            "tract": "15001000100",
            "zcta": "96720",
            "house": "001",
            "senate": "001",
            "pop2020": 10,
        }
    )
    add_blocks(con, blocks)
    place = pd.DataFrame(
        {
            "place_id": list(names),
            "name": list(names.values()),
            "plain": [PLAIN[k] for k in names],
            "pop2020": 10,
        }
    )
    overlap = pd.DataFrame(
        [(p, t, "x", 10, shares) for p in names for t in ("county", "house", "senate", "zcta")],
        columns=["place_id", "geo_type", "geo_id", "pop2020", "share"],
    )
    add_places(con, place, overlap)
    bp = pd.Series(list(names), index=blocks["block"].to_numpy())
    return con, bp


def test_passes(tmp_path):
    con, bp = make(tmp_path)
    r = check_gazetteer(con, bp, census={"1": "Kaneohe"}, overrides={"1": "Kāneʻohe"})
    assert r.passed and r.gate and r.n_compared == 4
    assert "2 keep an ASCII-only name" in r.notes[0]


def test_flags_problems(tmp_path):
    con, bp = make(tmp_path, names=NAMES | {"1": "Kaneohe"})
    con.execute("UPDATE place_overlap SET share = 0.5 WHERE place_id = '2' AND geo_type = 'house'")
    bp = bp.copy()
    bp.iloc[2] = "9"
    r = check_gazetteer(con, bp, census={"1": "Kaneohe"}, overrides={"1": "Kailua"})
    kinds = sorted(d["check"] for d in r.details)
    assert kinds == ["missing_place", "override_mismatch", "pop_mismatch", "pop_mismatch", "share_sum"]
    assert not r.passed


def test_matching_override_passes_and_mismatch_fails(tmp_path):
    con, bp = make(tmp_path)
    assert check_gazetteer(con, bp, census={"1": "Kaneohe"}, overrides={"1": "Kāneʻohe"}).passed
    assert not check_gazetteer(con, bp, census={"1": "Kaneohe"}, overrides={"1": "Kailua"}).passed


def test_place_without_any_overlap_rows_fails(tmp_path):
    con, bp = make(tmp_path)
    con.execute("DELETE FROM place_overlap WHERE place_id = '3'")
    r = check_gazetteer(con, bp, census={}, overrides={})
    missing = [d for d in r.details if d["check"] == "share_sum" and d["place_id"] == "3"]
    assert not r.passed and {d["geo_type"] for d in missing} == {"county", "house", "senate", "zcta"}


def test_missing_one_geo_type_fails_and_zcta_optional_without_zcta(tmp_path):
    con, bp = make(tmp_path)
    con.execute("DELETE FROM place_overlap WHERE place_id = '2' AND geo_type = 'senate'")
    r = check_gazetteer(con, bp, census={}, overrides={})
    assert [(d["place_id"], d["geo_type"]) for d in r.details] == [("2", "senate")]
    con.execute("UPDATE blocks SET zcta = NULL")
    con.execute("DELETE FROM place_overlap WHERE geo_type = 'zcta'")
    assert [d["geo_type"] for d in check_gazetteer(con, bp, census={}, overrides={}).details] == ["senate"]


def test_block_place_missing_from_store_fails(tmp_path):
    con, bp = make(tmp_path)
    bp = bp.copy()
    bp.iloc[0] = "9"
    r = check_gazetteer(con, bp, census={}, overrides={})
    assert {d["place_id"] for d in r.details if d["check"] == "pop_mismatch"} == {"1", "9"}
