# Validation 3: block population total and block equivalency vs spatial join district membership.
import geopandas as gpd
import pandas as pd

from klear.etl.blocks import MAX_SNAP_M, load_block_points, spatial_membership
from klear.validation import CheckResult

HAWAII_POP2020 = 1_455_271
MIN_AGREEMENT = 0.995


def _membership(blocks: pd.DataFrame, chamber: str, ids: set[str]) -> list[dict]:
    bad = blocks.loc[~blocks[chamber].isin(ids), ["block", chamber]]
    return [
        {"check": "membership", "chamber": chamber, "block": b, "value": v} for b, v in bad.itertuples(False)
    ]


def _agreement(blocks, points, districts, chamber, min_agreement, max_snap_m) -> tuple[dict, list[dict]]:
    sm = spatial_membership(points, districts, max_snap_m=max_snap_m).set_index("block")
    df = blocks[["block", chamber, "pop2020"]].copy()
    df["spatial"] = df["block"].map(sm["geo_id"])
    df["method"] = df["block"].map(sm["method"]).fillna("no_point")
    bad = df[df[chamber] != df["spatial"]]
    total = int(df["pop2020"].sum())
    pop_bad = int(bad["pop2020"].sum())
    share = 1.0 - pop_bad / total if total else 0.0
    methods = df["method"].value_counts()
    bad_methods = bad["method"].value_counts()
    summary = {
        "check": "agreement",
        "chamber": chamber,
        "share_agree": share,
        "pop_disagree": pop_bad,
        "blocks_disagree": len(bad),
        "edge": int(methods.get("edge", 0)),
        "snapped": int(methods.get("snapped", 0)),
        "unassigned": int(methods.get("none", 0) + methods.get("no_point", 0)),
        "disagree_inside": int(bad_methods.get("within", 0) + bad_methods.get("edge", 0)),
        "disagree_snapped": int(bad_methods.get("snapped", 0)),
        "disagree_unassigned": int(bad_methods.get("none", 0) + bad_methods.get("no_point", 0)),
        "disagree_zero_pop": int((bad["pop2020"] == 0).sum()),
        "passed": share >= min_agreement,
    }
    rows = [
        {
            "check": "disagree",
            "chamber": chamber,
            "block": r.block,
            "equivalency": getattr(r, chamber),
            "spatial": None if pd.isna(r.spatial) else r.spatial,
            "method": r.method,
            "pop2020": int(r.pop2020),
        }
        for r in bad.itertuples()
    ]
    return summary, rows


def check_blocks(
    blocks: pd.DataFrame,
    districts_house: gpd.GeoDataFrame,
    districts_senate: gpd.GeoDataFrame,
    *,
    points: pd.DataFrame | None = None,
    expected_total: int = HAWAII_POP2020,
    min_agreement: float = MIN_AGREEMENT,
    max_snap_m: float = MAX_SNAP_M,
) -> CheckResult:
    if points is None:
        points = load_block_points()
    details: list[dict] = []
    notes: list[str] = []
    failed = False
    total = int(blocks["pop2020"].sum())
    notes.append(f"total population {total:,} (expected {expected_total:,})")
    if total != expected_total:
        details.append({"check": "total", "expected": expected_total, "found": total})
        failed = True
    if not blocks["block"].is_unique:
        details.append({"check": "duplicate_blocks", "n": int(blocks["block"].duplicated().sum())})
        failed = True
    flagged: set[str] = set()
    differ: dict[str, set[str]] = {}
    joins: dict[str, str] = {}
    for chamber, districts in (("house", districts_house), ("senate", districts_senate)):
        ids = set(districts["geo_id"])
        bad = _membership(blocks, chamber, ids)
        details.extend(bad)
        flagged.update(d["block"] for d in bad)
        pops = blocks.groupby(chamber)["pop2020"].sum()
        for geo_id in sorted(ids):
            if pops.get(geo_id, 0) <= 0:
                details.append({"check": "empty_district", "chamber": chamber, "geo_id": geo_id})
        summary, rows = _agreement(blocks, points, districts, chamber, min_agreement, max_snap_m)
        details.append(summary)
        details.extend(rows)
        flagged.update(r["block"] for r in rows)
        differ[chamber] = {r["block"] for r in rows}
        joins[chamber] = (
            f"{summary['edge']:,} block interior points fell exactly on a district line, {summary['snapped']:,} "
            f"lay just outside every district and were matched to the nearest one within {max_snap_m:,.0f} m, "
            f"and {summary['unassigned']:,} could not be matched to any district"
        )
        notes.append(
            f"{chamber.title()}: the equivalency file and the spatial join agree for "
            f"{summary['share_agree']:.4%} of the population. {summary['blocks_disagree']:,} blocks "
            f"({summary['pop_disagree']:,} people) are assigned differently. Of these, "
            f"{summary['disagree_inside']:,} have their interior point inside another district, "
            f"{summary['disagree_snapped']:,} were matched to a nearest district and "
            f"{summary['disagree_unassigned']:,} could not be matched. {summary['disagree_zero_pop']:,} of the "
            f"{summary['blocks_disagree']:,} have no population, so they move nobody"
        )
        failed = failed or bool(bad) or not summary["passed"]
    if joins["house"] == joins["senate"]:
        notes.append(f"Spatial join, the same for both chambers: {joins['house']}")
    else:
        notes += [f"Spatial join, {c.title()}: {j}" for c, j in joins.items()]
    both = differ["house"] & differ["senate"]
    notes.append(
        f"{len(differ['house'] | differ['senate']):,} blocks differ in at least one chamber: the "
        f"{len(differ['house']):,} House and {len(differ['senate']):,} Senate blocks above, with the "
        f"{len(both):,} that differ in both counted once"
    )
    failed = failed or any(d["check"] == "empty_district" for d in details)
    no_zcta = blocks["zcta"].isna()
    notes.append(
        f"{int(no_zcta.sum()):,} blocks ({int(blocks.loc[no_zcta, 'pop2020'].sum()):,} people) have no ZIP "
        f"code tabulation area, and {int((blocks['pop2020'] == 0).sum()):,} blocks with no population are kept"
    )
    return CheckResult(
        name="blocks",
        passed=not failed,
        gate=True,
        n_compared=len(blocks),
        n_mismatch=len(flagged),
        details=details,
        notes=notes,
    )
