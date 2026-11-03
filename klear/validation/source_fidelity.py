# Checks that DBEDT profile workbook cells equal the Census API profile values cell by cell.
import pandas as pd

from klear.etl.acs_values import OPEN_ENDED_OFFSET
from klear.validation import CheckResult

FIELDS = ("estimate", "moe", "percent", "percent_moe")
KEYS = ["geo_type", "geo_id", "variable"]
FIELD_WORDS = {
    "estimate": "estimates",
    "moe": "margins of error",
    "percent": "percents",
    "percent_moe": "percent margins of error",
}
# allowed (DBEDT status, API status) pairs per field, from the 2020-2024 files and API seen 2026-10-29
KINDS = {
    "estimate": {
        ("ok", "ok"),  # number / number
        ("not_applicable", "not_applicable"),  # "(X)" / -888888888
        ("suppressed", "suppressed"),  # "-" / -666666666, "N" / -999999999
        ("top_coded", "ok"),  # "4,000+" / 4001 (value and open_ended MOE checked too)
        ("bottom_coded", "ok"),  # "2,500-" / 2499 (Census convention, not seen in 2024 files)
    },
    "moe": {
        ("ok", "ok"),  # "±436" / 436
        ("not_applicable", "not_applicable"),  # "(X)" / -888888888
        ("controlled", "controlled"),  # "*****" / -555555555
        ("not_computable", "not_computable"),  # "**" / -222222222, "N" / -999999999
        ("open_ended", "open_ended"),  # "***" / -333333333
    },
    "percent": {
        ("ok", "ok"),  # "59.3%" / 59.3
        ("universe_repeat", "ok"),  # count repeated / PE equal to the estimate
        ("not_applicable", "not_applicable"),  # "(X)" / -888888888
        ("suppressed", "suppressed"),  # "-" / -666666666
    },
    "percent_moe": {
        ("ok", "ok"),  # "±2.8" / 2.8
        ("not_applicable", "not_applicable"),  # "(X)" / -888888888
        ("not_computable", "not_computable"),  # "**" / -222222222
        ("controlled", "controlled"),  # "*****" / -555555555
    },
}
DBEDT_STATUS = {
    "estimate": "estimate_status",
    "moe": "moe_status",
    "percent": "percent_status",
    "percent_moe": "percent_moe_status",
}


def _same(a: pd.Series, b: pd.Series) -> pd.Series:
    return (a.isna() & b.isna()) | (a == b)


def _api_parts(api: pd.DataFrame) -> tuple[pd.DataFrame, pd.DataFrame]:
    dup = api.duplicated(KEYS)
    if dup.any():
        raise ValueError(f"api: duplicate rows for {api[dup][KEYS].head(3).to_dict('records')}")
    cols = KEYS + ["estimate", "estimate_status", "moe", "moe_status"]
    pct = api["variable"].str.endswith("P")
    est = api[~pct][cols].rename(
        columns={
            "estimate": "api_estimate", "estimate_status": "api_estimate_status",
            "moe": "api_moe", "moe_status": "api_moe_status",
        }
    )  # fmt: skip
    per = api[pct][cols].rename(
        columns={
            "estimate": "api_percent", "estimate_status": "api_percent_status",
            "moe": "api_percent_moe", "moe_status": "api_percent_moe_status",
        }
    )  # fmt: skip
    per["variable"] = per["variable"].str[:-1]
    return est, per


def _value(v):
    return None if pd.isna(v) else v


def _kind_ok(x: pd.DataFrame, f: str) -> pd.Series:
    pairs = pd.Series(list(zip(x[DBEDT_STATUS[f]], x[f"api_{f}_status"], strict=True)), index=x.index)
    return pairs.isin(KINDS[f])


def check_source_fidelity(dbedt_aligned: pd.DataFrame, api: pd.DataFrame) -> CheckResult:
    if dbedt_aligned["acs_var"].isna().any():
        rows = dbedt_aligned[dbedt_aligned["acs_var"].isna()][["sheet", "row_no", "label"]].head(3)
        raise ValueError(f"dbedt rows without acs_var: {rows.to_dict('records')}")
    est, per = _api_parts(api)
    on = ["geo_type", "geo_id", "acs_var"]
    x = dbedt_aligned.merge(
        est.rename(columns={"variable": "acs_var"}), on=on, how="left", indicator="_api", validate="m:1"
    ).merge(
        per.rename(columns={"variable": "acs_var"}), on=on, how="left", indicator="_api_p", validate="m:1"
    )
    present = (x["_api"] == "both") & (x["_api_p"] == "both")

    offset = x["estimate_status"].map(OPEN_ENDED_OFFSET)
    open_ended = offset.notna()
    est_ok = _same(x["estimate"], x["api_estimate"])
    est_ok[open_ended] = (x["estimate"] + offset == x["api_estimate"])[open_ended]

    has_pct = x["percent"].notna()
    universe_pct = x["api_percent"].isna() | (x["api_percent"] == x["api_estimate"])
    pct_ok = (x["percent"] == x["api_percent"]).where(has_pct, universe_pct)

    value_ok = {
        "estimate": est_ok,
        "moe": _same(x["moe"], x["api_moe"]),
        "percent": pct_ok,
        "percent_moe": _same(x["percent_moe"], x["api_percent_moe"]),
    }
    base = ["geo_type", "geo_id", "sheet", "row_no", "label", "acs_var"]
    details = [
        {k: r[k] for k in base} | {"field": "missing", "dbedt": None, "api": None}
        for _, r in x[~present].iterrows()
    ]
    notes = []
    for f in FIELDS:
        kind_ok = _kind_ok(x, f)
        bad = x[present & ~(value_ok[f] & kind_ok)]
        for _, r in bad.iterrows():
            details.append(
                {k: r[k] for k in base}
                | {"field": f, "dbedt": _value(r[f]), "api": _value(r[f"api_{f}"])}
                | {"dbedt_status": r[DBEDT_STATUS[f]], "api_status": r[f"api_{f}_status"]}
            )
        n_kind = int((present & ~kind_ok).sum())
        notes.append(
            f"{FIELD_WORDS[f]}: {int(present.sum()):,} compared, {len(bad):,} differ "
            f"({n_kind:,} because the kind of special code differs)"
        )
    notes.append(f"missing API rows: {int((~present).sum()):,}")
    n_open = int((open_ended & present & est_ok).sum())
    notes.append(
        f"open-ended: {n_open:,} DBEDT {'value' if n_open == 1 else 'values'} shown as top or bottom "
        "coded (for example 4,000+) matched the API's encoding of the same value (4,001)"
    )
    notes.append(
        f"universe rows: {int((~has_pct & present).sum()):,} DBEDT rows that repeat the count in the "
        "percent column were checked against an empty API percent or one equal to the API estimate"
    )
    return CheckResult(
        name="source_fidelity",
        passed=not details,
        gate=True,
        n_compared=len(FIELDS) * len(x),
        n_mismatch=len(details),
        details=details,
        notes=notes,
    )
