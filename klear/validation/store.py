# Validation of the built fact store: geography counts, sources, coverage, ranges and stored values against DBEDT.
import pandas as pd

from klear.etl.profile import DETAILED, PROFILE, SOURCE_BY_DATASET
from klear.store.db import (
    CONTEXT_GEO_TYPES,
    CONTEXT_TIER,
    MATCH_COLS,
    MOE_STATUSES,
    RANGE_TIERS,
    VALUE_STATUSES,
)
from klear.validation import CheckResult

EXPECTED_GEOS = {"state": 1, "county": 5, "senate": 25, "house": 51}
COMPARED_SOURCE = SOURCE_BY_DATASET[PROFILE]
DETAILED_SOURCE = SOURCE_BY_DATASET[DETAILED]
GEO_WORDS = {
    "state": "state", "county": "counties", "senate": "Senate districts", "house": "House districts",
    "tract": "tracts", "zcta": "ZIP code areas",
}  # fmt: skip
MAX_LISTED = 20
VALUE_COLS = ["value", "moe", "value_status", "moe_status"]
MATCH_KEYS = MATCH_COLS[:4]
TIER_WORDS = {
    "D": "direct", "A": "apportioned with a range", "P": "counted from points", "M": "modeled with a range",
    "S": "summed from published parts", "C": "county-level context",
}  # fmt: skip
SITE_WORDS = {"public_housing": "public housing", "lihtc": "LIHTC", "health_center": "health center"}
RULE_WORDS = {"confirmed": "confirmed", "needs_manual": "needing a manual check"}


def store_counts(con, rules=None) -> dict:
    # flat counts for the manifest and report: tables, facts per tier, sites per kind, rules per status
    tables = {"geographies": "geography", "indicators": "indicator", "facts": "fact", "sources": "source"}
    out = {k: con.execute(f"SELECT count(*) FROM {t}").fetchone()[0] for k, t in tables.items()}
    tiers = dict(con.execute("SELECT tier, count(*) FROM fact GROUP BY 1").fetchall())
    out |= {f"facts tier {t} ({w})": tiers[t] for t, w in TIER_WORDS.items() if t in tiers}
    kinds = dict(con.execute("SELECT kind, count(*) FROM site GROUP BY 1").fetchall())
    out |= {f"sites {w}": kinds.get(k, 0) for k, w in SITE_WORDS.items()}
    if rules is not None:
        status = [r.status for r in rules]
        out |= {f"rules {w}": status.count(k) for k, w in RULE_WORDS.items()}
    return out


def dbedt_expected(dbedt_aligned: pd.DataFrame) -> pd.DataFrame:
    # one row per (geo_type, geo_id, acs_var) in store vocabulary: counts under acs_var, percents under acs_var + "P"
    keys = ["geo_type", "geo_id", "acs_var"]
    est = dbedt_aligned[keys + ["estimate", "moe", "estimate_status", "moe_status"]]
    pct = dbedt_aligned[keys + ["percent", "percent_moe", "percent_status", "percent_moe_status"]]
    pct = pct.assign(acs_var=pct["acs_var"] + "P")
    out = pd.concat([est.set_axis(keys + VALUE_COLS, axis=1), pct.set_axis(keys + VALUE_COLS, axis=1)])
    dup = out.duplicated(keys)
    if dup.any():
        raise ValueError(f"dbedt: duplicate rows for {out[dup][keys].head(3).to_dict('records')}")
    return out.reset_index(drop=True)


def _same(a: pd.Series, b: pd.Series) -> pd.Series:
    return (a.isna() & b.isna()) | (a == b)


def _merged(facts: pd.DataFrame, dbedt_aligned: pd.DataFrame) -> tuple[pd.DataFrame, pd.Series]:
    # stored facts beside their DBEDT cell, and which facts have one
    m = facts.merge(
        dbedt_expected(dbedt_aligned).rename(columns={c: f"dbedt_{c}" for c in VALUE_COLS}),
        on=["geo_type", "geo_id", "acs_var"],
        how="left",
        indicator="_dbedt",
        validate="1:1",
    )
    return m, m["_dbedt"] == "both"


def matched_facts(facts: pd.DataFrame, dbedt_aligned: pd.DataFrame) -> pd.DataFrame:
    """Keys of the stored facts whose value, margin of error and both statuses equal their DBEDT cell."""
    m, found = _merged(facts, dbedt_aligned)
    for c in VALUE_COLS:
        found &= _same(m[c], m[f"dbedt_{c}"])
    return m.loc[found, MATCH_KEYS].reset_index(drop=True)


