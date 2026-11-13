"""The landing page quotes the store and the report of the build that wrote it, and nothing older."""

import json

from klear.web.about import about, geography

BUILT = "2026-11-06 16:38:59 HST (2026-11-07T02:38:59Z UTC)"
CHECKS = {"boundaries_house": "pass", "source_fidelity": "pass", "store": "pass"}
SKIPPED = [
    {"name": "chas", "reason": "missing manual input", "manual": True},
    {"name": "hud_income_limits", "reason": "missing manual input", "manual": True},
]


def _build(con, checks=CHECKS, skipped=SKIPPED):
    con.execute(
        "INSERT INTO build_info VALUES (?, ?, ?, ?, ?, ?)",
        [BUILT, "abc1234", 2, "build incomplete", json.dumps(skipped), json.dumps(checks)],
    )


def _report(path, built_at=BUILT):
    checks = [
        {"name": "source_fidelity", "passed": True, "n_compared": 177776, "n_mismatch": 0},
        {"name": "store", "passed": True, "stats": {"values_compared": 3854, "values_mismatched": 0}},
    ]
    path.write_text(
        json.dumps({"built_at": built_at, "checks": checks, "skipped": SKIPPED}), encoding="utf-8"
    )
    return path


def test_counts_and_sources_come_from_the_store(small_con, tmp_path):
    data = about(small_con, tmp_path / "validation.json")
    assert data["facts"] == ["2 House and 1 Senate districts", "3 measures from 2 public sources"]
    assert data["crosscheck"] is None
    assert [(s["name"], s["publisher"], s["years"]) for s in data["sources"]] == [
        ("American Community Survey 5-year", "Census", "2020-2024"),
        ("Public Housing", "HUD", "2025"),
    ]
    assert set(data) == {"facts", "sources", "crosscheck"}


def test_build_date_cross_check_and_skipped_checks_are_stated(small_con, tmp_path):
    _build(small_con)
    data = about(small_con, _report(tmp_path / "validation.json"))
    assert data["facts"][2:] == [
        "Built Nov 6, 2026",
        "177,776 DBEDT values cross-checked, 0 mismatches",
        "3 checks passed, 2 waiting on HUD files",
    ]
    assert "177,776 values, 0 mismatches" in data["crosscheck"]
    assert "3,854 stored Census figures that DBEDT's profiles also publish match" in data["crosscheck"]


def test_report_from_another_build_is_not_quoted(small_con, tmp_path):
    _build(small_con)
    data = about(small_con, _report(tmp_path / "validation.json", built_at="2026-11-07 09:00:00 HST"))
    assert data["facts"][2:] == ["Built Nov 6, 2026", "3 checks passed, 2 waiting on HUD files"]
    assert data["crosscheck"] is None


def test_failed_and_other_skipped_checks_are_counted(small_con, tmp_path):
    _build(
        small_con,
        {"a": "pass", "b": "fail"},
        [{"name": "chas", "manual": True}, {"name": "x", "manual": False}],
    )
    assert about(small_con, tmp_path / "missing.json")["facts"][-1] == "1 check passed, 1 failed, 2 skipped"


def test_each_source_says_how_its_figures_reach_a_district():
    assert geography({"C"}, {"county", "state"}, False) == "Counties and the state only (county-level)"
    assert geography({"M"}, {"county", "state"}, False) == "Counties and the state, for comparison"
    assert geography({"M"}, {"tract", "house", "senate"}, False) == "Census tracts, modeled for districts"
    assert geography({"A", "S"}, {"zcta", "house", "county"}, False) == "ZIP codes, estimated for districts"
    assert geography({"D"}, {"house", "senate", "county", "state"}, False) == "Published for each district"
    assert (
        geography(set(), set(), True) == "Used to check Census figures for districts, counties and the state"
    )
