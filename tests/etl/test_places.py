# Tests for CDC PLACES modeled need measures by tract and their weighted means in districts.
from pathlib import Path

import pandas as pd
import pytest

from klear.etl.geographies import small_geographies
from klear.etl.places import (
    PLACES_MEASURES,
    SOURCE_ID,
    parse_places,
    places_facts,
    places_indicator_rows,
)
from klear.store.db import add_facts, add_geographies, add_indicators, add_source
from klear.validation.places import check_places

FIXTURES = Path(__file__).parent.parent / "fixtures"
FOOD = "places_food_insecurity"
TRACT_A, TRACT_B = "15003008001", "15009030307"


@pytest.fixture
def places_sample():
    return parse_places(FIXTURES / "places_sample.csv")


@pytest.fixture
def places_blocks(blocks_fixture):
    # tract 15001021200 becomes the PLACES tract 15009030307
    return blocks_fixture.assign(tract=blocks_fixture.tract.replace({"15001021200": TRACT_B}))


def test_tract_ranges_from_ci(places_sample, places_blocks):
    assert "STROKE" not in set(places_sample.measureid)
    facts = places_facts(places_sample, places_blocks)
    t = facts[(facts.geo_type == "tract") & (facts.indicator_id == FOOD)].set_index("geo_id")
    # copied exactly from the live FOODINSECU row
    assert t.value[TRACT_A] == 23.4 and t.value_low[TRACT_A] == 20.6 and t.value_high[TRACT_A] == 26.3
    assert (t.tier == "M").all() and (t.moe_status == "range").all() and (t.source_id == SOURCE_ID).all()
    assert set(facts.indicator_id) == set(PLACES_MEASURES.values())


def test_district_is_weighted_mean(places_sample, places_blocks):
    facts = places_facts(places_sample, places_blocks)
    s = places_sample[places_sample.measureid == "FOODINSECU"].set_index("src_id")
    # house shares from the blocks: tract A is 60/40 in 001/002, tract B is 5/65 of 70
    share = {
        ("001", TRACT_A): 0.6,
        ("002", TRACT_A): 0.4,
        ("001", TRACT_B): 5 / 70,
        ("002", TRACT_B): 65 / 70,
    }
    h = facts[(facts.geo_type == "house") & (facts.indicator_id == FOOD)].set_index("geo_id")
    for d in ("001", "002"):
        w = {t: s.weight[t] * share[(d, t)] for t in (TRACT_A, TRACT_B)}
        for col, out in (("value", "value"), ("value_low", "value_low"), ("value_high", "value_high")):
            want = sum(w[t] * s[col][t] for t in w) / sum(w.values())
            assert h[out][d] == pytest.approx(want)
    assert (h.tier == "M").all() and (h.moe_status == "range").all()


def test_footnoted_blank_value_suppressed(places_sample, places_blocks):
    # no blank data_value exists in the live file, so one row is blanked here
    df = places_sample.copy()
    hit = (df.src_id == TRACT_A) & (df.measureid == "FOODINSECU")
    df.loc[hit, ["value", "value_low", "value_high"]] = float("nan")
    facts = places_facts(df, places_blocks)
    t = facts[(facts.geo_type == "tract") & (facts.indicator_id == FOOD)].set_index("geo_id")
    assert pd.isna(t.value[TRACT_A]) and t.value_status[TRACT_A] == "suppressed"
    assert pd.isna(t.value_low[TRACT_A]) and pd.isna(t.value_high[TRACT_A])
    assert t.value_status[TRACT_B] == "ok"
    assert facts.attrs["excluded"][FOOD] == [TRACT_A]
    h = facts[(facts.geo_type == "house") & (facts.indicator_id == FOOD)].set_index("geo_id")
    b = df[(df.src_id == TRACT_B) & (df.measureid == "FOODINSECU")].iloc[0]
    assert h.value["001"] == pytest.approx(b.value) and h.value["002"] == pytest.approx(b.value)
    # the suppressed tract keeps its weight in the range, at 0 for the low and 100 for the high
    assert (h.value_low < b.value_low).all() and (h.value_high > b.value_high).all()
    assert ((h.value_low <= h.value) & (h.value <= h.value_high)).all()
    res = check_places(df, facts)
    assert res.passed and any(TRACT_A in n and "food" in n.lower() for n in res.notes)


