# Validation 6: IRS ZIP totals against the state row and district sums, and the county file's completeness (gated).
from collections.abc import Sequence

import pandas as pd

from klear.etl.geographies import KALAWAO
from klear.etl.irs import IRS_COUNTIES, IRS_INDICATORS, POOL_ZIP, STATE_COUNTY, STATE_ZIP
from klear.validation import CheckResult

STATE_TOLERANCE = 0.005
PLACED_TOLERANCE = 1e-6
UNPLACED_MAX = 0.005
CHAMBERS = ("house", "senate")


def _state_check(zip_df: pd.DataFrame) -> CheckResult:
    details: list[dict] = []
    notes: list[str] = []
    state = zip_df[zip_df.zcta == STATE_ZIP].set_index("indicator_id").value
    rest = zip_df[zip_df.zcta != STATE_ZIP].groupby("indicator_id").value.sum()
    inds = [i for i in IRS_INDICATORS if i in set(zip_df.indicator_id)]
    for ind in inds:
        if ind not in state.index:
            details.append({"check": "state_total", "indicator_id": ind, "problem": "no state row"})
            continue
        want, got = float(state[ind]), float(rest.get(ind, 0.0))
        diff = abs(got - want) / want if want else float("inf")
        notes.append(
            f"{ind}: ZIP rows plus {POOL_ZIP} {got:,.0f}, state row {want:,.0f}, difference {diff:.3%}"
        )
        if diff > STATE_TOLERANCE:
            details.append(
                {"check": "state_total", "indicator_id": ind, "zip_total": got, "state": want, "diff": diff}
            )
    return CheckResult(
        "irs_state", not details, True, len(inds), len(details), details, notes,
        {"tolerance": STATE_TOLERANCE},
    )  # fmt: skip


def _district_totals(zip_df: pd.DataFrame, district_facts: pd.DataFrame, details: list[dict]) -> list[str]:
    # districts plus listed unplaced ZIPs add back to the ZIP rows plus 99999, unplaced at most 0.5%
    unplaced = district_facts.attrs.get("unplaced")
    if unplaced is None:
        details.append({"check": "district_total", "problem": "no unplaced record on the district facts"})
        return ["no unplaced record on the district facts, district totals not compared"]
    total = zip_df[zip_df.zcta != STATE_ZIP].groupby("indicator_id").value.sum()
    notes = []
    for chamber in CHAMBERS:
        sub = district_facts[district_facts.geo_type == chamber].groupby("indicator_id").value.sum()
        for ind in IRS_INDICATORS:
            if ind not in total.index:
                continue
            placed, want = float(sub.get(ind, 0.0)), float(total[ind])
            zips = unplaced.get(chamber, {}).get(ind, {})
            lost = float(sum(zips.values()))
            listed = ", ".join(f"{z} {v:,.0f}" for z, v in sorted(zips.items())) or "none"
            notes.append(
                f"{chamber} {ind}: districts sum to {placed:,.0f}, unplaced {lost:,.0f} (ZIPs: {listed}), "
                f"ZIP rows plus {POOL_ZIP} {want:,.0f}"
            )
            if abs(placed + lost - want) > PLACED_TOLERANCE * max(abs(want), 1.0):
                details.append(
                    {"check": "district_total", "chamber": chamber, "indicator_id": ind, "districts": placed,
                     "unplaced": lost, "zip_total": want}
                )  # fmt: skip
            if want and lost / want > UNPLACED_MAX:
                details.append(
                    {"check": "unplaced_share", "chamber": chamber, "indicator_id": ind, "unplaced": lost,
                     "zip_total": want, "share": lost / want}
                )  # fmt: skip
    return notes


def _county_check(zip_df: pd.DataFrame, county_df: pd.DataFrame, zip_county: pd.Series | None) -> CheckResult:
    # gated on every county being in the file, the ZIP-derived differences are reported only
    notes = [
        f"ZIP-derived counts leave out {POOL_ZIP} and use each ZIP's largest county, so gaps are expected",
        f"IRS publishes no Kalawao County ({KALAWAO}) row, so it is not required",
    ]
    details: list[dict] = []
    have = set(zip(county_df.county, county_df.indicator_id, strict=True))
    for c in (STATE_COUNTY, *IRS_COUNTIES):
        for i in IRS_INDICATORS:
            if (c, i) not in have:
                details.append({"check": "missing_county_row", "county": c, "indicator_id": i})
    if zip_county is None:
        details.append({"check": "input", "problem": "no ZIP to county map given"})
    if details:
        notes.append(f"{len(details):,} county rows or inputs missing, county comparison not made")
        return CheckResult("irs_county", False, True, 0, len(details), details, notes)
    ind = "irs_returns"
    z = zip_df[(zip_df.indicator_id == ind) & ~zip_df.zcta.isin({STATE_ZIP, POOL_ZIP})]
    derived = z.assign(county=z.zcta.map(zip_county)).groupby("county").value.sum()
    county = county_df[(county_df.indicator_id == ind) & (county_df.county != STATE_COUNTY)]
    county = county.set_index("county").value
    rows = []
    for c in sorted(county.index):
        got, want = float(derived.get(c, 0.0)), float(county[c])
        pct = (got - want) / want * 100 if want else float("nan")
        notes.append(f"{c} {ind}: ZIP-derived {got:,.0f}, county file {want:,.0f}, difference {pct:+.1f}%")
        rows.append({"county": c, "zip_derived": got, "county_file": want, "pct_diff": pct, "passed": True})
    return CheckResult("irs_county", True, True, len(rows), 0, rows, notes)


def check_irs(
    zip_df: pd.DataFrame,
    county_df: pd.DataFrame,
    district_facts: pd.DataFrame,
    *,
    zip_county: pd.Series | None = None,
    notes: Sequence[str] = (),
) -> list[CheckResult]:
    state = _state_check(zip_df)
    extra: list[dict] = []
    if district_facts.empty:
        state.notes.append("no district facts given, district totals not compared")
    else:
        state.notes += _district_totals(zip_df, district_facts, extra)
        if "unplaced" in district_facts.attrs:
            state.n_compared += len(CHAMBERS) * len(IRS_INDICATORS)
    state.notes += list(notes)
    state.details += extra
    state.n_mismatch = len(state.details)
    state.passed = not state.details
    return [state, _county_check(zip_df, county_df, zip_county)]
