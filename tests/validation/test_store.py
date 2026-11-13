# Tests for the fact store check: geography counts, declared coverage and stored values against DBEDT.
import pandas as pd
import pytest

from klear.etl.dbedt import COLUMNS
from klear.rules.registry import Rule
from klear.store.db import (
    add_coverage,
    add_coverage_exclusions,
    add_fact_matches,
    add_facts,
    add_geographies,
    add_indicators,
    add_source,
    open_store,
)
from klear.validation.store import check_store, matched_facts, store_counts

GEOS = {"state": 1, "county": 1}


def dbedt(geo_type, geo_id, acs_var, **over):
    row = dict.fromkeys(COLUMNS) | {
        "geo_type": geo_type, "geo_id": geo_id, "sheet": acs_var[:4], "row_no": 3, "label": "x",
        "estimate": 100.0, "estimate_status": "ok", "moe": 10.0, "moe_status": "ok",
        "percent": 50.0, "percent_status": "ok", "percent_moe": 5.0, "percent_moe_status": "ok",
        "acs_var": acs_var,
    }  # fmt: skip
    return row | over


@pytest.fixture
def con(tmp_path):
    c = open_store(tmp_path / "k.duckdb")
    add_source(c, "acs2024_5yr_profile", "p", "x", "x", "x", "x")
    add_source(c, "acs2024_5yr_detailed", "d", "x", "x", "x", "x")
    add_geographies(
        c,
        pd.DataFrame(
            [
                {"geo_type": "state", "geo_id": "15", "name": "Hawaiʻi", "parent_county": None},
                {"geo_type": "county", "geo_id": "15001", "name": "Hawaiʻi County", "parent_county": None},
            ]
        ),
    )
    inds = [("pop", "DP05_0001", "count"), ("pct", "DP05_0001P", "percent"), ("det", "B01003_001", "count")]
    add_indicators(
        c,
        pd.DataFrame(
            [
                {
                    "indicator_id": i,
                    "label": i,
                    "unit": u,
                    "universe": "u",
                    "acs_var": v,
                    "additive": u == "count",
                }
                for i, v, u in inds
            ]
        ),
    )
    cover = [("pop", "state"), ("pop", "county"), ("pct", "state"), ("pct", "county"), ("det", "state")]
    add_coverage(c, pd.DataFrame(cover, columns=["indicator_id", "geo_type"]))
    rows = []
    for g, gid in (("state", "15"), ("county", "15001")):
        rows.append(fact(g, gid, "pop", 100.0, 10.0))
        rows.append(fact(g, gid, "pct", 50.0, 5.0))
    rows.append(fact("state", "15", "det", 7.0, 1.0, source="acs2024_5yr_detailed"))
    add_facts(c, pd.DataFrame(rows))
    return c


def fact(g, gid, ind, value, moe, vs="ok", ms="ok", source="acs2024_5yr_profile"):
    return {
        "geo_type": g, "geo_id": gid, "indicator_id": ind, "value": value, "moe": moe, "value_status": vs,
        "moe_status": ms, "tier": "D", "source_id": source, "table_id": "T",
    }  # fmt: skip


ALIGNED = pd.DataFrame([dbedt("state", "15", "DP05_0001"), dbedt("county", "15001", "DP05_0001")])


def test_store_passes_and_counts_api_only(con):
    r = check_store(con, ALIGNED, GEOS)
    assert r.passed, r.details
    assert r.n_compared == 5
    assert r.stats["values_compared"] == 4 and r.stats["values_mismatched"] == 0
    assert r.stats["api_only"] == 1
    assert any("Census API only" in n for n in r.notes)


@pytest.mark.parametrize(
    "over,fields",
    [
        ({"estimate": 101.0}, {"value"}),
        ({"moe": 11.0}, {"moe"}),
        ({"percent": 50.1}, {"value"}),
        ({"percent_moe": None, "percent_moe_status": "not_computable"}, {"moe", "moe_status"}),
        ({"estimate_status": "top_coded"}, {"value_status"}),
        ({"moe_status": "controlled"}, {"moe_status"}),
    ],
)
def test_store_flags_each_stored_field(con, over, fields):
    aligned = pd.DataFrame([dbedt("state", "15", "DP05_0001", **over), dbedt("county", "15001", "DP05_0001")])
    r = check_store(con, aligned, GEOS)
    assert not r.passed
    assert {(d["check"], d["field"]) for d in r.details} == {("stored_value", f) for f in fields}


