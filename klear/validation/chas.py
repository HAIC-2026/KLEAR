# Validation 13: published CHAS source totals equal placed district totals plus unplaced tracts (gated).
import pandas as pd

from klear.etl.chas import CHAS_INDICATORS
from klear.validation import CheckResult

TOLERANCE = 1e-6
# HUD CHAS query tool, Hawaiʻi state summary for 2018-2022, "Income by Cost Burden" tables:
# households at or below 80% HAMFI, summed over the three income bands.
HUD_STATE = {
    "chas_renter_lowinc_burdened": 29_435 + 23_980 + 23_435,
    "chas_owner_lowinc_burdened": 17_665 + 13_685 + 21_320,
    "chas_renter_lowinc": 40_855 + 29_950 + 41_130,
    "chas_owner_lowinc": 25_370 + 25_115 + 47_870,
}
# tract values are rounded, so their sum differs slightly from the state estimate
STATE_TOLERANCE = 0.005


def check_chas(facts: pd.DataFrame) -> CheckResult:
    unplaced = facts.attrs.get("unplaced")
    published_all = facts.attrs.get("published")
    if unplaced is None or published_all is None:
        problem = "no unplaced tract or published total record on the facts"
        return CheckResult("chas", False, True, 0, 1, [{"check": "unplaced", "problem": problem}], [problem])
    details: list[dict] = []
    notes: list[str] = []
    for ind in CHAS_INDICATORS:
        rows = facts[facts.indicator_id == ind]
        published = float(published_all.get(ind, 0.0))
        for chamber in ("house", "senate"):
            placed = float(rows[rows.geo_type == chamber].value.sum())
            lost = unplaced.get(chamber, {}).get(ind, {})
            total = float(sum(lost.values()))
            notes.append(
                f"{ind} {chamber}: published tracts {published:,.0f}, placed in districts {placed:,.0f}, "
                f"unplaced {total:,.0f} in {len(lost)} tracts with no population or not in the block table"
            )
            if abs(published - (placed + total)) > TOLERANCE * max(published, 1.0):
                details.append(
                    {
                        "check": "total",
                        "indicator_id": ind,
                        "chamber": chamber,
                        "published": published,
                        "placed": placed,
                        "unplaced": total,
                    }
                )
    hawaii = facts.attrs.get("hawaii")
    if hawaii is None:
        details.append({"check": "state", "problem": "no Hawaiʻi tract totals on the facts"})
    else:
        for ind, want in HUD_STATE.items():
            got = float(hawaii.get(ind, 0.0))
            notes.append(f"{ind}: Hawaiʻi tracts sum to {got:,.0f}, HUD state summary {want:,}")
            if abs(got - want) > STATE_TOLERANCE * want:
                details.append({"check": "state", "indicator_id": ind, "tracts": got, "hud_state": want})
    live = facts[facts.value.notna() & (facts.tier == "A")]
    bad = live[~((live.value_low <= live.value) & (live.value <= live.value_high))]
    for r in bad.itertuples():
        details.append({"check": "range", "geo_id": r.geo_id, "indicator_id": r.indicator_id})
    for n in facts.attrs.get("uncovered", []):
        notes.append(f"tract with 2020 population missing from CHAS: {n}")
    return CheckResult("chas", not details, True, len(facts), len(details), details, notes)
