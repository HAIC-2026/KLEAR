import pandas as pd
import pytest

from klear.etl.crosswalk import NotACountError, apportion, build_crosswalk


def _blocks():
    rows = [
        ("b1", "15003", "T1", "96801", "001", "01", 70),
        ("b2", "15003", "T1", "96801", "002", "01", 30),
        ("b3", "15003", "T2", "96802", "002", "01", 50),
        ("b4", "15003", "T3", None, "002", "01", 0),
        ("b5", "15003", "T4", None, "003", "02", 10),
    ]
    return pd.DataFrame(rows, columns=["block", "county", "tract", "zcta", "house", "senate", "pop2020"])


def test_shares_sum_to_one_per_source_unit():
    x = build_crosswalk(_blocks(), "tract", "house")
    sums = x.groupby("src_id")["pop_share"].sum()
    assert sums.to_dict() == pytest.approx({"T1": 1.0, "T2": 1.0, "T4": 1.0})


def test_zero_population_unit_is_dropped_with_note():
    x = build_crosswalk(_blocks(), "tract", "house")
    assert "T3" not in set(x["src_id"])
    assert any("T3" in n for n in x.attrs["notes"])


def test_null_zcta_blocks_are_not_a_source_unit():
    x = build_crosswalk(_blocks(), "zcta", "house")
    assert set(x["src_id"]) == {"96801", "96802"}


def test_null_src_and_dst_drops_are_noted():
    b = _blocks()
    b.loc[len(b)] = ["b6", "15003", "T5", "96803", None, "02", 4]
    x = build_crosswalk(b, "zcta", "house")
    assert x.attrs["dropped_null_src"] == (2, 10)
    assert x.attrs["dropped_null_dst"] == (1, 4)
    notes = " ".join(x.attrs["notes"])
    assert "2 blocks with null zcta dropped from weights, population 10" in notes
    assert "1 blocks with null house dropped from weights, population 4" in notes


def test_split_tract():
    x = build_crosswalk(_blocks(), "tract", "house")
    v = pd.DataFrame({"src_id": ["T1"], "indicator_id": ["pop"], "value": [100.0]})
    out = apportion(v, x, {"pop": "count"}).set_index("dst_id")["value"]
    assert out["001"] == pytest.approx(70) and out["002"] == pytest.approx(30)


def test_apportion_conserves_total():
    x = build_crosswalk(_blocks(), "tract", "senate")
    v = pd.DataFrame({"src_id": ["T1", "T2", "T4"], "indicator_id": ["pop"] * 3, "value": [100.0, 41.0, 7.0]})
    out = apportion(v, x, {"pop": "count"})
    assert out["value"].sum() == pytest.approx(148.0, rel=1e-9)
    assert not out.attrs["notes"]


def test_apportion_notes_zero_pop_and_unmapped_value():
    x = build_crosswalk(_blocks(), "tract", "house")
    v = pd.DataFrame({"src_id": ["T1", "T3", "T9"], "indicator_id": ["pop"] * 3, "value": [10.0, 5.0, 2.0]})
    out = apportion(v, x, {"pop": "count"})
    assert out["value"].sum() == pytest.approx(10.0)
    notes = " ".join(out.attrs["notes"])
    assert "T3" in notes and "T9" in notes


def test_apportion_refuses_median():
    x = build_crosswalk(_blocks(), "tract", "house")
    v = pd.DataFrame({"src_id": ["T1"], "indicator_id": ["med_inc"], "value": [80000.0]})
    with pytest.raises(NotACountError):
        apportion(v, x, {"med_inc": "dollars"})


def test_bad_geo_rejected():
    with pytest.raises(ValueError):
        build_crosswalk(_blocks(), "block", "house")
