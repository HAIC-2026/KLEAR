# Validation 7: CMS county residuals are never negative, and district totals add back to the county totals (gated).
from collections.abc import Sequence

import pandas as pd

from klear.etl.cms import CMS_INDICATORS, county_residuals, null_published
from klear.etl.geographies import KALAWAO
from klear.validation import CheckResult


def check_cms(
    zip_df: pd.DataFrame,
    county_df: pd.DataFrame,
    zip_county: pd.Series | None = None,
    *,
    notes: Sequence[str] = (),
) -> CheckResult:
    out_notes = list(notes)
    if zip_county is None:
        problem = "no ZIP to county map given, residuals not computed"
        return CheckResult(
            "cms_county", False, True, 0, 1, [{"check": "zip_county_map", "problem": problem}], [problem]
        )
    details: list[dict] = []
    for name, df in (("zip_df", zip_df), ("county_df", county_df)):
        if df.empty:
            details.append({"check": "empty_input", "input": name})
    for r in null_published(zip_df).itertuples():
        details.append({"check": "null_published_zip", "zcta": r.zcta, "indicator_id": r.indicator_id})
    have = set(zip(county_df.county, county_df.indicator_id, strict=True))
    with_rows = set(zip_df.zcta.map(zip_county).dropna())
    for c in sorted(set(zip_county.dropna())):
        if c == KALAWAO and c not in with_rows and not any(k[0] == c for k in have):
            out_notes.append(f"CMS publishes no rows for Kalawao County ({KALAWAO}), so it needs no total")
            continue
        for ind in CMS_INDICATORS:
            if (c, ind) not in have:
                details.append({"check": "no_county_total", "county": c, "indicator_id": ind})
    unmapped = zip_df[(zip_df.value_status == "ok") & ~zip_df.zcta.isin(zip_county.index)]
    for z in sorted(set(unmapped.zcta)):
        details.append({"check": "published_zip_without_county", "zcta": z})
    res = county_residuals(zip_df, county_df, zip_county)
    for r in res.itertuples():
        out_notes.append(
            f"{r.county} {r.indicator_id}: county {r.county_total:,.0f}, published ZIPs {r.published:,.0f}, "
            f"residual {r.residual:,.0f}"
        )
        if pd.isna(r.county_total):
            details.append(
                {"check": "county_total_suppressed", "county": r.county, "indicator_id": r.indicator_id}
            )
        elif r.residual < 0:
            details.append(
                {"check": "residual", "county": r.county, "indicator_id": r.indicator_id, "residual": r.residual}
            )  # fmt: skip
    pub = zip_df[zip_df.value_status == "ok"].groupby("indicator_id").value.sum()
    total = county_df[county_df.value_status == "ok"].groupby("indicator_id").value.sum()
    for ind in CMS_INDICATORS:
        if ind in total.index and total[ind]:
            got = float(pub.get(ind, 0.0))
            out_notes.append(
                f"statewide published share {ind}: {got:,.0f} of {total[ind]:,.0f} ({got / total[ind]:.1%})"
            )
    return CheckResult("cms_county", not details, True, len(res), len(details), details, out_notes)


PLACED_TOLERANCE = 1e-6
UNPLACED_MAX = 0.005
CHAMBERS = ("house", "senate")


def check_cms_districts(facts: pd.DataFrame, county_df: pd.DataFrame) -> CheckResult:
    # districts hold the placed published ZIPs and residuals, the rest is listed as unplaced, at most 0.5%
    placed, lost_all = facts.attrs.get("placed"), facts.attrs.get("unplaced")
    if placed is None or lost_all is None:
        problem = "no placed or unplaced record on the district facts"
        return CheckResult(
            "cms_districts", False, True, 0, 1, [{"check": "attrs", "problem": problem}], [problem]
        )
    total = county_df[county_df.value_status == "ok"].groupby("indicator_id").value.sum()
    details: list[dict] = []
    notes: list[str] = []
    n = 0
    for chamber in CHAMBERS:
        sub = facts[facts.geo_type == chamber].groupby("indicator_id").value.sum()
        for ind in CMS_INDICATORS:
            n += 1
            districts, recorded = float(sub.get(ind, 0.0)), float(placed.get(chamber, {}).get(ind, 0.0))
            want = float(total.get(ind, 0.0))
            items = lost_all.get(chamber, {}).get(ind, {})
            lost = float(sum(items.values()))
            listed = ", ".join(f"{k} {v:,.0f}" for k, v in sorted(items.items())) or "none"
            notes.append(
                f"{chamber} {ind}: districts {districts:,.0f}, unplaced {lost:,.0f} ({listed}), "
                f"county totals {want:,.0f}"
            )
            tol = PLACED_TOLERANCE * max(abs(want), 1.0)
            if abs(districts - recorded) > tol:
                details.append(
                    {"check": "placed_record", "chamber": chamber, "indicator_id": ind, "districts": districts,
                     "placed": recorded}
                )  # fmt: skip
            if abs(districts + lost - want) > tol:
                details.append(
                    {"check": "total", "chamber": chamber, "indicator_id": ind, "districts": districts,
                     "unplaced": lost, "county_total": want}
                )  # fmt: skip
            if want and lost / want > UNPLACED_MAX:
                details.append(
                    {"check": "unplaced_share", "chamber": chamber, "indicator_id": ind, "unplaced": lost,
                     "county_total": want, "share": lost / want}
                )  # fmt: skip
    return CheckResult("cms_districts", not details, True, n, len(details), details, notes)
