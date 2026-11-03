# Tests for IRS SOI ZIP and county EITC data parsing, apportionment and checks.
from pathlib import Path

import pandas as pd
import pytest

from klear.etl import irs
from klear.etl.bounded import NotAdditiveError
from klear.etl.geographies import small_geographies
from klear.etl.irs import (
    IRS_INDICATORS,
    SOURCE_ID,
    irs_facts,
    irs_indicator_rows,
    parse_irs_county,
    parse_irs_zip,
)
from klear.store.db import add_facts, add_geographies, add_indicators, add_source
from klear.validation.irs import check_irs

FIXTURES = Path(__file__).parent.parent / "fixtures"


@pytest.fixture
def irs_zip_sample():
    return parse_irs_zip(FIXTURES / "irs_zip_sample.csv")


@pytest.fixture
def irs_county_sample():
    return parse_irs_county(FIXTURES / "irs_county_sample.csv")


def _val(df, zcta, ind):
    return df[(df.zcta == zcta) & (df.indicator_id == ind)].value.iloc[0]


def test_money_in_thousands(irs_zip_sample, irs_county_sample, irs_blocks):
    assert _val(irs_zip_sample, "00000", "irs_eitc_dollars") == 198_081_000
    facts = irs_facts(irs_zip_sample, irs_county_sample, irs_blocks)
    state = facts[(facts.geo_type == "state") & (facts.indicator_id == "irs_eitc_dollars")]
    assert state.value.tolist() == [198_081_000]
    assert IRS_INDICATORS["irs_eitc_dollars"]["unit"] == "dollars"


def test_99999_is_a_pool_not_a_zcta(irs_zip_sample, irs_county_sample, irs_blocks):
    facts = irs_facts(irs_zip_sample, irs_county_sample, irs_blocks)
    assert "99999" not in set(facts.geo_id)
    assert "00000" not in set(facts.geo_id)
    zips = irs_zip_sample[irs_zip_sample.indicator_id == "irs_returns"].set_index("zcta").value
    total = zips["96701"] + zips["96704"] + zips["99999"]
    for chamber in ("house", "senate"):
        sub = facts[(facts.geo_type == chamber) & (facts.indicator_id == "irs_returns")]
        assert sub.value.sum() == pytest.approx(total)
    house = facts[(facts.geo_type == "house") & (facts.indicator_id == "irs_returns")]
    assert (house.tier == "A").all()
    assert (house.moe_status == "range").all()
    assert (house.value_low <= house.value).all() and (house.value <= house.value_high).all()


def test_pool_spread_over_every_populated_zcta(irs_zip_sample, irs_county_sample, irs_blocks):
    facts = irs_facts(irs_zip_sample, irs_county_sample, irs_blocks)
    ind = "irs_returns"
    z = facts[(facts.geo_type == "zcta") & (facts.indicator_id == ind)]
    assert sorted(z.geo_id) == ["96701", "96704"]
    assert (z.tier == "D").all() and z.value_low.isna().all()
    zips = irs_zip_sample[irs_zip_sample.indicator_id == ind].set_index("zcta").value
    pop = irs_blocks.dropna(subset=["zcta"]).groupby("zcta").pop2020.sum()
    pop = pop[pop > 0]
    share = pop / pop.sum() * zips["99999"]
    # 99999 has no known location: every populated ZCTA, with or without an IRS row, gets its population share
    assert set(share.index) >= {"96701", "96704", "96720"}
    for chamber in ("house", "senate"):
        sub = facts[(facts.geo_type == chamber) & (facts.indicator_id == ind)]
        assert sub.value.sum() == pytest.approx(zips["96701"] + zips["96704"] + zips["99999"])
        # the high carries the district's pool share, not the whole pool
        assert (sub.value_high < sub.value_low + zips["96701"] + zips["96704"] + zips["99999"]).all()
        assert (sub.value_low <= sub.value).all() and (sub.value <= sub.value_high).all()
    house = facts[(facts.geo_type == "house") & (facts.indicator_id == ind)].set_index("geo_id")
    no_pool = irs_facts(irs_zip_sample[irs_zip_sample.zcta != "99999"], irs_county_sample, irs_blocks)
    base = no_pool[(no_pool.geo_type == "house") & (no_pool.indicator_id == ind)].set_index("geo_id")
    added = house.value - base.value.reindex(house.index, fill_value=0)
    assert added.sum() == pytest.approx(zips["99999"])
    assert (
        house.value_high - base.value_high.reindex(house.index, fill_value=0)
    ).to_numpy() == pytest.approx(added.to_numpy())
    assert any("99999" in n and "population" in n for n in facts.attrs["notes"])


