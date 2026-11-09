# Tests for the indicator catalog, DBEDT-to-profile alignment and profile fact loading.
import httpx
import pandas as pd
import pytest

from klear.etl.census_api import get_variable_labels
from klear.etl.dbedt import COLUMNS, fetch_dbedt, parse_dbedt_profile
from klear.etl.profile import (
    SOURCES,
    AlignmentError,
    Indicator,
    align_dbedt_to_profile,
    build_geographies,
    facts_from_api,
    load_catalog,
    load_profile_facts,
    sum_bins,
)
from klear.store.db import UNITS, open_store
from klear.validation.store import check_store

GEO_TYPES = ("state", "county", "senate", "house")
GEO_COUNTS = {"state": 1, "county": 5, "senate": 25, "house": 51}


def dbedt_rows(sheet, labels, geo_ids=("001",)):
    rows = []
    for geo_id in geo_ids:
        for row_no, label in labels:
            rows.append(
                {
                    "geo_type": "house", "geo_id": geo_id, "sheet": sheet, "row_no": row_no,
                    "label": label, "section": None, "estimate": 1.0, "estimate_status": "ok",
                    "moe": 1.0, "moe_status": "ok", "percent": None, "percent_moe": None,
                    "percent_status": "not_applicable", "percent_moe_status": "not_applicable",
                }
            )  # fmt: skip
    return pd.DataFrame(rows, columns=COLUMNS)


LABELS = {
    "DP02_0001E": "Estimate!!HOUSEHOLDS BY TYPE!!Total households",
    "DP02_0001PE": "Percent!!HOUSEHOLDS BY TYPE!!Total households",
    "DP02_0002E": "Estimate!!HOUSEHOLDS BY TYPE!!Total households!!Married-couple household",
    "DP02_0002PE": "Percent!!HOUSEHOLDS BY TYPE!!Total households!!Married-couple household",
    "DP03_0001E": "Estimate!!EMPLOYMENT STATUS!!Population 16 years and over",
    "NAME": "Geographic Area Name",
}


def test_catalog_units_valid():
    cat = load_catalog()
    assert len(cat) >= 40
    assert {i.unit for i in cat} <= UNITS
    assert all(isinstance(i, Indicator) for i in cat)


def test_catalog_ids_unique():
    ids = [i.indicator_id for i in load_catalog()]
    assert len(ids) == len(set(ids))


def test_catalog_vars_match_dataset_and_unit():
    for i in load_catalog():
        assert i.dataset == ("acs/acs5/profile" if i.acs_var.startswith("DP") else "acs/acs5")
        assert i.acs_var.endswith("P") == (i.unit == "percent"), i.indicator_id


def test_catalog_rejects_bad_unit(tmp_path):
    p = tmp_path / "c.yaml"
    p.write_text(
        "indicators:\n  - indicator_id: x\n    label: X\n    unit: minutes\n    universe: U\n"
        "    acs_var: DP03_0025\n    dataset: acs/acs5/profile\n",
        encoding="utf-8",
    )
    with pytest.raises(ValueError, match="minutes"):
        load_catalog(p)


def test_catalog_rejects_duplicate_ids(tmp_path):
    entry = (
        "  - indicator_id: x\n    label: X\n    unit: count\n    universe: U\n"
        "    acs_var: DP05_0001\n    dataset: acs/acs5/profile\n"
    )
    p = tmp_path / "c.yaml"
    p.write_text("indicators:\n" + entry + entry, encoding="utf-8")
    with pytest.raises(ValueError, match="duplicate"):
        load_catalog(p)


def test_catalog_geo_types_default_and_declared(tmp_path):
    p = tmp_path / "c.yaml"
    base = "    label: X\n    unit: count\n    universe: U\n    acs_var: B01003_001\n    dataset: acs/acs5\n"
    p.write_text(
        "indicators:\n  - indicator_id: a\n" + base + "  - indicator_id: b\n" + base
        + "    geo_types: [state, county]\n", encoding="utf-8")  # fmt: skip
    a, b = load_catalog(p)
    assert a.geo_types == GEO_TYPES and b.geo_types == ("state", "county")
    p.write_text("indicators:\n  - indicator_id: a\n" + base + "    geo_types: [ward]\n", encoding="utf-8")
    with pytest.raises(ValueError, match="ward"):
        load_catalog(p)
    p.write_text("indicators:\n  - indicator_id: a\n" + base + "    extra: 1\n", encoding="utf-8")
    with pytest.raises(ValueError, match="fields"):
        load_catalog(p)


