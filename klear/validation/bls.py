# Checks BLS county unemployment: every series has the month, rates agree with counts, counties add to the state.
import pandas as pd

from klear.etl.bls import AREAS, MEASURES
from klear.validation import CheckResult

RATE, UNEMPLOYED, LABOR_FORCE = (MEASURES[m][0] for m in ("03", "04", "06"))
# published rates are rounded to 0.1 points
RATE_TOLERANCE = 0.05 + 1e-9
# county counts may differ from the state count by rounding; Kalawao has no series
SUM_TOLERANCE = 0.005
PLACES = {"15": "Hawaiʻi", "15001": "Hawaiʻi County", "15003": "Honolulu County", "15007": "Kauaʻi County",
          "15009": "Maui County"}  # fmt: skip
WORDS = {LABOR_FORCE: "labor force", UNEMPLOYED: "unemployed people"}


def check_bls(rows: pd.DataFrame, vintage: str) -> CheckResult:
    details: list[dict] = []
    have = set(zip(rows.geo_type, rows.geo_id, rows.indicator_id, strict=True))
    want = {(*geo, MEASURES[m][0]) for geo in AREAS for m in MEASURES}
    details += [
        {"check": "missing", "geo_type": t, "geo_id": g, "indicator": i} for t, g, i in sorted(want - have)
    ]
    wide = rows.pivot_table(index=["geo_type", "geo_id"], columns="indicator_id", values="value")
    for (geo_type, geo_id), r in wide.iterrows():
        if r.isna().any() or not r[LABOR_FORCE]:
            continue
        computed = 100 * r[UNEMPLOYED] / r[LABOR_FORCE]
        if abs(computed - r[RATE]) > RATE_TOLERANCE:
            details.append(
                {"check": "rate", "geo_type": geo_type, "geo_id": geo_id, "published": r[RATE],
                 "computed": round(computed, 3)}
            )  # fmt: skip
    notes = [f"month: {vintage}, the latest month every series has, not seasonally adjusted"]
    counties = wide.loc["county"] if "county" in wide.index.get_level_values(0) else wide.iloc[:0]
    for ind in (LABOR_FORCE, UNEMPLOYED):
        if ("state", "15") not in wide.index or ind not in wide:
            continue
        state, total = float(wide.loc[("state", "15"), ind]), float(counties[ind].sum())
        notes.append(
            f"{WORDS[ind]}: the four counties add to {total:,.0f}, the state estimate is {state:,.0f}"
        )
        if abs(total - state) > SUM_TOLERANCE * state:
            details.append({"check": "county_sum", "indicator": ind, "state": state, "counties": total})
    if RATE in wide:
        notes.append(
            "unemployment rate: "
            + ", ".join(f"{PLACES.get(g, g)} {v:.1f}%" for (_, g), v in wide[RATE].items() if pd.notna(v))
        )
    return CheckResult("bls", not details, True, len(want), len(details), details, notes)
