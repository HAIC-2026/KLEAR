# Validation: every House and Senate district has exactly its expected number of listed members (gated).
import pandas as pd

from klear.validation import CheckResult

# Hawaiʻi elects one member per House and Senate district.
SEATS_PER_DISTRICT = 1


def check_members(
    members: pd.DataFrame, districts: dict[str, list[str]], fallbacks: dict[str, str]
) -> CheckResult:
    """districts maps each chamber to its district ids; fallbacks maps each chamber to the link a member gets
    when the profiles PDF links no page for them."""
    details: list[dict] = []
    notes: list[str] = []
    for chamber, ids in districts.items():
        mine = members[members.geo_type == chamber]
        counts = mine.geo_id.value_counts()
        for geo_id in ids:
            if (n := int(counts.get(geo_id, 0))) != SEATS_PER_DISTRICT:
                details.append({"check": "seats", "chamber": chamber, "district": geo_id, "listed": n})
        for geo_id in sorted(set(mine.geo_id) - set(ids)):
            details.append({"check": "unknown_district", "chamber": chamber, "district": geo_id})
        linked = int((mine.profile_url != fallbacks.get(chamber)).sum())
        dates = ", ".join(sorted(set(mine.listed_on)))
        notes.append(
            f"{chamber.title()}: {len(mine)} members for {len(ids)} districts, listing dated {dates}, "
            f"{linked} with their own page on capitol.hawaii.gov, the rest linked to the profiles PDF"
        )
    for name in sorted(members.name[members.name.duplicated()].unique()):
        details.append({"check": "duplicate_name", "name": name})
    n = sum(len(ids) for ids in districts.values())
    return CheckResult("members", not details, True, n, len(details), details, notes)