def test_district_without_weight_has_no_fact_and_is_noted(places_sample, places_blocks):
    df = places_sample.copy()
    hit = (df.src_id == TRACT_A) & (df.measureid == "FOODINSECU")
    df.loc[hit, ["value", "value_low", "value_high"]] = float("nan")
    # only tract A has people, so its blank value leaves every district with zero weight
    facts = places_facts(df, places_blocks[places_blocks.tract == TRACT_A])
    d = facts[(facts.geo_type != "tract") & (facts.indicator_id == FOOD)]
    assert d.empty
    assert facts.attrs["no_fact"][FOOD]["senate"] == ["01", "02"]
    assert any("senate" in n and "01" in n for n in facts.attrs["notes"])
    assert any("senate" in n and "01" in n for n in check_places(df, facts).notes)


def _with_line(tmp_path, edit):
    lines = (FIXTURES / "places_sample.csv").read_text(encoding="utf-8").splitlines()
    src = next(ln for ln in lines if '"15003008001"' in ln and '"FOODINSECU"' in ln)
    path = tmp_path / "places.csv"
    path.write_text("\n".join([*lines, *edit(src)]) + "\n", encoding="utf-8")
    return parse_places(path)


def test_non_crude_rows_dropped(tmp_path, places_blocks):
    # hand-made row: the FOODINSECU row relabelled age-adjusted with another value
    df = _with_line(tmp_path, lambda ln: [ln.replace('"CrdPrv"', '"AgeAdjPrv"').replace('"23.4"', '"30.0"')])
    assert len(df) == len(parse_places(FIXTURES / "places_sample.csv"))
    assert not df.duplicated(["src_id", "measureid"]).any()
    assert check_places(df, places_facts(df, places_blocks)).passed


def test_footnoted_numeric_value_suppressed(tmp_path):
    # hand-made: a footnote symbol on a row that still carries a number
    df = _with_line(tmp_path, lambda ln: [ln.replace('"23.4",,,', '"23.4","*","Suppressed",')])
    row = df[(df.src_id == TRACT_A) & (df.measureid == "FOODINSECU")].iloc[-1]
    assert pd.isna(row.value) and pd.isna(row.value_low) and pd.isna(row.value_high)


def test_check_fails_on_duplicate_pair(tmp_path, places_blocks):
    df = _with_line(tmp_path, lambda ln: [ln])
    assert not check_places(df, places_facts(df, places_blocks)).passed


def test_tract_without_house_listed(places_sample, places_blocks):
    extra = pd.DataFrame(
        [("150039999999000", "15003", "15003009999", None, None, "03", 10)], columns=places_blocks.columns
    )
    blocks = pd.concat([places_blocks, extra], ignore_index=True)
    facts = places_facts(places_sample, blocks)
    assert facts.attrs["uncovered"] == ["15003009999"]
    res = check_places(places_sample, facts)
    assert any("15003009999" in n for n in res.notes)


def test_tract_missing_from_places_listed(places_sample, places_blocks):
    df = places_sample[places_sample.src_id != TRACT_B]
    facts = places_facts(df, places_blocks)
    assert facts.attrs["uncovered"] == [TRACT_B]
    res = check_places(df, facts)
    assert any(TRACT_B in n for n in res.notes)


def test_check_fails_on_value_outside_range(places_sample, places_blocks):
    facts = places_facts(places_sample, places_blocks)
    bad = facts.copy()
    bad.attrs = facts.attrs
    i = bad.index[(bad.geo_type == "house")][0]
    bad.loc[i, "value_high"] = bad.loc[i, "value"] - 1
    assert not check_places(places_sample, bad).passed
    assert check_places(places_sample, facts).passed


def test_check_fails_without_attrs(places_sample):
    assert not check_places(places_sample, pd.DataFrame()).passed


def test_labels_from_measure_column(places_sample):
    rows = places_indicator_rows(places_sample).set_index("indicator_id")
    assert rows.label[FOOD] == "Food insecurity in the past 12 months among adults"
    assert (rows.unit == "percent").all() and not rows.additive.any()
    assert len(rows) == 6


def test_facts_load_into_store(places_sample, places_blocks, store):
    add_source(store, SOURCE_ID, "CDC PLACES", "CDC", "https://data.cdc.gov", "PLACES 2025", "public domain")
    add_indicators(store, places_indicator_rows(places_sample))
    add_geographies(store, small_geographies(places_blocks))
    add_facts(store, places_facts(places_sample, places_blocks))
    n = store.execute("SELECT count(*) FROM fact WHERE tier = 'M'").fetchone()[0]
    assert n > 0


@pytest.mark.network
def test_live_places_counts():
    from klear.etl.places import load_places

    df = load_places()
    counts = df.groupby("measureid").src_id.nunique().to_dict()
    assert set(counts) == set(PLACES_MEASURES)
    assert all(n == 426 for n in counts.values())
