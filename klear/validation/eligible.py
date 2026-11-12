# Checks detailed-table counts add up across districts and compares state estimates with program counts.
from pathlib import Path

import yaml

from klear import config
from klear.etl.profile import DETAILED, SOURCE_BY_DATASET, load_catalog
from klear.validation import CheckResult

ADMIN_PATH = config.ROOT / "data" / "catalog" / "admin_counts.yaml"
PARTS = ("house", "senate", "county")
PART_WORDS = {"house": "House districts", "senate": "Senate districts", "county": "counties"}
# a state total with no margin of error must match to rounding
EXACT_TOL = 0.5
SOURCE = SOURCE_BY_DATASET[DETAILED]


def summed_indicators(catalog=None) -> list[str]:
    """Detailed-table counts, the ones whose districts and counties must add up to the state."""
    catalog = load_catalog() if catalog is None else catalog
    return [i.indicator_id for i in catalog if i.dataset == DETAILED and i.unit == "count"]


def _state(con, indicator: str) -> tuple[float | None, float | None]:
    row = con.execute(
        "SELECT value, moe FROM fact WHERE indicator_id = ? AND geo_type = 'state' AND source_id = ?",
        [indicator, SOURCE],
    ).fetchone()
    return (row[0], row[1]) if row else (None, None)


def check_district_sums(con, indicators: list[str] | None = None) -> CheckResult:
    """House districts, Senate districts and counties each add up to the state estimate within its margin."""
    indicators = summed_indicators() if indicators is None else indicators
    expected = dict(con.execute("SELECT geo_type, count(*) FROM geography GROUP BY 1").fetchall())
    details = []
    for ind in indicators:
        state, state_moe = _state(con, ind)
        covered = {
            r[0]
            for r in con.execute(
                "SELECT geo_type FROM indicator_coverage WHERE indicator_id = ?", [ind]
            ).fetchall()
        }
        for geo_type in (p for p in PARTS if p in covered):
            n, n_value, total, moe = con.execute(
                "SELECT count(*), count(value), sum(value), sqrt(sum(moe * moe)) FROM fact "
                "WHERE indicator_id = ? AND geo_type = ? AND source_id = ?",
                [ind, geo_type, SOURCE],
            ).fetchone()
            diff = None if state is None or total is None else total - state
            limit = state_moe if state_moe is not None else EXACT_TOL
            want = expected.get(geo_type, 0)
            ok = n == want and n_value == want and diff is not None and abs(diff) <= limit
            details.append(
                {
                    "indicator": ind, "parts": geo_type, "n": n, "n_with_value": n_value, "state": state,
                    "state_moe": state_moe, "sum": total, "sum_moe": moe, "difference": diff, "passed": ok,
                }
            )  # fmt: skip
    bad = [d for d in details if not d["passed"]]
    largest = max((abs(d["difference"]) for d in details if d["difference"] is not None), default=0.0)
    notes = [
        (
            f"{len(indicators):,} counts from Census API detailed tables, each summed over the "
            f"{', '.join(PART_WORDS[p] for p in PARTS[:-1])} and {PART_WORDS[PARTS[-1]]} it is published for, "
            "and compared with the state estimate"
        ),
        f"largest difference from the state estimate: {largest:,.0f}",
    ]
    return CheckResult(
        name="district_sums",
        passed=not bad,
        gate=True,
        n_compared=len(details),
        n_mismatch=len(bad),
        details=details,
        notes=notes,
    )


def load_admin(path: Path | None = None) -> dict:
    data = yaml.safe_load(Path(path or ADMIN_PATH).read_text(encoding="utf-8"))
    counts = {c["id"]: c for c in data["counts"]}
    for c in counts.values():
        missing = {"label", "value", "unit", "publisher", "source", "url", "retrieved"} - set(c)
        if missing:
            raise ValueError(f"admin count {c['id']}: missing {sorted(missing)}")
    return {"counts": counts, "not_compared": data.get("not_compared") or []}


def _ratio(a, b) -> float | None:
    return None if a is None or not b else a / b


def check_admin_counts(con, admin: dict | None = None) -> CheckResult:
    """State estimates beside administrative counts of the same programs, reported and not gated."""
    admin = load_admin() if admin is None else admin
    c = admin["counts"]
    snap, snap_moe = _state(con, "hh_snap")
    low138, low138_moe = _state(con, "pop_under_138pct_poverty")
    working, working_moe = _state(con, "working_below_poverty")
    row = con.execute(
        "SELECT value FROM fact WHERE indicator_id = 'irs_eitc_returns' AND geo_type = 'state'"
    ).fetchone()
    eitc = row[0] if row else None
    rate = c["eitc_participation_rate"]["value"] / 100
    implied = None if eitc is None else eitc / rate
    rows = [
        {
            "kind": "comparison", "admin": "snap_households", "estimate": "hh_snap",
            "admin_value": c["snap_households"]["value"], "value": snap, "moe": snap_moe, "ratio": _ratio(snap, c["snap_households"]["value"]),
        },
        {
            "kind": "comparison", "admin": "medicaid_chip_enrollment", "estimate": "pop_under_138pct_poverty",
            "admin_value": c["medicaid_chip_enrollment"]["value"], "value": low138, "moe": low138_moe,
            "ratio": _ratio(low138, c["medicaid_chip_enrollment"]["value"]),
        },
        {
            "kind": "comparison", "admin": "eitc_participation_rate", "estimate": "working_below_poverty",
            "admin_value": implied, "eitc_returns": eitc, "rate": c["eitc_participation_rate"]["value"],
            "value": working, "moe": working_moe, "ratio": _ratio(working, implied),
        },
    ]  # fmt: skip
    missing = [r["estimate"] for r in rows if r["value"] is None or r["admin_value"] is None]
    notes = [f"{a['label']} not compared: {a['reason']}" for a in admin["not_compared"]]
    notes += [f"{m} not compared: the store holds no state value for it" for m in missing]
    return CheckResult(
        name="admin_counts",
        passed=True,
        gate=False,
        n_compared=len(rows) - len(missing),
        n_mismatch=len(missing),
        details=rows + [{"kind": "source", **v} for v in c.values()],
        notes=notes,
        stats={"missing": missing},
    )
