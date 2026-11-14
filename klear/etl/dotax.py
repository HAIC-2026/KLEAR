# Parses the Hawaiʻi Department of Taxation's tax credit tables by taxation district into county-level facts.
from pathlib import Path

import openpyxl
import pandas as pd

from klear.etl.fetch import fetch
from klear.store.db import CONTEXT_TIER, FACT_COLS

URL = "https://files.hawaii.gov/tax/stats/stats/credits/2023credit_tables.xlsx"
REPORT_URL = "https://files.hawaii.gov/tax/stats/stats/credits/2023credit.pdf"
PAGE_URL = "https://tax.hawaii.gov/stats/a5_1annual/a5_4credits/"
RAW_NAME = "dotax/2023credit_tables.xlsx"
SOURCE_ID = "hi_dotax_credits_2023"
NAME = "Tax Credits Claimed by Hawaiʻi Taxpayers, tables by taxation district (county-level)"
PUBLISHER = "Hawaiʻi Department of Taxation"
VINTAGE = "Tax year 2023"
# sheet: what it counts and the multiplier to the stored unit (A-3 is in thousands of dollars)
SHEETS = {"A-4": ("claims", 1), "A-3": ("dollars", 1000)}
# the two header rows above each taxation district column, and the store geography it is shown as
HEADERS = (
    (("STATE", "TOTAL"), ("state", "15")),
    (("OAHU*", "(DISTRICT 1)"), ("county", "15003")),
    (("MAUI", "(DISTRICT 2)"), ("county", "15009")),
    (("HAWAII", "(DISTRICT 3)"), ("county", "15001")),
    (("KAUAI", "(DISTRICT 4)"), ("county", "15007")),
)
KALAWAO = {
    (
        "county",
        "15005",
    ): "the Department of Taxation publishes four taxation districts, none of them Kalawao County"
}
# row label in the tables: indicator stem and reader name
CREDITS = {
    "Earned Income Tax Credit": ("eitc", "Hawaiʻi earned income tax credit"),
    "Refundable Food/Excise Tax Credit": ("food_excise", "Refundable food/excise tax credit"),
    "Tax Credit for Low-Income Household Renters": ("renters", "Low-income household renters credit"),
}
UNIVERSE = (
    "Individual income tax returns by taxation district. Oʻahu includes nonresident claims with an out-of-state "
    "address"
)


def fetch_credit_tables(*, refresh: bool = False) -> Path:
    return fetch(URL, RAW_NAME, refresh=refresh)


def indicator_id(stem: str, kind: str) -> str:
    return f"dotax_{stem}_{kind}"


def _text(v) -> str:
    return " ".join(str(v).split()) if v is not None else ""


def _value(v, where: str) -> tuple[float | None, str]:
    if isinstance(v, int | float) and not isinstance(v, bool):
        return float(v), "ok"
    if _text(v) == "d":
        return None, "suppressed"
    raise ValueError(f"{where}: unexpected cell {v!r}")


def _sheet(ws, kind: str, scale: int) -> list[dict]:
    rows = list(ws.iter_rows(values_only=True))
    want = [tuple(h) for h, _ in HEADERS]
    top = next(
        (
            i
            for i in range(len(rows) - 1)
            if list(zip(map(_text, rows[i][1:6]), map(_text, rows[i + 1][1:6]))) == want
        ),
        None,
    )
    if top is None:
        raise ValueError(f"sheet {ws.title}: taxation district header rows not found")
    out, seen = [], set()
    for row_no, r in enumerate(rows[top + 2 :], start=top + 3):
        label = _text(r[0])
        if label not in CREDITS:
            continue
        if label in seen:
            raise ValueError(f"sheet {ws.title}: {label!r} appears twice")
        seen.add(label)
        stem, _ = CREDITS[label]
        for k, (_, (geo_type, geo_id)) in enumerate(HEADERS, start=1):
            value, status = _value(r[k], f"sheet {ws.title} row {row_no} column {k + 1}")
            out.append(
                {
                    "geo_type": geo_type, "geo_id": geo_id, "indicator_id": indicator_id(stem, kind),
                    "value": None if value is None else round(value * scale), "value_status": status,
                    "table_id": ws.title,
                }
            )  # fmt: skip
    missing = sorted(set(CREDITS) - seen)
    if missing:
        raise ValueError(f"sheet {ws.title}: rows missing for {missing}")
    return out


def parse_credit_tables(path: Path) -> pd.DataFrame:
    """Claims (table A-4) and dollars (table A-3) of three credits for the state and each taxation district."""
    wb = openpyxl.load_workbook(path, read_only=True, data_only=True)
    try:
        missing = sorted(set(SHEETS) - set(wb.sheetnames))
        if missing:
            raise ValueError(f"sheets missing: {missing}")
        rows = [r for name, (kind, scale) in SHEETS.items() for r in _sheet(wb[name], kind, scale)]
    finally:
        wb.close()
    df = pd.DataFrame(rows)
    df["value"] = df["value"].astype("float64")
    return df


def credit_indicator_rows() -> pd.DataFrame:
    rows = []
    for stem, name in CREDITS.values():
        rows.append(
            {"indicator_id": indicator_id(stem, "claims"), "label": f"{name} claims (county-level)",
             "unit": "count", "universe": UNIVERSE, "acs_var": None, "additive": True}
        )  # fmt: skip
        rows.append(
            {"indicator_id": indicator_id(stem, "dollars"), "label": f"{name} dollars claimed (county-level)",
             "unit": "dollars", "universe": UNIVERSE, "acs_var": None, "additive": True}
        )  # fmt: skip
    return pd.DataFrame(rows)


def credit_facts(df: pd.DataFrame) -> pd.DataFrame:
    out = df.assign(
        moe=float("nan"), moe_status="not_applicable", tier=CONTEXT_TIER, source_id=SOURCE_ID,
        value_low=float("nan"), value_high=float("nan"),
    )  # fmt: skip
    return out[FACT_COLS].reset_index(drop=True)