def test_load_profile_facts_fetches_only_declared_coverage(tmp_path, monkeypatch):
    monkeypatch.setenv("CENSUS_API_KEY", "SECRETKEY123")
    geos = pd.DataFrame(
        [
            {"geo_type": "house", "geo_id": "001", "name": "H"},
            {"geo_type": "state", "geo_id": "15", "name": "S"},
        ]
    )
    seen = []

    def handler(request):
        seen.append(request.url.params["for"])
        get = request.url.params["get"].split(",")
        if request.url.params["for"] == "state:15":
            return httpx.Response(200, json=[get + ["state"], ["X"] + ["7"] * (len(get) - 1) + ["15"]])
        cols = ["state", "state legislative district (lower chamber)"]
        return httpx.Response(200, json=[get + cols, ["X"] + ["7"] * (len(get) - 1) + ["15", "001"]])

    cat = [
        Indicator("a", "A", "count", "U", "B01003_001", "acs/acs5", ("state",)),
        Indicator("b", "B", "count", "U", "B11001_001", "acs/acs5", ("state", "house")),
    ]
    con = open_store(tmp_path / "k.duckdb")
    client = httpx.Client(transport=httpx.MockTransport(handler))
    n = load_profile_facts(con, ("state", "house"), catalog=cat, geographies=geos, client=client)
    assert n == 3
    got = set(con.execute("SELECT indicator_id, geo_type FROM fact").fetchall())
    assert got == {("a", "state"), ("b", "state"), ("b", "house")}
    cover = set(con.execute("SELECT indicator_id, geo_type FROM indicator_coverage").fetchall())
    assert cover == got


def test_alignment_requires_leaf_label_match():
    df = dbedt_rows("DP02", [(3, "Total households"), (4, "Married-couple household")], ("001", "002"))
    out = align_dbedt_to_profile(df, LABELS)
    assert list(out.columns) == COLUMNS + ["acs_var"]
    assert out.set_index(["geo_id", "row_no"]).acs_var.to_dict() == {
        ("001", 3): "DP02_0001",
        ("001", 4): "DP02_0002",
        ("002", 3): "DP02_0001",
        ("002", 4): "DP02_0002",
    }
    bad = dbedt_rows("DP02", [(3, "Total households"), (4, "Cohabiting couple household")])
    with pytest.raises(AlignmentError) as e:
        align_dbedt_to_profile(bad, LABELS)
    msg = str(e.value)
    assert "DP02" in msg and "row 4" in msg and "Cohabiting couple household" in msg
    assert "DP02_0002" in msg


def test_alignment_fails_on_skipped_row():
    df = dbedt_rows("DP02", [(4, "Married-couple household")])
    with pytest.raises(AlignmentError, match="row 4"):
        align_dbedt_to_profile(df, LABELS)


def test_alignment_fails_on_count_difference():
    df = dbedt_rows("DP02", [(3, "Total households")])
    with pytest.raises(AlignmentError, match="DP02_0002"):
        align_dbedt_to_profile(df, LABELS)
    extra = dbedt_rows("DP03", [(3, "Population 16 years and over"), (4, "In labor force")])
    with pytest.raises(AlignmentError, match="In labor force"):
        align_dbedt_to_profile(extra, LABELS)


def test_alignment_fails_on_unknown_sheet():
    with pytest.raises(AlignmentError, match="DP09"):
        align_dbedt_to_profile(dbedt_rows("DP09", [(3, "Total households")]), LABELS)


def test_alignment_fails_on_label_disagreement_across_geos():
    df = dbedt_rows("DP02", [(3, "Total households"), (4, "Married-couple household")], ("001", "002"))
    df.loc[(df.geo_id == "002") & (df.row_no == 4), "label"] = "Other"
    with pytest.raises(AlignmentError, match="row 4"):
        align_dbedt_to_profile(df, LABELS)


