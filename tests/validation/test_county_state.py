# Tests for the county and state check: sums equal their parts, and every value has a source, tier and method.
import pandas as pd
import pytest

from klear.etl.geographies import small_geographies
from klear.etl.hud import SOURCE_ID as HUD_SOURCE
from klear.etl.hud import hcv_facts, hud_indicator_rows
from klear.etl.sites import SITE_SOURCES, site_facts, site_indicator_rows
from klear.store.db import add_facts, add_geographies, add_indicators, add_sites, add_source
from klear.store.methods import fact_method, place_note
from klear.validation.county_state import check_county_state

WHOLE = pd.DataFrame(
    [("state", "15", "Hawaii", None), ("county", "15001", "Hawaii County", None),
     ("county", "15003", "Honolulu County", None)],
    columns=["geo_type", "geo_id", "name", "parent_county"],
)  # fmt: skip


@pytest.fixture
def con(store, blocks_fixture):
    for sid in [HUD_SOURCE, *set(SITE_SOURCES.values())]:
        add_source(store, sid, sid, "pub", "https://example.org", "2026", "public domain")
    add_indicators(store, pd.concat([hud_indicator_rows(), site_indicator_rows()], ignore_index=True))
    add_geographies(store, pd.concat([small_geographies(blocks_fixture), WHOLE], ignore_index=True))
    rows = [
        {"OBJECTID": 1, "GEOID": "15003008001", "HCV_PUBLIC": 50.0},
        {"OBJECTID": 2, "GEOID": "15001021200", "HCV_PUBLIC": None},
    ]
    add_facts(store, hcv_facts(rows, blocks_fixture))
    sites = pd.DataFrame(
        {
            "site_id": ["public_housing_A", "lihtc_B", "health_center_C"],
            "kind": ["public_housing", "lihtc", "health_center"],
            "name": ["A", "B", "C"],
            "address": ["a", "b", "c"],
            "lat": [21.35, 21.35, 21.36],
            "lon": [-157.95, -157.85, -157.85],
            "house": ["001", "002", "002"],
            "senate": ["01", "01", "02"],
            "snapped_m": [0.0, 0.0, 0.0],
            "units": pd.array([10, 20, None], dtype="Int64"),
            "source_id": [SITE_SOURCES[k] for k in ("public_housing", "lihtc", "health_center")],
            "county": ["15003", "15003", "15001"],
        }
    )
    add_sites(store, sites)
    add_facts(store, site_facts(sites, {"county": ["15001", "15003"]}))
    return store


def test_consistent_store_passes(con):
    res = check_county_state(con)
    assert res.name == "county_state" and res.gate and res.passed, res.details
    assert any("each checked for a registered source, a tier and a method" in n for n in res.notes)
    assert any("3 voucher county and state totals" in n for n in res.notes)


def test_changed_county_sum_fails(con):
    con.execute("UPDATE fact SET value = 49 WHERE geo_type = 'county' AND geo_id = '15003' AND tier = 'S'")
    res = check_county_state(con)
    checks = {d["check"] for d in res.details}
    assert not res.passed and {"tract_sum", "county_sum"} <= checks


def test_state_high_must_cover_suppressed_tracts(con):
    con.execute("UPDATE fact SET value_high = 55 WHERE geo_type = 'state' AND indicator_id = 'hud_vouchers'")
    res = check_county_state(con)
    assert not res.passed and res.details[0]["suppressed_tracts"] == 1


def test_site_state_must_match_site_table(con):
    con.execute("UPDATE fact SET value = 2 WHERE geo_type = 'state' AND indicator_id = 'sites_lihtc'")
    res = check_county_state(con)
    bad = [d for d in res.details if d["check"] == "site_sum"]
    assert not res.passed and bad[0]["indicator"] == "sites_lihtc" and bad[0]["sites"] == 1


def test_value_without_method_fails(con, monkeypatch):
    monkeypatch.setattr("klear.validation.county_state.fact_method", lambda *a: "")
    res = check_county_state(con)
    assert not res.passed and {d["check"] for d in res.details} == {"no_method"}


def test_methods_name_derived_values():
    assert fact_method("D", "acs2024_5yr_profile", "county") == "published for this geography"
    assert "derived by KLEAR" in fact_method("M", "cdc_places_2025_county", "state")
    assert "95 percent confidence interval" in fact_method("M", "cdc_places_2025_county", "county")
    assert fact_method("S", "hud_hcv_tract", "county").startswith("sum of the published tract counts")
    assert (
        fact_method("S", "other", "state")
        == "sum of the published counts for the smaller areas inside this place"
    )
    assert fact_method("A", "cms_oep_2026", "house").startswith("apportioned estimate")
    assert place_note("cdc_places_2025_county", "state") == "average of the CDC county estimates"
    assert place_note("cdc_places_2025_county", "county") == place_note("acs", "state") == ""
