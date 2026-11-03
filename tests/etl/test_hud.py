# Tests for HUD housing choice vouchers by tract and their apportionment to districts.
import pandas as pd
import pytest

from klear.etl import hud
from klear.etl.bounded import NotAdditiveError
from klear.etl.geographies import small_geographies
from klear.etl.hud import HUD_INDICATORS, SOURCE_ID, hcv_facts, hud_indicator_rows
from klear.store.db import add_facts, add_geographies, add_indicators, add_source
from klear.validation.hud import check_hcv

IND = "hud_vouchers"


@pytest.fixture
def hcv_rows():
    return [
        {"OBJECTID": 1, "GEOID": "15003008001", "HCV_PUBLIC": 50.0},
        {"OBJECTID": 2, "GEOID": "15001021200", "HCV_PUBLIC": None},
        {"OBJECTID": 3, "GEOID": "15009999999", "HCV_PUBLIC": 40.0},
    ]


def test_null_vouchers_suppressed_and_widen_high(hcv_rows, blocks_fixture):
    facts = hcv_facts(hcv_rows, blocks_fixture)
    t = facts[facts.geo_type == "tract"].set_index("geo_id")
    assert pd.isna(t.value["15001021200"]) and t.value_status["15001021200"] == "suppressed"
    assert t.value["15003008001"] == 50 and (t.tier == "D").all()
    assert t.value_low.isna().all() and t.value_high.isna().all()
    h = facts[(facts.geo_type == "house") & (facts.indicator_id == IND)].set_index("geo_id")
    # tract 15003008001 is 60 percent in 001, 40 in 002; the suppressed tract touches both
    assert h.value["001"] == pytest.approx(30) and h.value["002"] == pytest.approx(20)
    assert h.value_low["001"] == 0 and h.value_low["002"] == 0
    assert h.value_high["001"] == 50 + 10 and h.value_high["002"] == 50 + 10
    assert (h.tier == "A").all() and (h.moe_status == "range").all()


def test_unknown_tract_noted_and_dropped(hcv_rows, blocks_fixture):
    facts = hcv_facts(hcv_rows, blocks_fixture)
    assert "15009999999" not in set(facts.geo_id)
    assert any("15009999999" in n for n in facts.attrs["notes"])


def test_unplaced_zero_population_tract_reconciles(blocks_fixture):
    extra = pd.DataFrame(
        [("150039900001000", "15003", "15003990000", None, "001", "01", 0)], columns=blocks_fixture.columns
    )
    blocks = pd.concat([blocks_fixture, extra], ignore_index=True)
    rows = [
        {"OBJECTID": 1, "GEOID": "15003008001", "HCV_PUBLIC": 50.0},
        {"OBJECTID": 2, "GEOID": "15003990000", "HCV_PUBLIC": 25.0},
    ]
    facts = hcv_facts(rows, blocks)
    t = facts[facts.geo_type == "tract"].set_index("geo_id")
    assert t.value["15003990000"] == 25
    assert facts.attrs["unplaced"]["house"] == {IND: {"15003990000": 25.0}}
    assert any("15003990000" in n and "25" in n for n in facts.attrs["notes"])
    h = facts[facts.geo_type == "house"]
    assert h.value.sum() == pytest.approx(50)
    res = check_hcv(facts)
    assert res.passed and res.gate and res.name == "hud_hcv"
    assert any("unplaced 25" in n for n in res.notes)
    bad = facts[~((facts.geo_type == "house") & (facts.geo_id == "001"))]
    bad.attrs = facts.attrs
    assert not check_hcv(bad).passed


def test_facts_load_into_store(hcv_rows, blocks_fixture, store):
    add_source(
        store,
        SOURCE_ID,
        "HUD HCV",
        "HUD",
        "https://www.huduser.gov",
        "Through December 2025",
        "public domain",
    )
    add_indicators(store, hud_indicator_rows())
    add_geographies(store, small_geographies(blocks_fixture))
    add_facts(store, hcv_facts(hcv_rows, blocks_fixture))
    n = store.execute("SELECT count(*) FROM fact WHERE value_status = 'suppressed'").fetchone()[0]
    assert n == 1
    assert set(HUD_INDICATORS) == {IND}


@pytest.mark.network
def test_live_hcv_rows():
    from klear.etl.hud import load_hcv_rows

    rows = load_hcv_rows()
    assert len(rows) == 442


def test_off_table_tract_is_published_and_unplaced(hcv_rows, blocks_fixture):
    facts = hcv_facts(hcv_rows, blocks_fixture)
    assert facts.attrs["published"] == {IND: 90.0}
    assert facts.attrs["unplaced"]["house"] == {IND: {"15009999999": 40.0}}
    res = check_hcv(facts)
    assert res.passed
    assert any("published tracts 90" in n for n in res.notes)
    facts.attrs["unplaced"] = {"house": {}, "senate": {}}
    assert not check_hcv(facts).passed


def test_non_additive_indicator_raises(hcv_rows, blocks_fixture, monkeypatch):
    rows = hud_indicator_rows().assign(additive=False)
    monkeypatch.setattr(hud, "hud_indicator_rows", lambda: rows)
    with pytest.raises(NotAdditiveError):
        hcv_facts(hcv_rows, blocks_fixture)
