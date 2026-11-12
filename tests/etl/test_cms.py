# Tests for CMS 2026 marketplace enrollment parsing, residual pools and checks.
from pathlib import Path

import pandas as pd
import pytest

from klear.etl import cms
from klear.etl.bounded import NotAdditiveError
from klear.etl.cms import (
    CMS_INDICATORS,
    SOURCE_ID,
    cms_facts,
    cms_indicator_rows,
    parse_cms_county,
    parse_cms_zip,
)
from klear.etl.geographies import small_geographies
from klear.store.db import add_facts, add_geographies, add_indicators, add_source
from klear.validation.cms import check_cms, check_cms_districts

FIXTURES = Path(__file__).parent.parent / "fixtures"
IND = "aca_consumers"


@pytest.fixture
def cms_zip_sample():
    return parse_cms_zip(FIXTURES / "cms_zip_sample.csv")


@pytest.fixture
def cms_county_sample():
    return parse_cms_county(FIXTURES / "cms_county_sample.csv")


@pytest.fixture
def zip_county(cms_blocks):
    geos = small_geographies(cms_blocks)
    return geos[geos.geo_type == "zcta"].set_index("geo_id").parent_county


def _row(df, zcta, ind=IND):
    return df[(df.zcta == zcta) & (df.indicator_id == ind)].iloc[0]


def test_star_is_suppressed_not_zero(cms_zip_sample):
    r = _row(cms_zip_sample, "96718")
    assert pd.isna(r.value) and r.value_status == "suppressed"
    assert _row(cms_zip_sample, "96701").value == 369
    assert _row(cms_zip_sample, "96701").value_status == "ok"
    assert _row(cms_zip_sample, "96701", "aca_aptc_consumers").value == 259


def test_county_thousands_commas(cms_county_sample):
    c = cms_county_sample.set_index(["county", "indicator_id"]).value
    assert c[("15001", IND)] == 4289
    assert c[("15003", IND)] == 13693
    assert set(cms_county_sample.indicator_id) == set(CMS_INDICATORS)


def test_residual_pool_per_county(cms_zip_sample, cms_county_sample, cms_blocks):
    facts = cms_facts(cms_zip_sample, cms_county_sample, cms_blocks)
    residual = 4289 - 591 - 237
    apt = 3104 - 457 - 174
    assert apt > 0
    for chamber in ("house", "senate"):
        sub = facts[(facts.geo_type == chamber) & (facts.indicator_id == IND)]
        assert sub.value.sum() == pytest.approx(369 + 237 + 591 + 561 + residual)
        a = facts[(facts.geo_type == chamber) & (facts.indicator_id == "aca_aptc_consumers")]
        assert a.value.sum() == pytest.approx(259 + 174 + 457 + 421 + apt)
        assert (sub.tier == "A").all() and (sub.moe_status == "range").all()
        assert (sub.value_low <= sub.value).all() and (sub.value <= sub.value_high).all()
    house = facts[(facts.geo_type == "house") & (facts.indicator_id == IND)].set_index("geo_id")
    # the whole residual counts once in the high bound of each district it could touch
    assert house.value_high["002"] >= 591 + residual


def test_suppressed_zcta_fact_is_null(cms_zip_sample, cms_county_sample, cms_blocks):
    facts = cms_facts(cms_zip_sample, cms_county_sample, cms_blocks)
    z = facts[(facts.geo_type == "zcta") & (facts.indicator_id == IND)].set_index("geo_id")
    assert pd.isna(z.value["96718"]) and z.value_status["96718"] == "suppressed"
    assert z.value["96720"] == 591 and (z.tier == "D").all()
    c = facts[(facts.geo_type == "county") & (facts.indicator_id == IND)]
    assert c.set_index("geo_id").value["15001"] == 4289 and (c.tier == "D").all()


def test_state_row_sums_the_counties(cms_zip_sample, cms_county_sample, cms_blocks):
    facts = cms_facts(cms_zip_sample, cms_county_sample, cms_blocks)
    st = facts[facts.geo_type == "state"].set_index("indicator_id")
    want = cms_county_sample.groupby("indicator_id").value.sum()
    assert st.geo_id.unique().tolist() == ["15"] and (st.tier == "S").all()
    assert st.value[IND] == want[IND] == 23380
    assert (st.moe_status == "not_applicable").all() and st.value_low.isna().all()