def test_county_and_state_facts_are_tier_d(irs_zip_sample, irs_county_sample, irs_blocks):
    facts = irs_facts(irs_zip_sample, irs_county_sample, irs_blocks)
    c = facts[(facts.geo_type == "county") & (facts.indicator_id == "irs_returns")]
    assert c.set_index("geo_id").value["15001"] == 95800
    assert (c.tier == "D").all() and c.value_low.isna().all()
    s = facts[(facts.geo_type == "state") & (facts.indicator_id == "irs_returns")]
    assert s.geo_id.tolist() == ["15"] and s.value.tolist() == [681450]


def test_facts_load_into_store(irs_zip_sample, irs_county_sample, irs_blocks, store):
    add_source(
        store, SOURCE_ID, "IRS SOI ZIP", "IRS", "https://www.irs.gov", "Tax year 2023", "public domain"
    )
    add_indicators(store, irs_indicator_rows())
    geos = small_geographies(irs_blocks)
    extra = pd.DataFrame(
        [("state", "15", "Hawaiʻi", None)]
        + [("county", c, f"County {c}", None) for c in ("15001", "15003", "15007", "15009")],
        columns=geos.columns,
    )
    add_geographies(store, pd.concat([geos, extra], ignore_index=True))
    add_facts(store, irs_facts(irs_zip_sample, irs_county_sample, irs_blocks))
    n = store.execute("SELECT count(*) FROM fact WHERE indicator_id = 'irs_returns'").fetchone()[0]
    assert n > 0


def _zip_df(rows):
    return pd.DataFrame(rows, columns=["zcta", "indicator_id", "value"])


def test_state_check_tolerance():
    ok = _zip_df(
        [("00000", "irs_returns", 1000.0), ("96701", "irs_returns", 960.0), ("99999", "irs_returns", 41.0)]
    )
    res = {
        r.name: r
        for r in check_irs(ok, pd.DataFrame(columns=["county", "indicator_id", "value"]), pd.DataFrame())
    }
    assert res["irs_state"].gate and res["irs_state"].passed
    bad = _zip_df(
        [("00000", "irs_returns", 1000.0), ("96701", "irs_returns", 960.0), ("99999", "irs_returns", 30.0)]
    )
    r = {
        r.name: r
        for r in check_irs(bad, pd.DataFrame(columns=["county", "indicator_id", "value"]), pd.DataFrame())
    }
    r = r["irs_state"]
    assert not r.passed and r.n_mismatch == 1
    d = r.details[0]
    assert d["indicator_id"] == "irs_returns" and d["state"] == 1000.0 and d["zip_total"] == 990.0


def test_county_check_reports_gap(irs_zip_sample, irs_county_sample, irs_blocks):
    facts = irs_facts(irs_zip_sample, irs_county_sample, irs_blocks)
    geos = small_geographies(irs_blocks)
    zip_county = geos[geos.geo_type == "zcta"].set_index("geo_id").parent_county
    res = {r.name: r for r in check_irs(irs_zip_sample, irs_county_sample, facts, zip_county=zip_county)}
    c = res["irs_county"]
    assert c.gate and c.passed and c.n_compared == 4
    assert any("15003" in n and "%" in n for n in c.notes)


def test_county_file_with_only_state_row_fails(irs_zip_sample, irs_county_sample, irs_blocks):
    geos = small_geographies(irs_blocks)
    zip_county = geos[geos.geo_type == "zcta"].set_index("geo_id").parent_county
    only_state = irs_county_sample[irs_county_sample.county == "15000"]
    c = check_irs(irs_zip_sample, only_state, pd.DataFrame(), zip_county=zip_county)[1]
    assert not c.passed and c.gate and c.n_compared == 0
    assert {d["county"] for d in c.details} == {"15001", "15003", "15007", "15009"}
    no_kauai = irs_county_sample[irs_county_sample.county != "15007"]
    assert not check_irs(irs_zip_sample, no_kauai, pd.DataFrame(), zip_county=zip_county)[1].passed


