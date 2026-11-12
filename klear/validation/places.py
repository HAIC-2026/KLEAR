# Validation 12: PLACES tract counts and ranges hold, and county values and the derived state are right (gated).
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


def check_places_county(
    df: pd.DataFrame, facts: pd.DataFrame, counties: list[str], measures: dict[str, str] = PLACES_MEASURES
) -> CheckResult:
    # each county and measure is published once and stored as published; the state is their adult-weighted mean
    details: list[dict] = []
    notes: list[str] = []
    rows = df[df.measureid.isin(measures) & df.src_id.isin(counties)]
    for r in rows[rows.duplicated(["src_id", "measureid"])].itertuples():
        details.append({"check": "duplicate", "geo_id": r.src_id, "measureid": r.measureid})
    pub = rows.drop_duplicates(["src_id", "measureid"]).set_index(["src_id", "measureid"])
    stored = facts.set_index(["geo_type", "geo_id", "indicator_id"])
    n = 0
    for mid, ind in measures.items():
        for county in counties:
            n += 1
            key = ("county", county, ind)
            if (county, mid) not in pub.index or key not in stored.index:
                details.append({"check": "missing_county", "geo_id": county, "indicator_id": ind})
                continue
            src, got = pub.loc[(county, mid)], stored.loc[key]
            for col in ("value", "value_low", "value_high"):
                a, b = src[col], got[col]
                if not (pd.isna(a) and pd.isna(b)) and not abs(a - b) <= 1e-9:
                    details.append(
                        {"check": "county_value", "geo_id": county, "indicator_id": ind, "field": col}
                    )
        part = rows[(rows.measureid == mid) & rows.value.notna()]
        want = (part.value * part.weight).sum() / part.weight.sum() if len(part) else float("nan")
        n += 1
        state = stored.loc[("state", "15", ind)] if ("state", "15", ind) in stored.index else None
        if state is None or not abs(state.value - want) <= 1e-9:
            details.append({"check": "state_mean", "indicator_id": ind, "expected": want})
            continue
        if not state.value_low <= state.value <= state.value_high:
            details.append({"check": "state_range", "indicator_id": ind})
        notes.append(
            f"{ind}: state {state.value:.2f} (range {state.value_low:.2f} to {state.value_high:.2f}) from "
            f"counties {part.value.min():.1f} to {part.value.max():.1f}"
        )
    return CheckResult("places_county", not details, True, n, len(details), details, notes)