def ind(indicator_id, acs_var, unit="count", label="Thing", dataset="acs/acs5/profile"):
    return Indicator(indicator_id, label, unit, "U", acs_var, dataset)


def api_row(variable, estimate, moe, est_status="ok", moe_status="ok", geo_id="001"):
    return {
        "geo_type": "house", "geo_id": geo_id, "variable": variable, "estimate": estimate,
        "estimate_status": est_status, "moe": moe, "moe_status": moe_status,
    }  # fmt: skip


def test_facts_from_api_maps_statuses():
    cat = [
        ind("a", "DP05_0001"),
        ind("b", "DP03_0128P", unit="percent"),
        ind("c", "DP05_0005"),
        ind("d", "DP05_0019"),
    ]
    api = pd.DataFrame(
        [
            api_row("DP05_0001", 100.0, 10.0),
            api_row("DP03_0128P", 12.5, 1.5),
            api_row("DP05_0005", None, None, "suppressed", "not_computable"),
            api_row("DP05_0019", 50.0, None, "ok", "controlled"),
        ]
    )
    out = facts_from_api(api, cat, "acs2024_5yr_profile").set_index("indicator_id")
    cols = ["value", "moe", "value_status", "moe_status"]
    assert out.loc["a", cols + ["tier", "table_id"]].tolist() == [100.0, 10.0, "ok", "ok", "D", "DP05"]
    assert out.loc["b", cols].tolist() == [12.5, 1.5, "ok", "ok"]
    assert out.loc["c", ["value_status", "moe_status"]].tolist() == ["suppressed", "not_computable"]
    assert pd.isna(out.loc["c", "value"]) and pd.isna(out.loc["c", "moe"])
    assert out.loc["d", ["value", "value_status", "moe_status"]].tolist() == [50.0, "ok", "controlled"]
    assert set(out.source_id) == {"acs2024_5yr_profile"}


def coded(value, status, var="DP04_0134", geo_id="001"):
    df = dbedt_rows("DP04", [(10, "Median (dollars)")], (geo_id,))
    return df.assign(estimate=value, estimate_status=status, moe=None, moe_status="open_ended", acs_var=var)


RENT = [ind("rent", "DP04_0134", "dollars", "Median gross rent")]


def test_facts_from_api_open_ended_takes_dbedt_value():
    for raw, value, status in [(4001.0, 4000.0, "top_coded"), (2499.0, 2500.0, "bottom_coded")]:
        api = pd.DataFrame([api_row("DP04_0134", raw, None, "ok", "open_ended")])
        out = facts_from_api(api, RENT, "s", coded(value, status)).iloc[0]
        assert (out.value, out.value_status, out.moe_status) == (value, status, "open_ended")
        assert pd.isna(out.moe)


def test_facts_from_api_open_ended_needs_dbedt_counterpart():
    api = pd.DataFrame([api_row("DP04_0134", 4001.0, None, "ok", "open_ended")])
    with pytest.raises(ValueError, match="DP04_0134"):
        facts_from_api(api, RENT, "s")
    with pytest.raises(ValueError, match="DP04_0134"):
        facts_from_api(api, RENT, "s", coded(4000.0, "top_coded", geo_id="002"))
    with pytest.raises(ValueError, match="DP04_0134"):
        facts_from_api(api, RENT, "s", coded(4000.0, "ok"))
    with pytest.raises(ValueError, match="DP04_0134"):
        facts_from_api(api, RENT, "s", coded(5000.0, "top_coded"))


def test_uncomputable_moe_keeps_estimate():
    # MOE -222222222 or "**": keep the estimate as ok, never coded, with no MOE
    for raw in (4001.0, 2499.0, 1234.0):
        api = pd.DataFrame([api_row("DP04_0134", raw, None, "ok", "not_computable")])
        out = facts_from_api(api, RENT, "s", coded(raw - 1, "top_coded")).iloc[0]
        assert (out.value, out.value_status, out.moe_status) == (raw, "ok", "not_computable")
        assert pd.isna(out.moe)


