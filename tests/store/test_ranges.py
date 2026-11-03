# Tests for fact ranges, additive indicators and the site table.
from pathlib import Path

import duckdb
import pandas as pd
import pytest

from klear.etl import profile
from klear.store.db import (
    MOE_STATUSES,
    RANGE_MEANING,
    SITE_KINDS,
    VALUE_STATUSES,
    add_facts,
    add_indicators,
    add_sites,
)


@pytest.fixture
def con(store):
    add_indicators(
        store,
        pd.DataFrame(
            [
                {
                    "indicator_id": "eitc",
                    "label": "EITC returns",
                    "unit": "count",
                    "universe": None,
                    "acs_var": None,
                    "additive": True,
                },
                {
                    "indicator_id": "rate",
                    "label": "Rate",
                    "unit": "percent",
                    "universe": None,
                    "acs_var": None,
                    "additive": False,
                },
            ]
        ),
    )
    return store


def fact(**over):
    row = {
        "geo_type": "house", "geo_id": "001", "indicator_id": "eitc", "value": 110.0, "moe": None,
        "value_status": "ok", "moe_status": "range", "tier": "A", "source_id": "test_src", "table_id": None,
        "value_low": 100.0, "value_high": 140.0,
    }  # fmt: skip
    row.update(over)
    return pd.DataFrame([row])


def site(**over):
    row = {
        "site_id": "ph1", "kind": "public_housing", "name": "Kuhio Park", "address": None, "lat": 21.3,
        "lon": -157.8, "house": "001", "senate": "01", "snapped_m": 0.0, "units": 10, "source_id": "test_src",
    }  # fmt: skip
    row.update(over)
    return pd.DataFrame([row])


def test_range_required_for_apportioned(con):
    add_facts(con, fact())
    assert con.execute("SELECT value_low, value_high FROM fact").fetchall() == [(100.0, 140.0)]
    with pytest.raises(ValueError, match="range"):
        add_facts(con, fact(geo_id="002", value_low=None, value_high=None))
    with pytest.raises(ValueError, match="value_low <= value"):
        add_facts(con, fact(geo_id="002", value_low=120.0))


def test_range_forbidden_for_direct(con):
    with pytest.raises(ValueError, match="range"):
        add_facts(con, fact(tier="D", moe_status="not_applicable"))


def test_suppressed_apportioned_fact_has_no_range(con):
    add_facts(
        con,
        fact(
            value=None,
            value_status="suppressed",
            moe_status="not_applicable",
            value_low=None,
            value_high=None,
        ),
    )


def test_store_checks_ranges_too(con):
    cols = "geo_type, geo_id, indicator_id, value, value_status, moe_status, tier, source_id"
    with pytest.raises(duckdb.ConstraintException):
        con.execute(
            f"INSERT INTO fact ({cols}) VALUES ('house','001','eitc',110,'ok','not_applicable','A','test_src')"
        )
    with pytest.raises(duckdb.ConstraintException):
        con.execute(
            f"INSERT INTO fact ({cols}, value_low, value_high) VALUES ('house','001','eitc',110,'ok','range','D','test_src',100,140)"
        )


def test_ranged_tier_needs_range_status(con):
    with pytest.raises(ValueError, match="moe_status range"):
        add_facts(con, fact(moe_status="not_applicable"))
    cols = "geo_type, geo_id, indicator_id, value, value_status, moe_status, tier, source_id, value_low, value_high"
    with pytest.raises(duckdb.ConstraintException):
        con.execute(
            f"INSERT INTO fact ({cols}) VALUES ('house','001','eitc',110,'ok','not_applicable','M','test_src',100,140)"
        )


def test_range_status_phrase():
    assert MOE_STATUSES["range"] == "about X (range L to H)"


def test_indicator_additive_is_required(store):
    row = {"indicator_id": "x", "label": "x", "unit": "dollars", "universe": None, "acs_var": None}
    with pytest.raises(ValueError, match="additive"):
        add_indicators(store, pd.DataFrame([row]))
    add_indicators(store, pd.DataFrame([{**row, "additive": True}]))
    assert store.execute("SELECT unit, additive FROM indicator").fetchall() == [("dollars", True)]


def test_range_wording_is_quoted_from_db():
    root = Path(__file__).parents[2]
    for path in ("README.md", "klear/store/schema.sql"):
        text = (root / path).read_text(encoding="utf-8")
        assert MOE_STATUSES["range"] in text, path
        assert RANGE_MEANING in text, path
        assert VALUE_STATUSES["suppressed"] in text, path


def test_suppressed_phrase_is_source_neutral():
    # HUD vouchers and CMS enrollment are administrative records, not surveys
    phrase = VALUE_STATUSES["suppressed"]
    assert phrase.startswith("not available")
    assert "survey" not in phrase


def test_range_status_requires_value_low(con):
    with pytest.raises(ValueError, match="moe_status range without value_low"):
        add_facts(con, fact(tier="D", value_low=None, value_high=None))


def test_site_rejects_null_district_and_negative_snap(con):
    with pytest.raises(ValueError, match="house"):
        add_sites(con, site(house=None))
    with pytest.raises(ValueError, match="senate"):
        add_sites(con, site(senate=None))
    with pytest.raises(ValueError, match="snapped_m"):
        add_sites(con, site(snapped_m=-1.0))


def test_indicator_additive_stored(con):
    rows = con.execute("SELECT indicator_id, additive FROM indicator ORDER BY 1").fetchall()
    assert rows == [("eitc", True), ("rate", False)]


def test_profile_indicator_additive():
    count = profile.Indicator("a", "A", "count", "U", "DP05_0001", "acs/acs5/profile")
    pct = profile.Indicator("b", "B", "percent", "U", "DP03_0128P", "acs/acs5/profile")
    assert (count.additive, pct.additive) == (True, False)


def test_present_estimate_with_not_applicable_moe():
    r = pd.DataFrame(
        [
            {
                "geo_type": "house", "geo_id": "001", "variable": "DP05_0001", "estimate": 100.0,
                "estimate_status": "ok", "moe": None, "moe_status": "not_applicable",
            }
        ]
    ).itertuples()  # fmt: skip
    i = profile.Indicator("a", "A", "count", "U", "DP05_0001", "acs/acs5/profile")
    assert profile._fact(i, next(r), {}) == (100.0, None, "ok", "not_applicable")


def test_site_added(con):
    add_sites(con, site())
    assert con.execute("SELECT site_id, kind FROM site").fetchall() == [("ph1", "public_housing")]
    assert SITE_KINDS == {"public_housing", "lihtc", "health_center"}


def test_site_kind_checked(con):
    with pytest.raises(ValueError, match="kind"):
        add_sites(con, site(kind="school"))
    for bad in ({"lat": 17.9}, {"lat": 23.1}, {"lon": -161.1}, {"lon": -153.9}):
        with pytest.raises(ValueError, match="lat|lon"):
            add_sites(con, site(**bad))


def test_site_needs_known_district_and_source(con):
    with pytest.raises(ValueError, match="house"):
        add_sites(con, site(house="999"))
    with pytest.raises(ValueError, match="senate"):
        add_sites(con, site(senate="99"))
    with pytest.raises(ValueError, match="source_id"):
        add_sites(con, site(source_id="nope"))
