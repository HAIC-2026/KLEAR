# Validation 9: published HCV source total equals placed district total plus unplaced tracts (gated).
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
    return CheckResult("hud_hcv", not details, True, 2, len(details), details, notes)