def test_present_estimate_with_not_applicable_moe():
    api = pd.DataFrame([api_row("DP05_0001", 101.0, None, "ok", "not_applicable")])
    out = facts_from_api(api, [ind("a", "DP05_0001")], "s")
    assert out.loc[0, ["value", "value_status", "moe_status"]].tolist() == [101.0, "ok", "not_applicable"]
    assert pd.isna(out.loc[0, "moe"])


def test_missing_estimate_with_a_moe_raises():
    api = pd.DataFrame([api_row("DP05_0001", None, 5.0, "suppressed", "ok")])
    with pytest.raises(ValueError, match="DP05_0001"):
        facts_from_api(api, [ind("a", "DP05_0001")], "s")


def test_facts_from_api_rejects_missing_rows():
    api = pd.DataFrame([api_row("DP05_0001", 1.0, 1.0)])
    with pytest.raises(ValueError, match="DP05_0005"):
        facts_from_api(api, [ind("a", "DP05_0001"), ind("b", "DP05_0005")], "s")


def test_load_profile_facts_offline(tmp_path, monkeypatch):
    monkeypatch.setenv("CENSUS_API_KEY", "SECRETKEY123")
    cat = [ind("pop", "DP05_0001"), ind("nh", "B02019_002", dataset="acs/acs5")]
    geos = pd.DataFrame(
        [
            {"geo_type": "house", "geo_id": "001", "name": "State House District 1 (2024); Hawaii"},
            {"geo_type": "house", "geo_id": "002", "name": "State House District 2 (2024); Hawaii"},
        ]
    )
    seen = []

    def handler(request):
        seen.append(request.url.path)
        get = request.url.params["get"].split(",")
        header = get + ["state", "state legislative district (lower chamber)"]
        rows = [[f"D{d}"] + ["7" for _ in get[1:]] + ["15", d] for d in ("001", "002")]
        return httpx.Response(200, json=[header] + rows)

    client = httpx.Client(transport=httpx.MockTransport(handler))
    con = open_store(tmp_path / "k.duckdb")
    n = load_profile_facts(con, ("house",), catalog=cat, geographies=geos, client=client)
    assert n == 4
    assert sorted(seen) == ["/data/2024/acs/acs5", "/data/2024/acs/acs5/profile"]
    facts = con.execute("SELECT indicator_id, source_id, tier FROM fact ORDER BY 1, 2").fetchall()
    assert set(facts) == {("nh", "acs2024_5yr_detailed", "D"), ("pop", "acs2024_5yr_profile", "D")}
    srcs = con.execute("SELECT source_id, publisher, vintage, license, url FROM source ORDER BY 1").fetchall()
    assert srcs == [
        ("acs2024_5yr_detailed", "U.S. Census Bureau", "2020-2024 ACS 5-year", "public domain",
         "https://api.census.gov/data/2024/acs/acs5"),
        ("acs2024_5yr_profile", "U.S. Census Bureau", "2020-2024 ACS 5-year", "public domain",
         "https://api.census.gov/data/2024/acs/acs5/profile"),
    ]  # fmt: skip
    assert con.execute("SELECT count(*) FROM geography").fetchone()[0] == 2
    assert (
        con.execute("SELECT acs_var FROM indicator WHERE indicator_id = 'nh'").fetchone()[0] == "B02019_002"
    )


def test_load_profile_facts_open_ended_uses_dbedt(tmp_path, monkeypatch):
    monkeypatch.setenv("CENSUS_API_KEY", "SECRETKEY123")
    geos = pd.DataFrame([{"geo_type": "house", "geo_id": d, "name": f"H{d}"} for d in ("001", "002")])

    def handler(request):
        get = request.url.params["get"].split(",")
        header = get + ["state", "state legislative district (lower chamber)"]
        rows = [["X", "4001", "-333333333", "15", d] for d in ("001", "002")]
        return httpx.Response(200, json=[header] + rows)

    client = httpx.Client(transport=httpx.MockTransport(handler))
    con = open_store(tmp_path / "k.duckdb")
    dbedt = pd.concat([coded(4000.0, "top_coded", geo_id=d) for d in ("001", "002")])
    n = load_profile_facts(
        con, ("house",), catalog=RENT, geographies=geos, client=client, dbedt_aligned=dbedt
    )
    assert n == 2
    rows = con.execute("SELECT value, moe, value_status, moe_status FROM fact").fetchall()
    assert set(rows) == {(4000.0, None, "top_coded", "open_ended")}