def test_store_flags_fact_without_dbedt_cell(con):
    r = check_store(con, ALIGNED.iloc[:1], GEOS)
    assert not r.passed
    assert {(d["check"], d["geo_id"]) for d in r.details} == {("no_dbedt_cell", "15001")}


def test_store_without_dbedt_frame_reports_not_compared(con):
    r = check_store(con, None, GEOS)
    assert r.passed and r.stats["values_compared"] is None
    assert any("not compared" in n for n in r.notes)


def test_store_completeness_follows_declared_coverage(con):
    add_coverage(con, pd.DataFrame([{"indicator_id": "det", "geo_type": "county"}]))
    r = check_store(con, ALIGNED, GEOS)
    assert [d for d in r.details if d["check"] == "missing_fact"] == [
        {"check": "missing_fact", "geo_type": "county", "geo_id": "15001", "indicator": "det"}
    ]


def test_partial_coverage_allows_missing_facts(con):
    add_coverage(con, pd.DataFrame([{"indicator_id": "det", "geo_type": "county", "complete": False}]))
    r = check_store(con, ALIGNED, GEOS)
    assert r.passed, r.details
    assert r.n_compared == 5 and r.stats["partial_coverage"] == 0
    con.execute("DELETE FROM indicator_coverage WHERE indicator_id = 'det' AND geo_type = 'state'")
    add_coverage(con, pd.DataFrame([{"indicator_id": "det", "geo_type": "state", "complete": False}]))
    r = check_store(con, ALIGNED, GEOS)
    assert r.passed and r.stats["partial_coverage"] == 1
    assert any("1 facts lie in partial coverage" in n for n in r.notes)


def test_store_counts_tiers_sites_and_rules(con):
    rules = [Rule("a", "p", s, "2026-01-01", "u", {}, None, "v", "2026-10-30") for s in ("confirmed",) * 2]
    c = store_counts(con, rules + [Rule("b", "p", "needs_manual", "2026-01-01", "u", {}, None, "v", "x")])
    assert c["facts tier D (direct)"] == 5 and c["sites health center"] == 0
    assert c["rules confirmed"] == 2 and c["rules needing a manual check"] == 1
    assert "rules confirmed" not in store_counts(con)


def test_named_exclusion_is_the_only_allowed_gap(con):
    add_coverage(con, pd.DataFrame([{"indicator_id": "det", "geo_type": "county"}]))
    assert not check_store(con, ALIGNED, GEOS).passed
    add_coverage_exclusions(
        con,
        pd.DataFrame(
            [{"indicator_id": "det", "geo_type": "county", "geo_id": "15001", "reason": "not published"}]
        ),
    )
    r = check_store(con, ALIGNED, GEOS)
    assert r.passed, r.details
    assert any("not required: county 15001 for 1 indicators, because not published" in n for n in r.notes)
    with pytest.raises(ValueError, match="unknown geography"):
        add_coverage_exclusions(
            con, pd.DataFrame([{"indicator_id": "det", "geo_type": "county", "geo_id": "x", "reason": "r"}])
        )


def test_store_flags_fact_outside_coverage(con):
    con.execute("DELETE FROM indicator_coverage WHERE indicator_id = 'det'")
    r = check_store(con, ALIGNED, GEOS)
    assert {d["check"] for d in r.details} == {"outside_coverage"}


def test_store_geography_count_is_gated(con):
    r = check_store(con, ALIGNED)
    assert not r.passed
    assert r.details[0]["check"] == "geographies"


