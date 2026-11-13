# Checks Department of Taxation credit tables: every district and credit is present and districts add to the state.
import pandas as pd

from klear.etl.dotax import HEADERS, credit_indicator_rows
from klear.validation import CheckResult

# the tables say details may not add to totals because of rounding
TOLERANCE = 0.001


def check_dotax(df: pd.DataFrame) -> CheckResult:
    details: list[dict] = []
    notes: list[str] = []
    geos = [geo for _, geo in HEADERS]
    labels = dict(credit_indicator_rows()[["indicator_id", "label"]].itertuples(index=False, name=None))
    for ind, g in df.groupby("indicator_id"):
        by_geo = {
            (t, i): (v, s) for t, i, v, s in zip(g.geo_type, g.geo_id, g.value, g.value_status, strict=True)
        }
        details += [
            {"check": "missing", "indicator": ind, "geo_type": t, "geo_id": i}
            for t, i in geos
            if (t, i) not in by_geo
        ]
        if ("state", "15") not in by_geo or any(by_geo.get(geo, (None, ""))[1] != "ok" for geo in geos):
            notes.append(
                f"{labels.get(ind, ind)}: not summed, a district or the state is suppressed or missing"
            )
            continue
        state = by_geo[("state", "15")][0]
        total = sum(by_geo[geo][0] for geo in geos if geo[0] == "county")
        notes.append(
            f"{labels.get(ind, ind)}: the four taxation districts add to {total:,.0f}, the state total is {state:,.0f}"
        )
        if abs(total - state) > TOLERANCE * state:
            details.append({"check": "district_sum", "indicator": ind, "state": state, "districts": total})
    n = int(df.indicator_id.nunique())
    return CheckResult("dotax", not details, True, n * len(geos), len(details), details, notes)
