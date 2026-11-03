# Tests for the DBEDT ACS district profile parser.
import openpyxl
import pandas as pd
import pytest

from klear.etl.dbedt import DBEDT_URLS, parse_dbedt_profile
from klear.etl.fetch import fetch

HEADERS = ("Estimate", "Margin of Error", "Percent", "Percent Margin of Error")


def make_workbook(path, labels, rows, sheet="DP03"):
    wb = openpyxl.Workbook()
    ws = wb.active
    ws.title = sheet
    ws.append([""] + [x for lab in labels for x in (lab, None, None, None)])
    ws.append(["Label"] + list(HEADERS) * len(labels))
    for row in rows:
        ws.append(list(row))
    wb.save(path)
    return path


HOUSE_LABELS = ["State House District 1 (2024); Hawaii", "State House District 2 (2024); Hawaii"]
ROWS = [
    ("EMPLOYMENT STATUS",),
    ("Population 16 years and over", "8,619", "±436", "8,619", "(X)", "22,938", "±1,225", "22,938", "(X)"),
    ("In labor force", "12,471", "±1,063", "59.3%", "±2.8", "-", "**", "-", "**"),
    ("Unemployment Rate", "(X)", "(X)", "6.2%", "±1.7", "(X)", "(X)", "12.5%", "±2.6"),
]


@pytest.fixture
def house_mini(tmp_path):
    return make_workbook(tmp_path / "house.xlsx", HOUSE_LABELS, ROWS)


def row(df, geo_id, label):
    sel = df[(df.geo_id == geo_id) & (df.label == label)]
    assert len(sel) == 1
    return sel.iloc[0]


def test_geo_ids_from_labels(house_mini):
    df = parse_dbedt_profile(house_mini, "house")
    assert set(zip(df.geo_type, df.geo_id)) == {("house", "001"), ("house", "002")}


def test_values_and_moe_parsed(house_mini):
    df = parse_dbedt_profile(house_mini, "house")
    r = row(df, "001", "Population 16 years and over")
    assert (r.estimate, r.estimate_status, r.moe, r.moe_status) == (8619.0, "ok", 436.0, "ok")
    assert pd.isna(r.percent)  # universe row repeats the count in the percent column
    assert r.sheet == "DP03"
    assert r.row_no == 4
    r = row(df, "001", "In labor force")
    assert (r.percent, r.percent_moe) == (59.3, 2.8)
    r = row(df, "002", "Unemployment Rate")
    assert (r.percent, r.percent_moe) == (12.5, 2.6)
    assert r.estimate_status == "not_applicable"


def test_mismatching_bare_percent_raises(tmp_path):
    rows = [("S",), ("Total", "8,619", "±436", "8,000", "(X)")]
    p = make_workbook(tmp_path / "x.xlsx", HOUSE_LABELS[:1], rows)
    with pytest.raises(ValueError, match=r"sheet DP03 row 4 geo 001"):
        parse_dbedt_profile(p, "house")


def test_section_rows_kept_as_section_not_facts(house_mini):
    df = parse_dbedt_profile(house_mini, "house")
    assert "EMPLOYMENT STATUS" not in set(df.label)
    assert set(df.section) == {"EMPLOYMENT STATUS"}
    assert len(df) == 6


def test_suppressed_cell_is_null_not_zero(house_mini):
    df = parse_dbedt_profile(house_mini, "house")
    r = row(df, "002", "In labor force")
    assert pd.isna(r.estimate) and r.estimate_status == "suppressed"
    assert pd.isna(r.moe) and r.moe_status == "not_computable"
    assert pd.isna(r.percent) and pd.isna(r.percent_moe)


def test_columns(house_mini):
    df = parse_dbedt_profile(house_mini, "house")
    assert list(df.columns) == [
        "geo_type", "geo_id", "sheet", "row_no", "label", "section",
        "estimate", "estimate_status", "moe", "moe_status", "percent", "percent_moe",
        "percent_status", "percent_moe_status",
    ]  # fmt: skip