def test_zip_without_zcta_noted(cms_zip_sample, cms_county_sample, cms_blocks):
    facts = cms_facts(cms_zip_sample, cms_county_sample, cms_blocks)
    assert "96823" not in set(facts.geo_id)
    assert any("96823" in n for n in facts.attrs["notes"])


def test_residual_without_candidate_is_noted(cms_zip_sample, cms_county_sample, cms_blocks, zip_county):
    facts = cms_facts(cms_zip_sample, cms_county_sample, cms_blocks)
    assert any("unplaced" in n and "15003" in n for n in facts.attrs["notes"])
    res = check_cms(cms_zip_sample, cms_county_sample, zip_county, notes=facts.attrs["notes"])
    assert any("unplaced" in n for n in res.notes)


def test_check_passes_and_reports(cms_zip_sample, cms_county_sample, zip_county):
    res = check_cms(cms_zip_sample, cms_county_sample, zip_county)
    assert res.name == "cms_county" and res.gate and res.passed
    assert any("15001" in n and "residual" in n for n in res.notes)
    assert any("statewide published share" in n for n in res.notes)


def test_negative_residual_fails(cms_zip_sample, cms_county_sample, zip_county):
    county = cms_county_sample.copy()
    county.loc[(county.county == "15001") & (county.indicator_id == IND), "value"] = 100
    res = check_cms(cms_zip_sample, county, zip_county)
    assert not res.passed and res.n_mismatch >= 1
    assert any(d["county"] == "15001" for d in res.details)


def test_starred_county_is_suppressed_and_fails(cms_zip_sample, cms_county_sample, cms_blocks, zip_county):
    county = cms_county_sample.copy()
    m = (county.county == "15001") & (county.indicator_id == IND)
    county["value_status"] = "ok"
    county.loc[m, ["value", "value_status"]] = [float("nan"), "suppressed"]
    facts = cms_facts(cms_zip_sample, county, cms_blocks)
    c = facts[(facts.geo_type == "county") & (facts.geo_id == "15001") & (facts.indicator_id == IND)]
    assert c.value.isna().all() and (c.value_status == "suppressed").all()
    assert any("15001" in n and "suppressed" in n for n in facts.attrs["notes"])
    assert not facts.loc[facts.geo_type == "house", "value"].isna().any()
    st = facts[(facts.geo_type == "state") & (facts.indicator_id == IND)].iloc[0]
    assert pd.isna(st.value) and st.value_status == "suppressed"
    res = check_cms(cms_zip_sample, county, zip_county)
    assert not res.passed and any(d["county"] == "15001" for d in res.details)


def _zdf(rows):
    return pd.DataFrame(rows, columns=["zcta", "indicator_id", "value", "value_status"])


def _cdf(rows):
    return pd.DataFrame(rows, columns=["county", "indicator_id", "value", "value_status"])


def test_suppressed_candidates_are_per_indicator(cms_blocks):
    # 96701 and 96718 are equally populated and sit in separate districts
    b = cms_blocks[cms_blocks.zcta.isin(["96701", "96718"])].copy()
    b["county"] = "15001"
    b["pop2020"] = 10
    b["house"] = b.zcta.map({"96701": "001", "96718": "002"})
    b["senate"] = b.zcta.map({"96701": "01", "96718": "02"})
    z = _zdf(
        [
            ("96701", IND, 100.0, "ok"),
            ("96701", "aca_aptc_consumers", float("nan"), "suppressed"),
            ("96718", IND, float("nan"), "suppressed"),
            ("96718", "aca_aptc_consumers", 10.0, "ok"),
        ]
    )
    c = _cdf([("15001", IND, 110.0, "ok"), ("15001", "aca_aptc_consumers", 20.0, "ok")])
    facts = cms_facts(z, c, b)
    h = facts[(facts.geo_type == "house") & (facts.indicator_id == IND)].set_index("geo_id").value
    assert h["001"] == pytest.approx(100) and h["002"] == pytest.approx(10)
    a = facts[(facts.geo_type == "house") & (facts.indicator_id == "aca_aptc_consumers")]
    a = a.set_index("geo_id").value
    assert a["002"] == pytest.approx(10) and a["001"] == pytest.approx(10)


