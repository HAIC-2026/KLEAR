# Validation: block-assigned 2020 district populations against the Census Bureau and the Reapportionment Commission.
import pandas as pd

from klear.validation import CheckResult

PLAN_MARK = "(2022)"
CHAMBERS = ("house", "senate")
TOP = 3


def _name(chamber: str, geo_id: str) -> str:
    return f"{chamber.title()} {int(geo_id)}"


def _merge(blocks: pd.DataFrame, census: pd.DataFrame, chamber: str) -> pd.DataFrame:
    ours = blocks.groupby(chamber)["pop2020"].sum().rename("blocks")
    theirs = census.loc[census["chamber"] == chamber].set_index("geo_id")
    df = pd.concat([ours, theirs["pop"].rename("census"), theirs["name"]], axis=1, join="outer")
    df.index.name = "geo_id"
    return df.reset_index().sort_values("geo_id", ignore_index=True)


def _int(v):
    return None if pd.isna(v) else int(v)


def _commission_notes(rows: list[dict]) -> list[str]:
    total = sum(r["blocks"] or 0 for r in rows if r["chamber"] == "house")
    perm = sum(r["permanent"] for r in rows if r["chamber"] == "house")
    same = sum(r["commission_census"] == r["blocks"] for r in rows)
    files = (
        "Reapportionment Commission final 2022 plan files: their own 2020 census counts equal KLEAR's "
        f"block sums in {same} of {len(rows)} districts"
    )
    base = (
        f"Permanent-resident base: the Commission counted {perm:,} permanent residents, {total - perm:,} "
        f"({(total - perm) / total:.1%}) fewer than the 2020 census count of {total:,}. Hawaiʻi draws "
        "districts on permanent residents, so the Commission removed non-resident military members, their "
        "dependents and non-resident students"
    )
    notes = [files, base]
    for chamber in CHAMBERS:
        sub = sorted((r for r in rows if r["chamber"] == chamber), key=lambda r: -r["not_permanent"])
        top = ", ".join(
            f"{r['name']} ({r['not_permanent']:,} people, {r['not_permanent'] / r['blocks']:.1%})"
            for r in sub[:TOP]
        )
        mid = pd.Series([r["not_permanent"] for r in sub]).median()
        share = pd.Series([r["not_permanent"] / r["blocks"] for r in sub]).median()
        notes.append(
            f"{chamber.title()}: largest differences are {top}. Median difference {mid:,.0f} people "
            f"({share:.1%})"
        )
    return notes


def check_population(
    blocks: pd.DataFrame,
    census: pd.DataFrame,
    commission: pd.DataFrame | None = None,
    *,
    commission_problem: str | None = None,
) -> CheckResult:
    # gate: every district's block sum equals the Census Bureau's published 2020 count for the 2022 plan
    details: list[dict] = []
    notes: list[str] = []
    for chamber in CHAMBERS:
        df = _merge(blocks, census, chamber)
        stale = df[df["name"].notna() & ~df["name"].fillna("").str.contains(PLAN_MARK, regex=False)]
        details += [
            {"check": "plan", "chamber": chamber, "district": g, "name": n, "passed": False}
            for g, n in zip(stale["geo_id"], stale["name"], strict=True)
        ]
        for r in df.itertuples():
            ours, theirs = _int(r.blocks), _int(r.census)
            ok = ours is not None and ours == theirs
            details.append(
                {
                    "check": "district",
                    "chamber": chamber,
                    "district": r.geo_id,
                    "name": _name(chamber, r.geo_id),
                    "blocks": ours,
                    "census": theirs,
                    "difference": None if ours is None or theirs is None else ours - theirs,
                    "passed": ok,
                }
            )
        rows = [d for d in details if d["check"] == "district" and d["chamber"] == chamber]
        same = sum(d["passed"] for d in rows)
        notes.append(
            f"{chamber.title()}: {same} of {len(rows)} districts match the Census Bureau's 2020 count exactly"
        )
    notes.append(
        "Census Bureau source: 2020 Census 118th Congressional District Summary File (API dataset dec/cd118, "
        "total population P1_001N), which tabulates the 2022 state legislative districts. "
        f"Every district name it returns ends in {PLAN_MARK}"
    )
    rows = [d for d in details if d["check"] == "district"]
    if commission is not None:
        theirs = commission.set_index(["chamber", "geo_id"])
        for d in rows:
            key = (d["chamber"], d["district"])
            found = key in theirs.index
            d["commission_census"] = int(theirs.at[key, "census_total"]) if found else None
            d["permanent"] = int(theirs.at[key, "permanent"]) if found else None
            d["not_permanent"] = d["blocks"] - d["permanent"] if found and d["blocks"] is not None else None
        complete = [d for d in rows if d["permanent"] is not None and d["blocks"] is not None]
        if len(complete) == len(rows):
            notes += _commission_notes(complete)
        else:
            notes.append(f"Reapportionment Commission files cover {len(complete)} of {len(rows)} districts")
    else:
        notes.append(
            f"Reapportionment Commission files not compared: {commission_problem or 'not available'}"
        )
    bad = [d for d in details if not d["passed"]]
    return CheckResult(
        name="population",
        passed=not bad,
        gate=True,
        n_compared=len(rows),
        n_mismatch=len(bad),
        details=details,
        notes=notes,
        stats={"commission": commission is not None},
    )
