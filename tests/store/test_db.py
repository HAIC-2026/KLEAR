# Tests for the DuckDB fact store constraints.
import duckdb
import pandas as pd
import pytest

from klear.store.db import add_facts, add_geographies, add_indicators, add_source, open_store


@pytest.fixture
def con(tmp_path):
    c = open_store(tmp_path / "klear.duckdb")
    add_source(
        c, "acs5_2024", "ACS 5-year", "Census Bureau", "https://api.census.gov", "2020-2024", "public domain"
    )
    add_geographies(
        c,
        pd.DataFrame(
            [{"geo_type": "house", "geo_id": "001", "name": "House District 1", "parent_county": None}]
        ),
    )
    add_indicators(
        c,
        pd.DataFrame(
            [
                {
                    "indicator_id": "pop_total",
                    "label": "Total population",
                    "unit": "count",
                    "universe": "Total population",
                    "acs_var": "B01003_001E",
                    "additive": True,
                }
            ]
        ),
    )
    return c


def fact(**over):
    row = {
        "geo_type": "house",
        "geo_id": "001",
        "indicator_id": "pop_total",
        "value": 1000.0,
        "moe": 50.0,
        "value_status": "ok",
        "moe_status": "ok",
        "tier": "D",
        "source_id": "acs5_2024",
        "table_id": "B01003",
    }
    row.update(over)
    return pd.DataFrame([row])


def test_fact_requires_known_source(con):
    with pytest.raises(ValueError, match="nope"):
        add_facts(con, fact(source_id="nope"))


def test_fact_rejects_unknown_indicator(con):
    with pytest.raises(ValueError, match="no_such_indicator"):
        add_facts(con, fact(indicator_id="no_such_indicator"))


def test_fact_rejects_bad_tier(con):
    with pytest.raises(ValueError, match="tier"):
        add_facts(con, fact(tier="X"))


def test_negative_moe_rejected(con):
    with pytest.raises(ValueError, match="moe"):
        add_facts(con, fact(moe=-1.0))


def test_null_value_requires_null_value_status(con):
    with pytest.raises(ValueError, match="value"):
        add_facts(con, fact(value=None, moe=None, moe_status="not_computable"))
    with pytest.raises(ValueError, match="value"):
        add_facts(con, fact(value_status="suppressed"))
    add_facts(con, fact(value=None, moe=None, value_status="suppressed", moe_status="not_computable"))


def test_moe_present_exactly_when_moe_status_ok(con):
    with pytest.raises(ValueError, match="moe"):
        add_facts(con, fact(moe=None))
    with pytest.raises(ValueError, match="moe"):
        add_facts(con, fact(moe_status="controlled"))
    add_facts(con, fact(moe=None, moe_status="not_computable"))


def test_null_value_needs_absent_moe_status(con):
    with pytest.raises(ValueError, match="null value"):
        add_facts(con, fact(value=None, moe=None, value_status="suppressed", moe_status="controlled"))


def test_roundtrip(con):
    df = fact()
    add_facts(con, df)
    out = con.execute("select * from fact").df()
    pd.testing.assert_frame_equal(out[list(df.columns)], df, check_dtype=False)


def test_top_coded_roundtrip(con):
    df = fact(value=250000.0, moe=None, value_status="top_coded", moe_status="open_ended")
    add_facts(con, df)
    out = con.execute("select value, value_status, moe_status from fact").fetchall()
    assert out == [(250000.0, "top_coded", "open_ended")]
    with pytest.raises(ValueError):
        add_facts(con, fact(value=None, moe=None, value_status="bottom_coded", moe_status="open_ended"))


def test_duplicate_fact_key_rejected(con):
    add_facts(con, fact())
    with pytest.raises((ValueError, duckdb.ConstraintException)):
        add_facts(con, fact(value=2.0))


def test_fact_requires_existing_geography(con):
    with pytest.raises(ValueError, match="999"):
        add_facts(con, fact(geo_id="999"))


def test_geography_rejects_bad_type(con):
    bad = pd.DataFrame([{"geo_type": "ward", "geo_id": "1", "name": "x", "parent_county": None}])
    with pytest.raises(ValueError, match="ward"):
        add_geographies(con, bad)


def test_indicator_rejects_bad_unit(con):
    bad = pd.DataFrame(
        [
            {
                "indicator_id": "x",
                "label": "x",
                "unit": "furlongs",
                "universe": "u",
                "acs_var": None,
                "additive": False,
            }
        ]
    )
    with pytest.raises(ValueError, match="furlongs"):
        add_indicators(con, bad)


@pytest.mark.parametrize("col", ["value_status", "moe_status"])
def test_fact_rejects_bad_status(con, col):
    with pytest.raises(ValueError, match=col):
        add_facts(con, fact(**{col: "maybe"}))


def test_coded_value_and_open_ended_moe_go_together(con):
    with pytest.raises(ValueError, match="open_ended"):
        add_facts(con, fact(value=4000.0, moe=None, moe_status="open_ended"))
    with pytest.raises(ValueError, match="open_ended"):
        add_facts(con, fact(value=4000.0, moe=None, value_status="top_coded", moe_status="not_computable"))


@pytest.mark.parametrize(
    "value,moe,vs,ms",
    [
        (None, None, "ok", "not_computable"),
        (1.0, None, "ok", "ok"),
        (1.0, 2.0, "ok", "controlled"),
        (1.0, None, "top_coded", "not_computable"),
        (None, None, "suppressed", "controlled"),
        (1.0, 2.0, "no", "ok"),
    ],
)
def test_sql_constraints_hold_without_python_checks(con, value, moe, vs, ms):
    with pytest.raises(duckdb.ConstraintException):
        con.execute(
            "INSERT INTO fact (geo_type, geo_id, indicator_id, value, moe, value_status, moe_status, tier, source_id)"
            " VALUES ('house', '001', 'pop_total', ?, ?, ?, ?, 'D', 'acs5_2024')",
            [value, moe, vs, ms],
        )


def test_blocks_roundtrip_and_checks(con):
    from klear.store.db import add_blocks

    row = {"block": "150030001001000", "county": "15003", "tract": "15003000100", "zcta": None}
    add_blocks(con, pd.DataFrame([row | {"house": "022", "senate": "012", "pop2020": 7}]))
    assert con.execute("SELECT house, pop2020 FROM blocks").fetchall() == [("022", 7)]
    with pytest.raises(ValueError, match="pop2020"):
        add_blocks(con, pd.DataFrame([row | {"block": "x", "house": "022", "senate": "012", "pop2020": -1}]))


def test_indicator_note_is_optional_and_stored(con):
    row = {"label": "X", "unit": "count", "universe": "U", "acs_var": None, "additive": True}
    add_indicators(
        con, pd.DataFrame([{"indicator_id": "with_note", **row, "note": "Meets the income test only."}])
    )
    add_indicators(con, pd.DataFrame([{"indicator_id": "no_note", **row}]))
    notes = dict(
        con.execute("SELECT indicator_id, note FROM indicator WHERE indicator_id LIKE '%note'").fetchall()
    )
    assert notes == {"with_note": "Meets the income test only.", "no_note": None}