def test_sources_cover_both_datasets():
    assert {s["dataset"] for s in SOURCES.values()} == {"acs/acs5/profile", "acs/acs5"}


@pytest.mark.network
def test_catalog_vars_exist_in_api():
    labels = {
        "acs/acs5/profile": get_variable_labels("acs/acs5/profile"),
        "acs/acs5": get_variable_labels("acs/acs5"),
    }
    for i in load_catalog():
        assert f"{i.acs_var}E" in labels[i.dataset], i.indicator_id


@pytest.mark.network
def test_build_geographies_has_82():
    geos = build_geographies(GEO_TYPES)
    assert geos.groupby("geo_type").size().to_dict() == {"state": 1, "county": 5, "senate": 25, "house": 51}
    assert geos.set_index(["geo_type", "geo_id"]).name[("house", "022")] == (
        "State House District 22 (2024); Hawaiʻi"
    )


@pytest.mark.network
def test_load_profile_facts_live_matches_dbedt(tmp_path):
    con = open_store(tmp_path / "k.duckdb")
    cat = load_catalog()
    n = load_profile_facts(con)
    assert n == sum(GEO_COUNTS[g] for i in cat for g in i.geo_types)
    assert con.execute("SELECT count(*) FROM geography").fetchone()[0] == 82
    labels = get_variable_labels("acs/acs5/profile")
    aligned = pd.concat(
        [align_dbedt_to_profile(parse_dbedt_profile(fetch_dbedt(g), g), labels) for g in GEO_TYPES]
    )
    r = check_store(con, aligned)
    assert r.passed, r.details
    assert r.stats["values_compared"] > 3000 and r.stats["values_mismatched"] == 0


def test_build_geographies_spells_county_names(monkeypatch):
    from klear.etl import profile

    monkeypatch.setattr(profile, "fetch_dbedt", lambda g: g)
    monkeypatch.setattr(
        profile, "dbedt_geo_names", lambda path, g: {"15001": "Hawaii County", "15007": "Kauai County"}
    )
    geos = profile.build_geographies(("county",))
    assert dict(zip(geos["geo_id"], geos["name"], strict=True)) == {
        "15001": "Hawaiʻi County",
        "15007": "Kauaʻi County",
    }


# income-eligible estimates summed from published bins

ELIGIBLE = {
    "hh_poverty_no_snap": ["B22003_006"],
    "pop_under_125pct_poverty": ["C17002_002", "C17002_003", "C17002_004"],
    "pop_under_138pct_poverty": ["C27016_002", "C27016_012"],
    "pop_under_150pct_poverty": ["C17002_002", "C17002_003", "C17002_004", "C17002_005"],
    "pop_under_200pct_poverty": [f"C17002_{n:03d}" for n in range(2, 8)],
    "uninsured_under_138pct_poverty": [f"C27016_{n:03d}" for n in (5, 8, 11, 15, 18, 21)],
    "uninsured_138_399pct_poverty": [f"C27016_{n:03d}" for n in (25, 28, 31, 35, 38, 41)],
    "kupuna_under_200pct_poverty": [f"B17024_{n:03d}" for n in [*range(107, 115), *range(120, 128)]],
    "working_below_poverty": [f"B17014_{n:03d}" for n in (5, 6, 7, 11, 12, 13, 16, 17, 18)]
    + ["B17009_003", "B17009_006"],
}