def _mark(con, *keys):
    rows = [
        {"geo_type": g, "geo_id": i, "indicator_id": ind, "source_id": "acs2024_5yr_profile",
         "match_source_id": "dbedt", "match_url": "https://example.org/dbedt.xlsx"}
        for g, i, ind in keys
    ]  # fmt: skip
    add_fact_matches(con, pd.DataFrame(rows))


def test_matched_facts_are_the_compared_facts_equal_to_their_cell(con):
    facts = con.execute(
        "SELECT f.*, i.acs_var FROM fact f JOIN indicator i USING (indicator_id) "
        "WHERE source_id = 'acs2024_5yr_profile'"
    ).df()
    aligned = pd.DataFrame(
        [dbedt("state", "15", "DP05_0001", moe=11.0), dbedt("county", "15001", "DP05_0001")]
    )
    got = matched_facts(facts, aligned)
    assert sorted(map(tuple, got.to_numpy())) == [
        ("county", "15001", "pct", "acs2024_5yr_profile"),
        ("county", "15001", "pop", "acs2024_5yr_profile"),
        ("state", "15", "pct", "acs2024_5yr_profile"),
    ]


def test_match_marks_must_be_proven_by_this_build(con):
    add_source(
        con,
        "dbedt",
        "DBEDT profiles",
        "DBEDT",
        "https://example.org",
        "x",
        "x",
        page_url="https://example.org",
    )
    _mark(con, ("state", "15", "pop"), ("county", "15001", "pct"))
    r = check_store(con, ALIGNED, GEOS)
    assert r.passed, r.details
    assert any(n.startswith("2 stored facts are marked as matching DBEDT") for n in r.notes)
    aligned = pd.DataFrame(
        [dbedt("state", "15", "DP05_0001", estimate=99.0), dbedt("county", "15001", "DP05_0001")]
    )
    r = check_store(con, aligned, GEOS)
    assert {"check": "unproven_match", "geo_type": "state", "geo_id": "15", "indicator_id": "pop",
            "source_id": "acs2024_5yr_profile"} in r.details  # fmt: skip
    r = check_store(con, None, GEOS)
    assert not r.passed and {"check": "unproven_match", "n": 2} in r.details


def test_match_marks_need_a_stored_fact_and_a_web_page(con):
    add_source(
        con,
        "dbedt",
        "DBEDT profiles",
        "DBEDT",
        "https://example.org",
        "x",
        "x",
        page_url="https://example.org",
    )
    with pytest.raises(ValueError, match="no stored fact"):
        _mark(con, ("state", "15", "det"))
    bad = {"geo_type": "state", "geo_id": "15", "indicator_id": "pop", "source_id": "acs2024_5yr_profile",
           "match_source_id": "dbedt", "match_url": "ftp://example.org"}  # fmt: skip
    with pytest.raises(ValueError, match="not https"):
        add_fact_matches(con, pd.DataFrame([bad]))
    with pytest.raises(ValueError, match="unknown match_source_id"):
        add_fact_matches(con, pd.DataFrame([bad | {"match_url": "https://x.org", "match_source_id": "nope"}]))


def test_county_level_facts_are_stored_only_for_counties_and_the_state(con):
    add_geographies(
        con, pd.DataFrame([{"geo_type": "house", "geo_id": "001", "name": "H1", "parent_county": None}])
    )
    row = fact("county", "15001", "det", 3.0, None, ms="not_applicable") | {"tier": "C"}
    add_facts(con, pd.DataFrame([row]))
    with pytest.raises(ValueError, match="tier C outside"):
        add_facts(con, pd.DataFrame([row | {"geo_type": "house", "geo_id": "001"}]))
    with pytest.raises(Exception, match="CHECK constraint"):
        con.execute("INSERT INTO fact (geo_type, geo_id, indicator_id, value, value_status, moe_status, tier, "
                    "source_id) VALUES ('house', '001', 'det', 1, 'ok', 'not_applicable', 'C', 'acs2024_5yr_profile')")  # fmt: skip
    r = check_store(con, ALIGNED, GEOS | {"house": 1})
    assert any(n.startswith("1 county-level facts (tier C) for 1 indicators, 0 of them") for n in r.notes)
