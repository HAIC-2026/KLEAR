# Tests for the district sum check and the comparison with program counts.
import duckdb
import pandas as pd
import pytest

from klear.eval.golden import ROOT
from klear.store.db import add_facts, add_geographies, add_indicators, add_source, open_store
from klear.validation.eligible import (
    SOURCE,
    check_admin_counts,
    check_district_sums,
    load_admin,
    summed_indicators,
)
from klear.validation.report import render_validation

STORE = ROOT / "data/build/klear.duckdb"


def fact(geo_type, geo_id, ind, value, moe, source=SOURCE, **kw):
    return {
        "geo_type": geo_type, "geo_id": geo_id, "indicator_id": ind, "value": value, "moe": moe,
        "value_status": "ok", "moe_status": "ok" if moe is not None else "controlled", "tier": "D",
        "source_id": source, "table_id": None,
    } | kw  # fmt: skip


@pytest.fixture
def con(tmp_path):
    con = open_store(tmp_path / "s.duckdb")
    add_source(
        con, SOURCE, "American Community Survey 5-year Detailed Tables", "Census", "u", "2020-2024", "pd"
    )
    add_source(con, "irs", "IRS Statistics of Income", "IRS", "u", "Tax year 2023", "pd")
    geos = [("state", "15"), ("county", "15003"), ("house", "001"), ("house", "002")]
    add_geographies(
        con, pd.DataFrame([{"geo_type": t, "geo_id": g, "name": g, "parent_county": None} for t, g in geos])
    )
    ids = ["hh_snap", "pop_under_138pct_poverty", "working_below_poverty", "irs_eitc_returns"]
    add_indicators(
        con,
        pd.DataFrame(
            [{"indicator_id": i, "label": i, "unit": "count", "universe": "U", "acs_var": None, "additive": True}
             for i in ids]
        ),
    )  # fmt: skip
    rows = []
    for ind, state, parts in (
        ("hh_snap", 100.0, (60.0, 40.0)),
        ("pop_under_138pct_poverty", 300.0, (100.0, 205.0)),
    ):
        rows += [fact("state", "15", ind, state, 4.0), fact("county", "15003", ind, state, 4.0)]
        rows += [fact("house", f"00{k}", ind, v, 3.0) for k, v in enumerate(parts, start=1)]
    rows += [fact("state", "15", "working_below_poverty", 50.0, 5.0)]
    rows += [
        fact(
            "state", "15", "irs_eitc_returns", 120.0, None, source="irs", tier="A", moe_status="range",
            value_low=110.0, value_high=130.0,
        )
    ]  # fmt: skip
    add_facts(con, pd.DataFrame(rows))
    cover = [(i, g) for i in ids[:2] for g in ("state", "county", "house")]
    con.executemany("INSERT INTO indicator_coverage VALUES (?, ?, TRUE)", cover)
    yield con
    con.close()


def test_district_sums_pass_within_the_state_margin_and_fail_outside(con):
    r = check_district_sums(con, ["hh_snap", "pop_under_138pct_poverty"])
    by = {(d["indicator"], d["parts"]): d for d in r.details}
    assert by[("hh_snap", "house")]["passed"] and by[("hh_snap", "house")]["difference"] == 0
    bad = by[("pop_under_138pct_poverty", "house")]
    assert not bad["passed"] and bad["difference"] == 5.0 and bad["sum_moe"] == pytest.approx(18**0.5)
    assert r.gate and not r.passed and (r.n_compared, r.n_mismatch) == (4, 1)


def test_district_sums_fail_when_a_district_is_missing(con):
    con.execute("DELETE FROM fact WHERE indicator_id = 'hh_snap' AND geo_id = '002'")
    r = check_district_sums(con, ["hh_snap"])
    assert not r.passed and r.details[0]["parts"] == "house" and r.details[0]["n"] == 1


def test_summed_indicators_are_the_detailed_table_counts():
    ids = summed_indicators()
    assert {"hh_snap", "uninsured_under_138pct_poverty", "working_below_poverty"} <= set(ids)
    assert "pop_total" not in ids and "pct_hh_snap" not in ids


def test_admin_counts_cite_an_official_source_for_every_figure():
    admin = load_admin()
    assert set(admin["counts"]) == {"snap_households", "medicaid_chip_enrollment", "eitc_participation_rate"}
    for c in admin["counts"].values():
        assert c["url"].startswith("https://") and c["retrieved"] and c["publisher"]
    assert admin["not_compared"] and all(x["reason"] for x in admin["not_compared"])


def test_admin_comparison_rows_and_report(con):
    admin = load_admin()
    r = check_admin_counts(con, admin)
    rows = {d["admin"]: d for d in r.details if d["kind"] == "comparison"}
    assert not r.gate and r.passed and r.n_compared == 3
    assert rows["snap_households"]["ratio"] == pytest.approx(
        100 / admin["counts"]["snap_households"]["value"]
    )
    eitc = rows["eitc_participation_rate"]
    assert eitc["admin_value"] == pytest.approx(120 / 0.833) and eitc["ratio"] == pytest.approx(
        50 / (120 / 0.833)
    )
    text = render_validation([r], [], {})
    assert "## Income-eligible estimates against program counts" in text
    assert "| Households that received SNAP in the past 12 months (ACS) | 100 ± 4 |" in text
    assert "tax returns against families and individuals" in text and "HTTP 403" in text
    assert "—" not in text


def test_admin_comparison_reports_missing_inputs(con):
    con.execute("DELETE FROM fact WHERE indicator_id = 'irs_eitc_returns'")
    r = check_admin_counts(con)
    assert r.passed and r.n_mismatch == 1 and r.stats["missing"] == ["working_below_poverty"]
    assert "working_below_poverty not compared: the store holds no state value for it" in r.notes


def test_sums_table_renders(con):
    r = check_district_sums(con, ["hh_snap"])
    text = render_validation([r], [], {})
    assert "| hh_snap | 100 ± 4 | 100 | 100 | yes |" in text


def test_built_store_districts_add_up_to_the_state():
    if not STORE.exists():
        pytest.skip("Build the store to check its district sums")
    with duckdb.connect(str(STORE), read_only=True) as con:
        r = check_district_sums(con)
        assert r.passed, [d for d in r.details if not d["passed"]][:3]
        assert r.n_compared == 3 * len(summed_indicators())
        assert {d["parts"] for d in r.details} == {"house", "senate", "county"}