def test_district_totals_fail_when_residuals_unplaced(cms_zip_sample, cms_county_sample, cms_blocks):
    # 15003, 15007 and 15009 residuals have no suppressed ZCTA, so House consumers sum to 5,219
    facts = cms_facts(cms_zip_sample, cms_county_sample, cms_blocks)
    assert facts.attrs["placed"]["house"][IND] == pytest.approx(5219)
    lost = facts.attrs["unplaced"]["house"][IND]
    assert lost == {"15003 residual": 12763.0, "15007 residual": 1451.0, "15009 residual": 3947.0}
    res = check_cms_districts(facts, cms_county_sample)
    assert res.name == "cms_districts" and res.gate and not res.passed
    bad = {(d["check"], d["chamber"], d["indicator_id"]) for d in res.details}
    assert ("unplaced_share", "house", IND) in bad and ("unplaced_share", "senate", IND) in bad
    assert not any(d["check"] == "total" for d in res.details)
    assert any("house aca_consumers: districts 5,219" in n and "county totals 23,380" in n for n in res.notes)


def test_district_totals_pass_when_everything_placed(cms_blocks):
    b = cms_blocks[cms_blocks.zcta.isin(["96701", "96718"])].copy()
    b["county"] = "15001"
    b["pop2020"] = 10
    b["house"] = b.zcta.map({"96701": "001", "96718": "002"})
    b["senate"] = b.zcta.map({"96701": "01", "96718": "02"})
    z = _zdf([("96701", IND, 100.0, "ok"), ("96718", IND, float("nan"), "suppressed")])
    z = pd.concat([z, z.assign(indicator_id="aca_aptc_consumers", value=z.value / 2)], ignore_index=True)
    c = _cdf([("15001", IND, 110.0, "ok"), ("15001", "aca_aptc_consumers", 55.0, "ok")])
    facts = cms_facts(z, c, b)
    res = check_cms_districts(facts, c)
    assert res.passed, res.details
    assert res.n_compared == 4
    # a district value that does not match the placed record fails
    bad = facts.copy()
    bad.attrs = facts.attrs
    bad.loc[bad.index[(bad.geo_type == "house") & (bad.indicator_id == IND)][0], "value"] += 5
    assert {d["check"] for d in check_cms_districts(bad, c).details} == {"placed_record", "total"}


def test_district_check_without_records_fails(cms_county_sample):
    res = check_cms_districts(pd.DataFrame(), cms_county_sample)
    assert not res.passed and res.gate


def test_empty_inputs_fail(cms_zip_sample, cms_county_sample, zip_county):
    empty_c = cms_county_sample.iloc[0:0]
    r = check_cms(cms_zip_sample, empty_c, zip_county)
    assert not r.passed and any(d.get("input") == "county_df" for d in r.details)
    r = check_cms(cms_zip_sample.iloc[0:0], cms_county_sample, zip_county)
    assert not r.passed and any(d.get("input") == "zip_df" for d in r.details)


def test_missing_county_or_indicator_total_fails(cms_zip_sample, cms_county_sample, zip_county):
    c = cms_county_sample[cms_county_sample.county != "15003"]
    r = check_cms(cms_zip_sample, c, zip_county)
    assert not r.passed and any(d["check"] == "no_county_total" and d["county"] == "15003" for d in r.details)
    c = cms_county_sample[cms_county_sample.indicator_id != "aca_aptc_consumers"]
    r = check_cms(cms_zip_sample, c, zip_county)
    assert not r.passed and any(d.get("indicator_id") == "aca_aptc_consumers" for d in r.details)


