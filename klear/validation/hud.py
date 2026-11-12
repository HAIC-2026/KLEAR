# Validation 9: published HCV total equals the district totals plus unplaced tracts, and the state total (gated).
import pandas as pd

from klear.etl.hud import INDICATOR
from klear.validation import CheckResult

TOLERANCE = 1e-6


def check_hcv(facts: pd.DataFrame) -> CheckResult:
    details: list[dict] = []
    notes: list[str] = []
    unplaced = facts.attrs.get("unplaced")
    published = facts.attrs.get("published", {}).get(INDICATOR)
    if unplaced is None or published is None:
        problem = "no unplaced tract or published total record on the facts"
        return CheckResult("hud_hcv", False, True, 0, 1, [{"check": "attrs", "problem": problem}], [problem])
    ind = facts[facts.indicator_id == INDICATOR]
    for chamber in ("house", "senate"):
        placed = float(ind[ind.geo_type == chamber].value.sum())
        lost = unplaced.get(chamber, {}).get(INDICATOR, {})
        total = float(sum(lost.values()))
        notes.append(
            f"{chamber}: published tracts {published:,.0f}, placed in districts {placed:,.0f}, "
            f"unplaced {total:,.0f} in {len(lost)} tracts with no population or not in the block table"
        )
        if abs(published - (placed + total)) > TOLERANCE * max(published, 1.0):
            details.append(
                {
                    "check": "total",
                    "chamber": chamber,
                    "published": published,
                    "placed": placed,
                    "unplaced": total,
                }
            )
    state = ind[ind.geo_type == "state"]
    off = float(facts.attrs.get("off_table", {}).get(INDICATOR, 0.0))
    if state.empty:
        details.append({"check": "no_state_total"})
    else:
        summed = float(state.value.iloc[0])
        notes.append(
            f"state: published tracts {published:,.0f}, summed to the state {summed:,.0f}, in tracts not in the "
            f"block table {off:,.0f}"
        )
        if abs(published - (summed + off)) > TOLERANCE * max(published, 1.0):
            details.append(
                {"check": "state_total", "published": published, "state": summed, "off_table": off}
            )
    return CheckResult("hud_hcv", not details, True, 3, len(details), details, notes)