def compare_values(facts: pd.DataFrame, dbedt_aligned: pd.DataFrame) -> list[dict]:
    # every stored fact against the DBEDT cell for the same variable and geography
    m, found = _merged(facts, dbedt_aligned)
    out = []
    for c in VALUE_COLS:
        bad = m[found & ~_same(m[c], m[f"dbedt_{c}"])]
        out += [
            {
                "check": "stored_value", "geo_type": r.geo_type, "geo_id": r.geo_id, "indicator": r.indicator_id,
                "field": c, "stored": None if pd.isna(r[c]) else r[c],
                "dbedt": None if pd.isna(r[f"dbedt_{c}"]) else r[f"dbedt_{c}"],
            }
            for _, r in bad.iterrows()
        ]  # fmt: skip
    out += [
        {"check": "no_dbedt_cell", "geo_type": r.geo_type, "geo_id": r.geo_id, "indicator": r.indicator_id}
        for r in m[~found].itertuples()
    ]
    return out


def check_store(
    con, dbedt_aligned: pd.DataFrame | None = None, expected_geos: dict[str, int] | None = None
) -> CheckResult:
    expected_geos = expected_geos or EXPECTED_GEOS
    details: list[dict] = []
    geos = dict(con.execute("SELECT geo_type, count(*) FROM geography GROUP BY 1").fetchall())
    if geos != expected_geos:
        details.append({"check": "geographies", "expected": expected_geos, "found": geos})
    unsourced = con.execute(
        "SELECT count(*) FROM fact WHERE source_id IS NULL OR source_id NOT IN (SELECT source_id FROM source)"
    ).fetchone()[0]
    if unsourced:
        details.append({"check": "unsourced_facts", "n": unsourced})
    declared = """
        FROM geography g JOIN indicator_coverage c USING (geo_type)
        WHERE c.complete AND NOT EXISTS (
            SELECT 1 FROM coverage_exclusion x
            WHERE x.indicator_id = c.indicator_id AND x.geo_type = g.geo_type AND x.geo_id = g.geo_id
        )
    """
    n_pairs = con.execute(f"SELECT count(*) {declared}").fetchone()[0]
    missing = con.execute(
        f"""
        SELECT g.geo_type, g.geo_id, c.indicator_id {declared} AND NOT EXISTS (
            SELECT 1 FROM fact f
            WHERE f.geo_type = g.geo_type AND f.geo_id = g.geo_id AND f.indicator_id = c.indicator_id
        )
        ORDER BY 1, 2, 3
        """
    ).fetchall()
    exclusions = con.execute(
        "SELECT geo_type, geo_id, reason, count(*) FROM coverage_exclusion GROUP BY 1, 2, 3 ORDER BY 1, 2, 3"
    ).fetchall()
    details += [{"check": "missing_fact", "geo_type": t, "geo_id": g, "indicator": i} for t, g, i in missing]
    outside = con.execute(
        """
        SELECT f.geo_type, f.geo_id, f.indicator_id FROM fact f
        WHERE NOT EXISTS (
            SELECT 1 FROM indicator_coverage c WHERE c.indicator_id = f.indicator_id AND c.geo_type = f.geo_type
        )
        ORDER BY 1, 2, 3
        """
    ).fetchall()
    details += [
        {"check": "outside_coverage", "geo_type": t, "geo_id": g, "indicator": i} for t, g, i in outside
    ]
    partial = con.execute(
        """
        SELECT count(*) FROM fact f JOIN indicator_coverage c USING (indicator_id, geo_type) WHERE NOT c.complete
        """
    ).fetchone()[0]
    tiers = ", ".join(f"'{t}'" for t in sorted(RANGE_TIERS))
    no_range = con.execute(
        f"""
        SELECT geo_type, geo_id, indicator_id FROM fact
        WHERE tier IN ({tiers}) AND value IS NOT NULL AND (value_low IS NULL OR value_high IS NULL)
        ORDER BY 1, 2, 3
        """
    ).fetchall()
    details += [{"check": "no_range", "geo_type": t, "geo_id": g, "indicator": i} for t, g, i in no_range]

    facts = con.execute(
        "SELECT f.geo_type, f.geo_id, f.indicator_id, i.acs_var, f.source_id, f.tier, "
        + ", ".join(f"f.{c}" for c in VALUE_COLS)
        + " FROM fact f JOIN indicator i USING (indicator_id)"
    ).df()
    compared = facts[facts["source_id"] == COMPARED_SOURCE]
    api_only = int((facts["source_id"] == DETAILED_SOURCE).sum())
    other = len(facts) - len(compared) - api_only
    value_problems = None
    marks = con.execute(f"SELECT {', '.join(MATCH_KEYS)} FROM fact_match").df()
    marked = set(marks.itertuples(index=False, name=None))
    if dbedt_aligned is not None:
        value_problems = compare_values(compared, dbedt_aligned)
        details += value_problems
        proven = set(matched_facts(compared, dbedt_aligned).itertuples(index=False, name=None))
        details += [
            {"check": "unproven_match", **dict(zip(MATCH_KEYS, k, strict=True))}
            for k in sorted(marked - proven)
        ]
    elif marked:
        details.append({"check": "unproven_match", "n": len(marked)})
    geo_list = ", ".join(f"'{g}'" for g in sorted(CONTEXT_GEO_TYPES))
    context_out = con.execute(
        f"SELECT geo_type, geo_id, indicator_id FROM fact WHERE tier = '{CONTEXT_TIER}' "
        f"AND geo_type NOT IN ({geo_list}) ORDER BY 1, 2, 3"
    ).fetchall()
    details += [
        {"check": "context_below_county", "geo_type": t, "geo_id": g, "indicator": i}
        for t, g, i in context_out
    ]
    n_context, n_context_ids = con.execute(
        f"SELECT count(*), count(DISTINCT indicator_id) FROM fact WHERE tier = '{CONTEXT_TIER}'"
    ).fetchone()

    c = store_counts(con)
    value_counts = facts["value_status"].value_counts()
    moe_counts = facts["moe_status"].value_counts()
    tier_counts = facts["tier"].value_counts()
    notes = [
        "geographies: " + ", ".join(f"{geos.get(k, 0):,} {GEO_WORDS.get(k, k)}" for k in expected_geos),
        (
            f"{c['facts']:,} facts for {c['indicators']:,} indicators from {c['sources']:,} sources, "
            f"{unsourced:,} without a registered source"
        ),
        (
            f"{len(missing):,} of {n_pairs:,} (geography, indicator) pairs in the complete coverage have no "
            f"fact, and {len(outside):,} facts lie outside the declared coverage"
        ),
        (
            f"{partial:,} facts lie in partial coverage, where a source publishes only some tracts or ZIP code "
            "areas, so a missing fact there is expected"
        ),
        *[f"not required: {t} {g} for {n:,} indicators, because {reason}" for t, g, reason, n in exclusions],
        "facts by tier: " + ", ".join(f"{TIER_WORDS.get(t, t)} {v:,}" for t, v in tier_counts.items()),
        (
            f"{len(no_range):,} apportioned or modeled facts with a value lack a range (value_low and "
            "value_high)"
        ),
    ]
    if value_problems is None:
        notes.append(
            "stored values were not compared with DBEDT in this build because the DBEDT check was skipped"
        )
    else:
        notes.append(
            f"{len(compared):,} stored Census API data profile facts compared with the DBEDT workbook cell "
            f"(value, margin of error and both statuses), {len(value_problems):,} differ"
        )
    notes.append(
        f"{len(marked):,} stored facts are marked as matching DBEDT's published profile, and the site says so in "
        "their source lists. A mark is allowed only on a fact compared above and equal to its DBEDT cell"
    )
    notes.append(
        f"{n_context:,} county-level facts (tier C) for {n_context_ids:,} indicators, "
        f"{len(context_out):,} of them stored for a geography other than a county or the state"
    )
    notes += [
        (
            f"{api_only:,} facts from the Census API detailed tables (B and C tables) are Census API only, "
            "with no second source to compare against"
        ),
        (
            f"{other:,} facts from the tax, enrollment, site, need and county-level sources are checked by "
            "their own sections below"
        ),
        "values, with the phrase the agent uses: "
        + ", ".join(f'{v:,} "{VALUE_STATUSES.get(k, k)}"' for k, v in value_counts.items()),
        "margins of error, with the phrase the agent uses: "
        + ", ".join(f'{v:,} "{MOE_STATUSES.get(k, k)}"' for k, v in moe_counts.items()),
    ]
    return CheckResult(
        name="store",
        passed=not details,
        gate=True,
        n_compared=n_pairs,
        n_mismatch=len(details),
        details=details[:MAX_LISTED],
        notes=notes,
        stats={
            "geographies": {k: geos.get(k, 0) for k in expected_geos},
            "pairs": n_pairs,
            "values_compared": None if value_problems is None else len(compared),
            "values_mismatched": None if value_problems is None else len(value_problems),
            "api_only": api_only,
            "other_sources": other,
            "partial_coverage": partial,
        },
    )