def test_kalawao_without_rows_passes(cms_zip_sample, cms_county_sample, zip_county):
    zc = pd.concat([zip_county, pd.Series({"96742": "15005"})])
    r = check_cms(cms_zip_sample, cms_county_sample, zc)
    assert r.passed, r.details
    assert any("Kalawao County (15005)" in n for n in r.notes)


def test_other_county_without_rows_needs_every_total(cms_zip_sample, cms_county_sample, zip_county):
    # Kalawao with part of its totals is no longer exempt, and every other county needs all totals
    zc = pd.concat([zip_county, pd.Series({"96742": "15005"})])
    part = pd.DataFrame(
        [{"county": "15005", "indicator_id": "aca_consumers", "value": 5.0, "value_status": "ok"}]
    )
    r = check_cms(cms_zip_sample, pd.concat([cms_county_sample, part], ignore_index=True), zc)
    assert not r.passed
    assert [d for d in r.details if d["check"] == "no_county_total"] == [
        {"check": "no_county_total", "county": "15005", "indicator_id": "aca_aptc_consumers"}
    ]
    c = cms_county_sample[~((cms_county_sample.county == "15003") & (cms_county_sample.indicator_id != IND))]
    r = check_cms(cms_zip_sample, c, zip_county)
    assert not r.passed and any(
        d.get("county") == "15003" for d in r.details if d["check"] == "no_county_total"
    )


def test_null_value_with_ok_status_fails(cms_zip_sample, cms_county_sample, cms_blocks, zip_county):
    z = cms_zip_sample.copy()
    z.loc[(z.zcta == "96701") & (z.indicator_id == IND), "value"] = float("nan")
    r = check_cms(z, cms_county_sample, zip_county)
    assert not r.passed
    assert any(d.get("zcta") == "96701" and d["indicator_id"] == IND for d in r.details)
    with pytest.raises(ValueError, match="96701 aca_consumers"):
        cms_facts(z, cms_county_sample, cms_blocks)


def test_check_without_zip_county_map_fails(cms_zip_sample, cms_county_sample):
    res = check_cms(cms_zip_sample, cms_county_sample)
    assert not res.passed and res.gate


def test_facts_load_into_store(cms_zip_sample, cms_county_sample, cms_blocks, store):
    add_source(store, SOURCE_ID, "CMS OEP 2026", "CMS", "https://www.cms.gov", "2026", "public domain")
    add_indicators(store, cms_indicator_rows())
    geos = small_geographies(cms_blocks)
    extra = pd.DataFrame(
        [("county", c, f"County {c}", None) for c in ("15001", "15003", "15007", "15009")]
        + [("state", "15", "Hawaii", None)],
        columns=geos.columns,
    )
    add_geographies(store, pd.concat([geos, extra], ignore_index=True))
    add_facts(store, cms_facts(cms_zip_sample, cms_county_sample, cms_blocks))
    n = store.execute(
        "SELECT count(*) FROM fact WHERE indicator_id = 'aca_consumers' AND value_status = 'suppressed'"
    ).fetchone()[0]
    assert n == 1


@pytest.mark.network
def test_live_cms():
    from klear.etl.cms import COUNTY_URL, ZIP_URL
    from klear.etl.fetch import fetch

    z = parse_cms_zip(fetch(ZIP_URL, "cms/zip.zip"))
    c = parse_cms_county(fetch(COUNTY_URL, "cms/county.zip"))
    pub = z[(z.indicator_id == IND) & (z.value_status == "ok")]
    # the brief expected 19,907 published; the live file gives 23,313 with 19 suppressed ZIPs
    assert len(pub) == 78 and pub.value.sum() == 23_313
    assert c[c.indicator_id == IND].value.sum() == 23_380


def test_non_additive_indicator_raises(cms_zip_sample, cms_county_sample, cms_blocks, monkeypatch):
    rows = cms.cms_indicator_rows()
    ind = rows.indicator_id.iloc[0]
    rows.loc[0, "additive"] = False
    monkeypatch.setattr(cms, "cms_indicator_rows", lambda: rows)
    with pytest.raises(NotAdditiveError, match=ind):
        cms_facts(cms_zip_sample, cms_county_sample, cms_blocks)
