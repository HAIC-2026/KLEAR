# Validation: the Commission report's district descriptions and permanent-resident counts against its own totals.
import pandas as pd

from klear.etl.plan_report import SEATS, PlanReport
from klear.validation import CheckResult

COLUMNS = [
    "geo_type", "geo_id", "description", "description_page", "description_pdf_page", "permanent",
    "permanent_page", "permanent_pdf_page",
]  # fmt: skip
KEY = ["chamber", "geo_id"]


def _name(chamber: str, geo_id: str) -> str:
    return f"{chamber.title()} {int(geo_id)}"


def _units(report: PlanReport, rows: pd.DataFrame, problems: list[str]) -> tuple[list[dict], set]:
    """Each island unit's district sum against the unit total in Tables B and C, and the units against the
    state total in Table D. Returns the unit rows and the (chamber, unit) pairs whose sums fail."""
    out, failed = [], set()
    for b in report.bases.itertuples():
        sub = rows[(rows.chamber == b.chamber) & (rows.unit == b.unit)]
        same_base = report.bases[report.bases.unit == b.unit].base.nunique() == 1
        ok = len(sub) == b.seats and int(sub.permanent.sum()) == b.base and same_base
        if not ok:
            failed.add((b.chamber, b.unit))
            problems.append(
                f"{b.chamber.title()} districts on {b.unit}: {len(sub)} districts adding to "
                f"{int(sub.permanent.sum()):,}, the report gives {b.seats} seats and {b.base:,} permanent residents"
            )
        out.append(
            {
                "check": "unit",
                "chamber": b.chamber,
                "unit": b.unit,
                "districts": len(sub),
                "seats": b.seats,
                "sum": int(sub.permanent.sum()),
                "base": b.base,
                "passed": ok,
            }
        )
    total = report.state_total
    for chamber, seats in SEATS.items():
        bases = report.bases[report.bases.chamber == chamber]
        ok = (
            total is not None
            and int(bases.base.sum()) == total["base"]
            and int(bases.seats.sum()) == total[chamber] == seats
        )
        if not ok:
            failed |= {(chamber, u) for u in bases.unit}
            problems.append(f"{chamber.title()} island units do not add up to the state row of Table D")
        out.append(
            {
                "check": "state",
                "chamber": chamber,
                "unit": "State",
                "districts": int(bases.seats.sum()),
                "seats": total[chamber] if total else None,
                "sum": int(bases.base.sum()),
                "base": total["base"] if total else None,
                "passed": ok,
            }
        )
    return out, failed


def check_plan_report(
    report: PlanReport, commission: pd.DataFrame | None = None, *, commission_problem: str | None = None
) -> tuple[CheckResult, pd.DataFrame]:
    """Every House and Senate district needs its description from Appendix D and a permanent-resident count
    whose island table row adds up (count minus target is the deviation), equals the statewide table, sums with
    the island unit's other districts to the unit total, and, when the plan files load, equals their count.
    A count that fails any of these is left out, and so is a description found twice or not as expected."""
    problems = list(report.problems)
    desc = report.descriptions
    twice = desc.duplicated(KEY, keep=False)
    problems += [f"{_name(r.chamber, r.geo_id)} described more than once" for r in desc[twice].itertuples()]
    desc = desc[~twice]

    rows = report.island_rows
    dup = rows.duplicated(KEY, keep=False)
    problems += [
        f"{_name(r.chamber, r.geo_id)} listed twice in the island tables" for r in rows[dup].itertuples()
    ]
    rows = rows[~dup].merge(
        report.bases[["chamber", "unit", "target"]].rename(columns={"target": "unit_target"}),
        on=["chamber", "unit"],
        how="left",
    )
    state = report.state_rows.drop_duplicates(KEY, keep=False).set_index(KEY)["permanent"]
    rows["statewide"] = [state.get((c, g)) for c, g in zip(rows.chamber, rows.geo_id, strict=True)]
    bad = rows.permanent - rows.target != rows.deviation
    bad |= rows.target != rows.unit_target
    bad |= rows.statewide != rows.permanent
    problems += [
        f"{_name(r.chamber, r.geo_id)}: island table row does not add up or differs from the statewide table"
        for r in rows[bad].itertuples()
    ]
    units, failed = _units(report, rows, problems)
    bad |= pd.Series(
        [(c, u) in failed for c, u in zip(rows.chamber, rows.unit, strict=True)], index=rows.index
    )

    notes = []
    if commission is not None:
        files = commission.set_index(KEY)["permanent"]
        rows["files"] = [files.get((c, g)) for c, g in zip(rows.chamber, rows.geo_id, strict=True)]
        differ = rows.files != rows.permanent
        problems += [
            f"{_name(r.chamber, r.geo_id)}: {r.permanent:,} in the report, {r.files} in the plan files"
            for r in rows[differ].itertuples()
        ]
        bad |= differ
        notes.append(
            f"Permanent residents equal the Commission's final plan files (field PopHaw2022) in "
            f"{int((~differ).sum())} of {len(rows)} districts"
        )
    else:
        notes.append(f"Commission plan files not compared: {commission_problem or 'not loaded'}")
    kept = rows[~bad]

    expected = pd.DataFrame([(c, f"{i:03d}") for c, n in SEATS.items() for i in range(1, n + 1)], columns=KEY)
    out = (
        expected.merge(desc, on=KEY, how="left")
        .rename(columns={"page": "description_page", "pdf_page": "description_pdf_page"})
        .merge(
            kept[[*KEY, "permanent", "page", "pdf_page"]].rename(
                columns={"page": "permanent_page", "pdf_page": "permanent_pdf_page"}
            ),
            on=KEY,
            how="left",
        )
        .rename(columns={"chamber": "geo_type"})
    )
    missing = out.description.isna() | out.permanent.isna()
    for r in out[missing].itertuples():
        gone = [w for w, v in (("description", r.description), ("population", r.permanent)) if pd.isna(v)]
        problems.append(f"{_name(r.geo_type, r.geo_id)}: {' and '.join(gone)} left out")
    full = out[~missing]
    pages = sorted(set(out.description_page.dropna()))
    described = f"Official descriptions for {int(out.description.notna().sum())} of {len(out)} districts"
    counted = f"Permanent residents for {int(out.permanent.notna().sum())} of {len(out)} districts"
    sums = [u for u in units if u["check"] == "unit"]
    notes = [
        f"{described}, report pages {pages[0]} to {pages[-1]} (Appendix D)" if pages else described,
        f"{counted}, Tables 1 to 10 on report pages {', '.join(sorted(set(kept.page.dropna()), key=int))}",
        f"Island units: {sum(u['passed'] for u in sums)} of {len(sums)} chamber and unit sums equal the "
        "report's totals"
        + (f", statewide {report.state_total['base']:,} permanent residents" if report.state_total else ""),
        *notes,
    ]
    details = [{"check": "problem", "problem": p, "passed": False} for p in problems] + units
    for col in ("description_pdf_page", "permanent", "permanent_pdf_page"):
        out[col] = out[col].astype("Int64")
    result = CheckResult(
        name="plan_report",
        passed=not problems and len(full) == len(out),
        gate=True,
        n_compared=len(out),
        n_mismatch=int(missing.sum()),
        details=details,
        notes=notes,
        stats={"districts": len(out), "complete": len(full)},
    )
    return result, out[COLUMNS]