def test_district_totals_gated(irs_zip_sample, irs_county_sample, irs_blocks):
    facts = irs_facts(irs_zip_sample, irs_county_sample, irs_blocks)
    assert set(facts.attrs["unplaced"]) == {"house", "senate"}
    # the sample's state row covers all of Hawaiʻi, so only the district comparison is asserted here
    state = check_irs(irs_zip_sample, irs_county_sample, facts, notes=["from facts"])[0]
    assert not [d for d in state.details if d["check"] == "district_total"]
    assert (
        any("house irs_returns: districts sum to" in n for n in state.notes) and "from facts" in state.notes
    )
    lost = facts.drop(facts[(facts.geo_type == "senate") & (facts.indicator_id == "irs_returns")].index[:1])
    lost.attrs = facts.attrs
    bad = check_irs(irs_zip_sample, irs_county_sample, lost)[0]
    assert not bad.passed and bad.gate
    assert {d["chamber"] for d in bad.details if d["check"] == "district_total"} == {"senate"}
    no_attrs = pd.DataFrame(facts)
    no_attrs.attrs = {}
    res = check_irs(irs_zip_sample, irs_county_sample, no_attrs)[0]
    assert any(d.get("problem") == "no unplaced record on the district facts" for d in res.details)
    assert res.n_compared == 3 and not res.passed


def test_county_check_without_input_is_not_a_pass(irs_zip_sample, irs_county_sample):
    c = check_irs(irs_zip_sample, irs_county_sample, pd.DataFrame())[1]
    assert not c.passed and c.n_compared == 0 and c.gate


def _small(rows):
    return _zip_df([(z, i, v) for z, v in rows for i in ("irs_returns",)])


def test_unplaced_pool_fails_district_gate(blocks_fixture, irs_county_sample):
    # no ZCTA has population, so the 99999 pool has no candidate and cannot be placed
    blocks = blocks_fixture.assign(pop2020=blocks_fixture.pop2020.where(blocks_fixture.zcta.isna(), 0))
    z = _small([("00000", 1000.0), ("96701", 600.0), ("96720", 300.0), ("99999", 100.0)])
    facts = irs_facts(z, irs_county_sample, blocks)
    state = check_irs(z, irs_county_sample, facts)[0]
    assert not state.passed
    bad = [d for d in state.details if d["check"] == "district_total"]
    assert {d["chamber"] for d in bad} == {"house", "senate"}
    # the ZIP rows are listed as unplaced, the pool is not, so the totals cannot add back
    assert all(d["districts"] == 0 and d["unplaced"] == 900 for d in bad)


def test_unplaced_zip_listed_and_capped(irs_blocks, irs_county_sample):
    # 96799 has no 2020 ZCTA: a small amount is listed and passes, a large one fails the 0.5% cap
    small = _small([("00000", 10000.0), ("96701", 9940.0), ("96799", 10.0), ("99999", 50.0)])
    facts = irs_facts(small, irs_county_sample, irs_blocks)
    assert facts.attrs["unplaced"]["house"] == {"irs_returns": {"96799": 10.0}}
    state = check_irs(small, irs_county_sample, facts)[0]
    assert state.passed, state.details
    assert any("house irs_returns" in n and "96799 10" in n for n in state.notes)
    big = _small([("00000", 10000.0), ("96701", 9900.0), ("96799", 60.0), ("99999", 40.0)])
    state = check_irs(big, irs_county_sample, irs_facts(big, irs_county_sample, irs_blocks))[0]
    assert {d["check"] for d in state.details} == {"unplaced_share"}


@pytest.mark.network
def test_live_irs():
    from klear.etl.fetch import fetch
    from klear.etl.irs import COUNTY_URL, ZIP_URL

    z = parse_irs_zip(fetch(ZIP_URL, "irs/23zpallnoagi.csv"))
    c = parse_irs_county(fetch(COUNTY_URL, "irs/23incyallnoagi.csv"))
    assert z.zcta.nunique() == 60
    assert _val(z, "00000", "irs_returns") == 681_450
    assert _val(z, "00000", "irs_eitc_returns") == 83_180
    res = {r.name: r for r in check_irs(z, c, pd.DataFrame())}
    assert res["irs_state"].passed, res["irs_state"].details


def test_non_additive_indicator_raises(irs_zip_sample, irs_county_sample, irs_blocks, monkeypatch):
    rows = irs.irs_indicator_rows()
    ind = rows.indicator_id.iloc[0]
    rows.loc[0, "additive"] = False
    monkeypatch.setattr(irs, "irs_indicator_rows", lambda: rows)
    with pytest.raises(NotAdditiveError, match=ind):
        irs_facts(irs_zip_sample, irs_county_sample, irs_blocks)
