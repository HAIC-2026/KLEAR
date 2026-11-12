# Tests for the district population check against the Census Bureau and the Reapportionment Commission.
import json

import pandas as pd
import pytest

from klear import config
from klear.validation.population import check_population
from klear.validation.report import render_validation, tables

BLOCKS = pd.DataFrame(
    {
        "block": ["b1", "b2", "b3", "b4"],
        "house": ["001", "001", "002", "002"],
        "senate": ["001", "001", "001", "001"],
        "pop2020": [100, 50, 300, 0],
    }
)


def census(**change) -> pd.DataFrame:
    rows = [
        ("house", "001", "State House District 1 (2022), Hawaii", 150),
        ("house", "002", "State House District 2 (2022), Hawaii", 300),
        ("senate", "001", "State Senate District 1 (2022), Hawaii", 450),
    ]
    df = pd.DataFrame(rows, columns=["chamber", "geo_id", "name", "pop"])
    for key, pop in change.items():
        chamber, geo = key.split("_")
        df.loc[(df["chamber"] == chamber) & (df["geo_id"] == geo), "pop"] = pop
    return df


COMMISSION = pd.DataFrame(
    [
        ("house", "001", "Oahu", 150, 140),
        ("house", "002", "Oahu", 300, 200),
        ("senate", "001", "Oahu", 450, 340),
    ],
    columns=["chamber", "geo_id", "unit", "census_total", "permanent"],
)


def test_matching_districts_pass_and_report_the_commission_base():
    r = check_population(BLOCKS, census(), COMMISSION)
    assert r.passed and r.gate and r.n_compared == 3 and r.n_mismatch == 0
    assert "House: 2 of 2 districts match the Census Bureau's 2020 count exactly" in r.notes
    assert any("equal KLEAR's block sums in 3 of 3 districts" in n for n in r.notes)
    base = next(n for n in r.notes if n.startswith("Permanent-resident base"))
    assert "340 permanent residents, 110 (24.4%) fewer than the 2020 census count of 450" in base
    assert "non-resident military members, their dependents and non-resident students" in base
    assert (
        "House: largest differences are House 2 (100 people, 33.3%), House 1 (10 people, 6.7%)"
        in " ".join(r.notes)
    )
    house2 = next(d for d in r.details if d.get("name") == "House 2")
    assert house2["permanent"] == 200 and house2["not_permanent"] == 100


def test_any_difference_fails_with_the_district_named():
    r = check_population(BLOCKS, census(house_002=301), COMMISSION)
    assert not r.passed and r.n_mismatch == 1
    bad = [d for d in r.details if not d["passed"]]
    assert bad[0]["name"] == "House 2" and bad[0]["difference"] == -1
    text = render_validation([r], [], {})
    assert "**population: FAIL** (gated)" in text and "First 1 of 1 findings" in text


def test_a_district_missing_from_either_side_fails():
    r = check_population(BLOCKS, census().iloc[1:], None, commission_problem="download failed")
    assert not r.passed
    missing = next(d for d in r.details if d["name"] == "House 1")
    assert missing["census"] is None and missing["difference"] is None
    assert "Reapportionment Commission files not compared: download failed" in r.notes


def test_counts_for_an_older_plan_fail():
    old = census()
    old["name"] = old["name"].str.replace("(2022)", "(2018)")
    r = check_population(BLOCKS, old, COMMISSION)
    assert not r.passed and {d["check"] for d in r.details if not d["passed"]} == {"plan"}


def test_population_table_lists_every_district():
    (t,) = tables(check_population(BLOCKS, census(), COMMISSION))
    assert [c["label"] for c in t["columns"]][-1] == "Not permanent residents"
    assert t["rows"][1] == ["House 2", "300", "300", "yes", "200", "100 (33.3%)"]
    (bare,) = tables(check_population(BLOCKS, census(), None))
    assert len(bare["columns"]) == 4 and "permanent" not in bare["note"]
    text = render_validation([check_population(BLOCKS, census(), COMMISSION)], [], {})
    assert "### District populations" in text and "| House 2 | 300 | 300 | yes | 200 | 100 (33.3%) |" in text
    assert ";" not in text and "—" not in text


def test_built_store_matches_the_census_bureau_in_every_district():
    path = config.BUILD_DIR / "validation.json"
    if not path.exists():
        pytest.skip("Build the store to check its district populations")
    checks = {c["name"]: c for c in json.loads(path.read_text(encoding="utf-8"))["checks"]}
    if "population" not in checks:
        pytest.skip("This build predates the population check")
    r = checks["population"]
    assert r["passed"] and r["n_compared"] == 76 and r["n_mismatch"] == 0
