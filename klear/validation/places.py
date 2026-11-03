# Validation 12: PLACES tract counts and ranges hold, with exclusions and gaps listed (gated).
import pandas as pd

from klear.etl.places import PLACES_MEASURES
from klear.validation import CheckResult

EXPECTED_TRACTS = 426


def check_places(df: pd.DataFrame, facts: pd.DataFrame) -> CheckResult:
    for key in ("excluded", "no_fact", "uncovered"):
        if key not in facts.attrs:
            problem = f"no {key} record on the facts"
            return CheckResult(
                "places", False, True, 0, 1, [{"check": "attrs", "problem": problem}], [problem]
            )
    details: list[dict] = []
    notes: list[str] = []
    dup = df[df.duplicated(["src_id", "measureid"], keep=False)]
    for r in dup.drop_duplicates(["src_id", "measureid"]).itertuples():
        details.append({"check": "duplicate", "geo_id": r.src_id, "measureid": r.measureid})
    n_tract = int((facts.geo_type == "tract").sum())
    want = len(df) - facts.attrs.get("off_block_rows", 0)
    if n_tract != want:
        details.append({"check": "tract_count", "facts": n_tract, "expected": want})
    covered = set(facts[facts.geo_type == "tract"].geo_id)
    for mid, ind in PLACES_MEASURES.items():
        have = set(df[df.measureid == mid].src_id)
        if len(have) != EXPECTED_TRACTS:
            miss = sorted((covered | set(facts.attrs["uncovered"])) - have)
            notes.append(
                f"{ind}: {len(have)} tracts, not {EXPECTED_TRACTS}, missing {', '.join(miss) or 'none known'}"
            )
        out = facts.attrs["excluded"].get(ind, [])
        if out:
            notes.append(
                f"{ind}: suppressed, left out of district means, widen district ranges: {', '.join(out)}"
            )
        for chamber, ids in facts.attrs["no_fact"].get(ind, {}).items():
            if ids:
                notes.append(f"{ind}: {chamber} districts without a fact: {', '.join(ids)}")
    if facts.attrs["uncovered"]:
        notes.append(
            f"tracts with 2020 population missing from PLACES: {', '.join(facts.attrs['uncovered'])}"
        )
    live = facts[facts.value.notna()]
    bad = live[~((live.value_low <= live.value) & (live.value <= live.value_high))]
    for r in bad.itertuples():
        details.append(
            {"check": "range", "geo_type": r.geo_type, "geo_id": r.geo_id, "indicator_id": r.indicator_id}
        )
    return CheckResult("places", not details, True, len(live), len(details), details, notes)
