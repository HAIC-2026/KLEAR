# Fetches BLS Local Area Unemployment Statistics for Hawaiʻi counties and the state as county-level facts.
import json
from pathlib import Path

import pandas as pd

from klear.etl.fetch import fetch
from klear.store.db import CONTEXT_TIER, FACT_COLS

API_URL = "https://api.bls.gov/publicAPI/v1/timeseries/data/"
SOURCE_ID = "bls_laus_county"
RAW_NAME = "bls/laus_hi.json"
NAME = "Local Area Unemployment Statistics, monthly county and state estimates (county-level)"
PUBLISHER = "U.S. Bureau of Labor Statistics"
PAGE_URL = "https://www.bls.gov/lau/"
SERIES_URL = "https://data.bls.gov/timeseries/{}"
# store geography and its LAUS area code; BLS publishes no Kalawao County series
AREAS = {
    ("state", "15"): "ST1500000000000",
    ("county", "15001"): "CN1500100000000",
    ("county", "15003"): "CN1500300000000",
    ("county", "15007"): "CN1500700000000",
    ("county", "15009"): "CN1500900000000",
}
KALAWAO = {("county", "15005"): "BLS publishes no Kalawao County estimates"}
# LAUS measure code: indicator, unit, label
MEASURES = {
    "03": ("bls_unemployment_rate", "percent", "Unemployment rate (county-level, monthly)"),
    "04": ("bls_unemployed", "count", "Unemployed people (county-level, monthly)"),
    "06": ("bls_labor_force", "count", "Civilian labor force (county-level, monthly)"),
}
UNIVERSE = "Civilian noninstitutional population 16 and over, by place of residence, not seasonally adjusted"


class Unavailable(RuntimeError):
    pass


def series_id(area: str, measure: str) -> str:
    # not seasonally adjusted (U), as BLS publishes county estimates
    return f"LAU{area}{measure}"


SERIES = {
    series_id(area, m): (geo_type, geo_id, MEASURES[m][0])
    for (geo_type, geo_id), area in AREAS.items()
    for m in MEASURES
}


def series_url(sid: str) -> str:
    return SERIES_URL.format(sid)


def _reply(path: Path) -> dict:
    d = json.loads(path.read_text(encoding="utf-8"))
    if d.get("status") != "REQUEST_SUCCEEDED":
        raise Unavailable(
            f"BLS API did not answer: {d.get('status')} {' '.join(d.get('message') or [])}".strip()
        )
    got = {s.get("seriesID"): s.get("data") for s in d.get("Results", {}).get("series", [])}
    empty = sorted(s for s in SERIES if not got.get(s))
    if empty:
        raise Unavailable(f"BLS API returned no data for {', '.join(empty)}")
    return d


def fetch_laus(*, refresh: bool = False) -> Path:
    return fetch(API_URL, RAW_NAME, refresh=refresh, json_body={"seriesid": list(SERIES)}, validate=_reply)


def parse_laus(path: Path) -> pd.DataFrame:
    """One row per series and month: geography, indicator, year, month, value and the preliminary flag."""
    rows = []
    for s in _reply(path)["Results"]["series"]:
        if s["seriesID"] not in SERIES:
            raise ValueError(f"unexpected BLS series {s['seriesID']}")
        geo_type, geo_id, indicator = SERIES[s["seriesID"]]
        for d in s["data"]:
            # skip annual averages and months BLS did not publish ("-", such as October 2025)
            if not d["period"].startswith("M") or d["period"] == "M13" or d["value"].strip() == "-":
                continue
            try:
                value = float(d["value"])
            except ValueError:
                raise ValueError(f"{s['seriesID']} {d['year']} {d['period']}: value {d['value']!r}") from None
            rows.append(
                {
                    "series_id": s["seriesID"], "geo_type": geo_type, "geo_id": geo_id, "indicator_id": indicator,
                    "year": int(d["year"]), "month": int(d["period"][1:]), "month_name": d["periodName"],
                    "value": value,
                    "preliminary": any(f.get("code") == "P" for f in d.get("footnotes") or []),
                }
            )  # fmt: skip
    df = pd.DataFrame(rows)
    if df.duplicated(["series_id", "year", "month"]).any():
        raise ValueError("BLS reply repeats a series month")
    return df


def latest(df: pd.DataFrame) -> pd.DataFrame:
    """Rows for the latest month every series has."""
    months = [set(zip(g.year, g.month, strict=True)) for _, g in df.groupby("series_id")]
    common = set.intersection(*months) if months else set()
    if not common:
        raise ValueError("BLS series share no month")
    year, month = max(common)
    return df[(df.year == year) & (df.month == month)].reset_index(drop=True)


def vintage(rows: pd.DataFrame) -> str:
    """Like 'September 2026 (preliminary)'."""
    text = f"{rows.month_name.iloc[0]} {rows.year.iloc[0]}"
    return f"{text} (preliminary)" if rows.preliminary.any() else text


def laus_indicator_rows() -> pd.DataFrame:
    rows = [
        {"indicator_id": i, "label": label, "unit": unit, "universe": UNIVERSE, "acs_var": None,
         "additive": unit == "count"}
        for i, unit, label in MEASURES.values()
    ]  # fmt: skip
    return pd.DataFrame(rows)


def laus_facts(rows: pd.DataFrame) -> pd.DataFrame:
    out = pd.DataFrame(
        {
            "geo_type": rows.geo_type, "geo_id": rows.geo_id, "indicator_id": rows.indicator_id,
            "value": rows.value, "moe": float("nan"), "value_status": "ok", "moe_status": "not_applicable",
            "tier": CONTEXT_TIER, "source_id": SOURCE_ID, "table_id": rows.series_id,
            "value_low": float("nan"), "value_high": float("nan"),
        }
    )  # fmt: skip
    return out[FACT_COLS]
