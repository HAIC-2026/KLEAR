# Validation 4: tract-to-district apportionment conservation and accuracy against direct ACS district values.
import math

import pandas as pd

from klear.etl.blocks import build_block_table
from klear.etl.census_api import get_acs
from klear.etl.crosswalk import apportion, build_crosswalk
from klear.validation import CheckResult

REL_TOL = 1e-6
AGE65 = [f"B01001_{i:03d}" for i in (20, 21, 22, 23, 24, 25, 44, 45, 46, 47, 48, 49)]
INDICATORS = {
    "total_pop": ["B01003_001"],
    "pop_65_plus": AGE65,
    "households": ["B11001_001"],
    "snap_households": ["B22003_002"],
    "poverty_persons": ["B17001_002"],
    "renter_households": ["B25003_003"],
}
UNITS = {k: "count" for k in INDICATORS}
LABELS = {
    "total_pop": "total population",
    "pop_65_plus": "population 65 and over",
    "households": "households",
    "snap_households": "households receiving SNAP",
    "poverty_persons": "people below poverty",
    "renter_households": "renter households",
}
CHAMBERS = ("house", "senate")


def _combine(df: pd.DataFrame) -> pd.DataFrame:
    # sum estimates per geography, MOE as root sum of squares (Census approximation)
    out = {}
    for name, variables in INDICATORS.items():
        sub = df[df["variable"].isin(variables)]
        g = sub.groupby("geo_id")
        est = g["estimate"].sum(min_count=1)
        moe = g["moe"].apply(
            lambda s: math.sqrt((s.dropna() ** 2).sum()) if s.notna().any() else float("nan")
        )
        n_null = g["estimate"].apply(lambda s: int(s.isna().sum()))
        out[name] = pd.DataFrame({"value": est, "moe": moe, "n_null": n_null})
    res = pd.concat(out, names=["indicator_id", "geo_id"]).reset_index()
    return res


def _merge_chambers(items: list[tuple[str, str]]) -> list[str]:
    # state a note shared by both chambers once
    seen: dict[str, list[str]] = {}
    for chamber, text in items:
        seen.setdefault(text, []).append(chamber)
    return [
        f"{' and '.join(c.title() for c in chambers)}: {text}" if len(chambers) < len(CHAMBERS) else
        f"Both chambers: {text}"
        for text, chambers in seen.items()
    ]  # fmt: skip


def _fetch_all(fetch, geo_type: str) -> pd.DataFrame:
    variables = [v for vs in INDICATORS.values() for v in vs]
    parts = [fetch("acs/acs5", variables, geo_type)]
    return _combine(pd.concat(parts, ignore_index=True))


def check_apportionment(con=None, *, blocks: pd.DataFrame | None = None, fetch=get_acs) -> CheckResult:
    blocks = build_block_table() if blocks is None else blocks
    tract = _fetch_all(fetch, "tract")
    src = tract.rename(columns={"geo_id": "src_id"})[["src_id", "indicator_id", "value"]]
    src = src.assign(value=src["value"].fillna(0.0))
    notes = [
        (
            "Margins of error: component estimates are summed and their margins of error combined as the "
            "square root of the sum of squares"
        ),
    ]
    null_cells = int(tract["n_null"].sum())
    if null_cells:
        notes.append(f"{null_cells:,} suppressed or missing tract estimates counted as 0")
    details, summaries, conserved = [], [], []
    by_chamber: list[tuple[str, str]] = []
    for chamber in CHAMBERS:
        xwalk = build_crosswalk(blocks, "tract", chamber)
        app = apportion(src, xwalk, UNITS)
        by_chamber += [(chamber, n) for n in xwalk.attrs["notes"] + app.attrs["notes"]]
        zero = set(xwalk.attrs["zero_pop"])
        zero_val = src[src["src_id"].isin(zero)].groupby("indicator_id")["value"].sum()
        total_val = src.groupby("indicator_id")["value"].sum()
        lost = {LABELS.get(k, k): f"{v:,.0f}" for k, v in zero_val.items() if v != 0}
        by_chamber.append(
            (
                chamber,
                f"ACS value on the {len(zero):,} zero-population tracts, which get no weights and are the only "
                "allowed exception to conservation: "
                + (", ".join(f"{k} {v}" for k, v in lost.items()) if lost else "none"),
            )
        )
        direct = _fetch_all(fetch, chamber).rename(columns={"geo_id": "dst_id"})
        direct_null = int(direct["n_null"].sum())
        by_chamber.append(
            (
                chamber,
                f"{direct_null:,} suppressed or missing direct district component estimates"
                + (", their indicators summed over the published components only" if direct_null else ""),
            )
        )
        for ind in INDICATORS:
            expected = float(total_val.get(ind, 0.0) - zero_val.get(ind, 0.0))
            got = float(app.loc[app["indicator_id"] == ind, "value"].sum())
            ok = math.isclose(got, expected, rel_tol=REL_TOL, abs_tol=1e-9)
            conserved.append(ok)
            m = app[app["indicator_id"] == ind].merge(
                direct[direct["indicator_id"] == ind],
                on=["dst_id", "indicator_id"],
                how="outer",
                suffixes=("_app", "_direct"),
            )
            m["value_app"] = m["value_app"].fillna(0.0)
            ape = (
                (m["value_app"] - m["value_direct"]).abs()
                / m["value_direct"].where(m["value_direct"] > 0)
                * 100
            )
            within = (m["value_app"] - m["value_direct"]).abs() <= m["moe"]
            has_moe = m["moe"].notna() & m["value_direct"].notna()
            for r, a, w in zip(m.itertuples(), ape, within, strict=True):
                details.append(
                    {
                        "kind": "district",
                        "chamber": chamber,
                        "indicator": ind,
                        "district": r.dst_id,
                        "apportioned": float(r.value_app),
                        "direct": None if pd.isna(r.value_direct) else float(r.value_direct),
                        "direct_moe": None if pd.isna(r.moe) else float(r.moe),
                        "abs_pct_error": None if pd.isna(a) else float(a),
                        "within_moe": bool(w) if pd.notna(r.moe) and pd.notna(r.value_direct) else None,
                    }
                )
            summaries.append(
                {
                    "kind": "summary",
                    "chamber": chamber,
                    "indicator": ind,
                    "n_districts": len(m),
                    "median_ape": float(ape.median()),
                    "p90_ape": float(ape.quantile(0.9)),
                    "share_within_moe": float(within[has_moe].mean()) if has_moe.any() else None,
                    "source_total": expected,
                    "apportioned_total": got,
                    "conserved": ok,
                }
            )
    notes += _merge_chambers(by_chamber)
    return CheckResult(
        name="apportionment",
        passed=all(conserved),
        gate=True,
        n_compared=len(conserved),
        n_mismatch=conserved.count(False),
        details=summaries + details,
        notes=notes,
    )
