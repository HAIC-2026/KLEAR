# Validation 8: every help site has a House and Senate district within snapping distance (gated).
import pandas as pd

from klear.etl.blocks import MAX_SNAP_M
from klear.validation import CheckResult


def check_sites(sites: pd.DataFrame) -> CheckResult:
    details: list[dict] = []
    notes: list[str] = []
    for r in sites.itertuples():
        if pd.isna(r.house) or pd.isna(r.senate):
            details.append({"check": "missing_district", "site_id": r.site_id})
        if r.snapped_m > MAX_SNAP_M:
            details.append({"check": "beyond_snap_distance", "site_id": r.site_id, "snapped_m": r.snapped_m})
        elif r.snapped_m > 0:
            notes.append(
                f"{r.site_id} ({r.name}) snapped {r.snapped_m:.0f} m into House {r.house}, Senate {r.senate}"
            )
    for sid in sorted(sites.site_id[sites.site_id.duplicated()].unique()):
        details.append({"check": "duplicate_site_id", "site_id": sid})
    notes.append(f"{len(sites)} sites checked, snapping limit {MAX_SNAP_M:,.0f} m")
    return CheckResult("sites", not details, True, len(sites), len(details), details, notes)
