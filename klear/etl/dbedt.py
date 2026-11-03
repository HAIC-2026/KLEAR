# Parses DBEDT ACS 5-year district, county and state profile workbooks into tidy rows.
import re
from pathlib import Path

import openpyxl
import pandas as pd

from klear.etl.acs_values import parse_estimate, parse_moe
from klear.etl.fetch import fetch

BASE_URL = "https://files.hawaii.gov/dbedt/census/acs/acs2024/5yr/"
DBEDT_URLS = {
    "house": BASE_URL + "acs2024_5yr_house_hi.xlsx",
    "senate": BASE_URL + "acs2024_5yr_senate_hi.xlsx",
    "county": BASE_URL + "acs2024_5yr_counties_hi.xlsx",
    "state": BASE_URL + "acs2024_5yr_state_hi.xlsx",
}
SUBHEADERS = ("Estimate", "Margin of Error", "Percent", "Percent Margin of Error")
COLUMNS = [
    "geo_type", "geo_id", "sheet", "row_no", "label", "section",
    "estimate", "estimate_status", "moe", "moe_status", "percent", "percent_moe",
    "percent_status", "percent_moe_status",
]  # fmt: skip
COUNTY_FIPS = {
    "Hawaii County, Hawaii": "15001",
    "Honolulu County, Hawaii": "15003",
    "Kalawao County, Hawaii": "15005",
    "Kauai County, Hawaii": "15007",
    "Maui County, Hawaii": "15009",
}
_DISTRICT_RES = {
    "house": re.compile(r"State House District (\d{1,2}) \(2024\); Hawaii"),
    "senate": re.compile(r"State Senate District (\d{1,2}) \(2024\); Hawaii"),
}


def _geo_id(label: str, geo_type: str) -> str:
    if geo_type in _DISTRICT_RES:
        m = _DISTRICT_RES[geo_type].fullmatch(label)
        if m:
            return f"{int(m.group(1)):03d}"
    elif geo_type == "county" and label in COUNTY_FIPS:
        return COUNTY_FIPS[label]
    elif geo_type == "state" and label == "Hawaii":
        return "15"
    raise ValueError(f"unexpected {geo_type} column label: {label!r}")


def _percent(raw, estimate_raw, where: str) -> tuple[float | None, str]:
    text = None if raw is None else str(raw).strip()
    if text is not None and text.endswith("%"):
        return parse_estimate(text[:-1])
    if text is None or text in ("(X)", "-", "N"):
        return parse_estimate(text)
    if text == str(estimate_raw).strip():
        return None, "universe_repeat"
    raise ValueError(f"{where}: bare percent {text!r} differs from estimate {estimate_raw!r}")


def _blank(v) -> bool:
    return v is None or (isinstance(v, str) and not v.strip())


def _parse_sheet(ws, geo_type: str) -> list[dict]:
    rows = list(ws.iter_rows(values_only=True))
    if len(rows) < 2:
        raise ValueError(f"sheet {ws.title}: missing header rows")
    top = [c for c in rows[0][1:]]
    sub = [c for c in rows[1][1:]]
    while sub and _blank(sub[-1]) and _blank(top[-1]):
        sub.pop()
        top.pop()
    if rows[1][0] != "Label" or not sub or len(sub) % 4:
        raise ValueError(f"sheet {ws.title}: unexpected header layout")
    geos = []
    for i in range(0, len(sub), 4):
        if tuple(sub[i : i + 4]) != SUBHEADERS:
            raise ValueError(
                f"sheet {ws.title}: unexpected column headers at block {i // 4}: {sub[i : i + 4]}"
            )
        if _blank(top[i]) or any(not _blank(c) for c in top[i + 1 : i + 4]):
            raise ValueError(f"sheet {ws.title}: bad geography label in column block {i // 4}")
        geos.append(_geo_id(str(top[i]).strip(), geo_type))
    if len(set(geos)) != len(geos):
        raise ValueError(f"sheet {ws.title}: duplicate geography labels")
    out = []
    section = None
    for row_no, r in enumerate(rows[2:], start=3):
        label, cells = r[0], list(r[1 : 1 + len(sub)])
        cells += [None] * (len(sub) - len(cells))
        if _blank(label):
            if all(_blank(c) for c in cells) and all(_blank(c) for c in r[1 + len(sub) :]):
                continue
            raise ValueError(f"sheet {ws.title} row {row_no}: values without a label")
        label = str(label).strip()
        if all(_blank(c) for c in cells):
            section = label
            continue
        for k, geo_id in enumerate(geos):
            est, moe, pct, pct_moe = cells[4 * k : 4 * k + 4]
            e_val, e_status = parse_estimate(est)
            m_val, m_status = parse_moe(moe)
            p_val, p_status = _percent(pct, est, f"sheet {ws.title} row {row_no} geo {geo_id}")
            pm_val, pm_status = parse_moe(pct_moe)
            out.append(
                {
                    "geo_type": geo_type, "geo_id": geo_id, "sheet": ws.title, "row_no": row_no,
                    "label": label, "section": section,
                    "estimate": e_val, "estimate_status": e_status,
                    "moe": m_val, "moe_status": m_status,
                    "percent": p_val, "percent_moe": pm_val,
                    "percent_status": p_status, "percent_moe_status": pm_status,
                }
            )  # fmt: skip
    return out