def test_catalog_eligible_estimates_use_only_published_bins_and_carry_the_income_test_note():
    cat = {i.indicator_id: i for i in load_catalog()}
    for iid, bins in ELIGIBLE.items():
        i = cat[iid]
        assert list(i.variables) == bins, iid
        assert i.dataset == "acs/acs5" and i.unit == "count" and i.geo_types == GEO_TYPES
        assert i.note.startswith("Meets the income test only.") and "household rules" in i.note
        assert "\u2014" not in i.note and "\u00b7" not in i.note and ";" not in i.note
    assert cat["working_below_poverty"].table_id == "B17014+B17009"
    assert cat["hh_snap"].variables == ("B22003_002",) and cat["hh_snap"].note is None
    # SNAP's 130 percent limit is left as the published 125 and 150 percent cuts, never interpolated
    assert not any("130" in i.indicator_id for i in cat.values())


def test_catalog_bins_entries_are_checked(tmp_path):
    p = tmp_path / "c.yaml"
    base = "  - indicator_id: a\n    label: X\n    unit: count\n    universe: U\n    dataset: acs/acs5\n"
    p.write_text("indicators:\n" + base + "    bins: [C17002_002, C17002_003]\n    note: Only an estimate.\n")
    (a,) = load_catalog(p)
    assert a.acs_var == "C17002_002+C17002_003" and a.bins == ("C17002_002", "C17002_003")
    assert a.note == "Only an estimate." and a.table_id == "C17002"
    for bad, why in (("[C17002_002, C17002_002]", "distinct"), ("[DP05_0001, C17002_003]", "does not fit")):
        p.write_text("indicators:\n" + base + f"    bins: {bad}\n")
        with pytest.raises(ValueError, match=why):
            load_catalog(p)
    p.write_text("indicators:\n" + base.replace("count", "percent") + "    bins: [C17002_002, C17002_003]\n")
    with pytest.raises(ValueError, match="count cells"):
        load_catalog(p)


def bins_frame(cells):
    return pd.DataFrame([api_row(f"C17002_{k:03d}", *c) for k, c in enumerate(cells, start=2)])


def test_sum_bins_adds_estimates_and_combines_margins_by_root_sum_of_squares():
    value, moe, vs, ms = sum_bins(bins_frame([(100.0, 30.0), (50.0, 40.0), (0.0, 12.0)]))
    assert (value, vs, ms) == (150.0, "ok", "ok")
    assert moe == pytest.approx((30**2 + 40**2 + 12**2) ** 0.5)


def test_sum_bins_statuses():
    controlled = (10.0, None, "ok", "controlled")
    assert sum_bins(bins_frame([controlled, controlled])) == (20.0, None, "ok", "controlled")
    # a controlled cell has no sampling error and adds nothing to the margin
    value, moe, _, ms = sum_bins(bins_frame([controlled, (5.0, 3.0)]))
    assert (value, moe, ms) == (15.0, 3.0, "ok")
    assert sum_bins(bins_frame([(5.0, None, "ok", "not_computable"), (5.0, 3.0)])) == (
        10.0,
        None,
        "ok",
        "not_computable",
    )
    assert sum_bins(bins_frame([(None, None, "suppressed", "not_computable"), (5.0, 3.0)])) == (
        None,
        None,
        "suppressed",
        "not_computable",
    )


def test_facts_from_api_sums_bins_per_geography():
    cat = [Indicator("u125", "Under 125", "count", "U", "x", "acs/acs5", bins=("C17002_002", "C17002_003"))]
    api = pd.DataFrame(
        [
            api_row("C17002_002", 10.0, 3.0),
            api_row("C17002_003", 20.0, 4.0),
            api_row("C17002_002", 1.0, 1.0, geo_id="002"),
            api_row("C17002_003", 2.0, 1.0, geo_id="002"),
            api_row("C17002_004", 99.0, 9.0),
        ]
    )
    out = facts_from_api(api, cat, "acs2024_5yr_detailed").set_index("geo_id")
    assert out.loc["001", ["value", "moe", "value_status", "moe_status"]].tolist() == [30.0, 5.0, "ok", "ok"]
    assert out.loc["002", "value"] == 3.0 and out.loc["002", "moe"] == pytest.approx(2**0.5)
    assert set(out.table_id) == {"C17002"} and set(out.indicator_id) == {"u125"}
    with pytest.raises(ValueError, match="C17002_003"):
        facts_from_api(api[api.variable != "C17002_003"], cat, "s")