def test_percent_statuses(house_mini):
    df = parse_dbedt_profile(house_mini, "house")
    r = row(df, "001", "Population 16 years and over")
    assert (r.percent_status, r.percent_moe_status) == ("universe_repeat", "not_applicable")
    r = row(df, "001", "In labor force")
    assert (r.percent_status, r.percent_moe_status) == ("ok", "ok")
    r = row(df, "002", "In labor force")
    assert (r.percent_status, r.percent_moe_status) == ("suppressed", "not_computable")


def test_open_ended_moe_marker(tmp_path):
    rows = [("S",), ("Median (dollars)", "4,000+", "***", "(X)", "(X)")]
    df = parse_dbedt_profile(make_workbook(tmp_path / "x.xlsx", HOUSE_LABELS[:1], rows), "house")
    r = df.iloc[0]
    assert (r.estimate, r.estimate_status, r.moe_status) == (4000.0, "top_coded", "open_ended")


def test_county_and_state_labels(tmp_path):
    rows = [("S",), ("Total", "1", "±1", "1", "(X)", "2", "±2", "2", "(X)")]
    labels = ["Kauai County, Hawaii", "Kalawao County, Hawaii"]
    df = parse_dbedt_profile(make_workbook(tmp_path / "c.xlsx", labels, rows), "county")
    assert set(zip(df.geo_type, df.geo_id)) == {("county", "15007"), ("county", "15005")}
    rows = [("S",), ("Total", "1", "±1", "1", "(X)")]
    df = parse_dbedt_profile(make_workbook(tmp_path / "s.xlsx", ["Hawaii"], rows), "state")
    assert set(zip(df.geo_type, df.geo_id)) == {("state", "15")}


def test_wrong_geo_type_raises(house_mini):
    with pytest.raises(ValueError, match="State House District 1"):
        parse_dbedt_profile(house_mini, "senate")


def test_bad_subheader_raises(tmp_path):
    p = make_workbook(tmp_path / "x.xlsx", HOUSE_LABELS[:1], ROWS[:1])
    wb = openpyxl.load_workbook(p)
    wb.active["C2"] = "Wrong"
    wb.save(p)
    with pytest.raises(ValueError, match="column"):
        parse_dbedt_profile(p, "house")


def test_unknown_cell_raises(tmp_path):
    rows = [("S",), ("Total", "garbage", "±1", "1", "(X)")]
    p = make_workbook(tmp_path / "x.xlsx", HOUSE_LABELS[:1], rows)
    with pytest.raises(ValueError, match="garbage"):
        parse_dbedt_profile(p, "house")


@pytest.mark.network
def test_real_house_file_has_51_districts():
    df = parse_dbedt_profile(fetch(DBEDT_URLS["house"], "dbedt/acs2024_5yr_house_hi.xlsx"), "house")
    assert df.geo_id.nunique() == 51
    assert set(df.geo_id) == {f"{i:03d}" for i in range(1, 52)}
    assert set(df.sheet) == {"DP02", "DP03", "DP04", "DP05"}


@pytest.mark.network
def test_real_senate_file_has_25_districts():
    df = parse_dbedt_profile(fetch(DBEDT_URLS["senate"], "dbedt/acs2024_5yr_senate_hi.xlsx"), "senate")
    assert set(df.geo_id) == {f"{i:03d}" for i in range(1, 26)}


@pytest.mark.network
def test_real_county_and_state_files():
    df = parse_dbedt_profile(fetch(DBEDT_URLS["county"], "dbedt/acs2024_5yr_counties_hi.xlsx"), "county")
    assert set(df.geo_id) == {"15001", "15003", "15005", "15007", "15009"}
    df = parse_dbedt_profile(fetch(DBEDT_URLS["state"], "dbedt/acs2024_5yr_state_hi.xlsx"), "state")
    assert set(df.geo_id) == {"15"}
    r = df[(df.sheet == "DP03") & (df.label == "Population 16 years and over")].iloc[0]
    assert (r.estimate, r.moe) == (1178236.0, 713.0)