def parse_dbedt_profile(path: Path, geo_type: str) -> pd.DataFrame:
    """Read a DBEDT ACS 2020-2024 5-year profile workbook into one row per geography and line.

    Layout seen on 2026-10-29 in all four files (house, senate, counties, state):
    - Sheets DP02, DP03, DP04, DP05 (174, 147, 162, 115 rows). Sheet order varies (senate is
      DP02, DP04, DP03, DP05), so sheets are read by name, never by position.
    - Row 1: blank A1, then the geography label above the first column of each 4-column block,
      other cells of the block None. Row 2: "Label" then Estimate, Margin of Error, Percent,
      Percent Margin of Error repeated per block. Data starts in row 3. Blocks: house 51 (205
      columns), senate 25 (101), counties 5 (21), state 1 (5).
    - Geography labels: "State House District 1 (2024); Hawaii" (house, id "001"),
      "State Senate District 1 (2024); Hawaii" (senate, id "001"); counties "Hawaii County, Hawaii",
      "Honolulu County, Hawaii", "Kalawao County, Hawaii", "Kauai County, Hawaii" (no okina),
      "Maui County, Hawaii" -> 15001, 15003, 15005, 15007, 15009; state "Hawaii" -> "15".
    - Every cell is text. Estimates look like "8,619", MOEs like "±436", percents like "59.3%"
      (trailing percent sign stripped here), percent MOEs like "±2.8". Universe rows repeat the
      count in the Percent column (e.g. "21,045"); those percents are null with status
      "universe_repeat". Markers seen: "(X)", "-", "N",
      "**", "***", "*****", and open-ended estimates like "4,000+".
    - Section header rows (e.g. "EMPLOYMENT STATUS", 48 per file) have only column A filled. They
      become the section of the following rows and are not emitted as facts.
    - Labels have no indentation and repeat within a sheet, so row_no (1-based sheet row) is the
      row key. percent_status and percent_moe_status carry the parsed status of those cells.
    Unknown cell text raises ValueError.
    """
    wb = openpyxl.load_workbook(path, read_only=True, data_only=True)
    try:
        records = []
        for ws in wb.worksheets:
            records += _parse_sheet(ws, geo_type)
    finally:
        wb.close()
    df = pd.DataFrame(records, columns=COLUMNS)
    for col in ("estimate", "moe", "percent", "percent_moe"):
        df[col] = pd.to_numeric(df[col], errors="coerce").astype("float64")
    return df


def fetch_dbedt(geo_type: str, *, refresh: bool = False) -> Path:
    url = DBEDT_URLS[geo_type]
    return fetch(url, "dbedt/" + url.rsplit("/", 1)[1], refresh=refresh)


def dbedt_geo_names(path: Path, geo_type: str) -> dict[str, str]:
    # map geo_id to the geography label in row 1 of the first sheet
    wb = openpyxl.load_workbook(path, read_only=True, data_only=True)
    try:
        top = next(wb.worksheets[0].iter_rows(min_row=1, max_row=1, values_only=True))
    finally:
        wb.close()
    labels = [str(c).strip() for c in top[1:] if not _blank(c)]
    return {_geo_id(label, geo_type): label for label in labels}
