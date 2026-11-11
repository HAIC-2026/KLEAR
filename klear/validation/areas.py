# Validation 14: every resident is on one island and in one board area or county division, shares sum to 1 and
# every board, division and other name matches its source (gated).
import pandas as pd

from klear.etl.areas import DIVISION_COUNTIES, HONOLULU, KINDS, Areas, Names
from klear.etl.places_gazetteer import plain_name
from klear.validation import CheckResult

TOLERANCE = 1e-9


def _unplaced(blocks: pd.DataFrame, where: pd.Series, column: str, check: str) -> list[dict]:
    lost = blocks[where & blocks[column].isna()]
    if lost.empty:
        return []
    return [
        {
            "check": check,
            "blocks": len(lost),
            "pop2020": int(lost["pop2020"].sum()),
            "first": lost["block"].iloc[0],
        }
    ]


def check_areas(areas: Areas, names: Names, census: dict[str, str], boards: set[int]) -> CheckResult:
    """census maps each division GEOID to its Census name; boards holds the board numbers in the City's layer."""
    details: list[dict] = []
    blocks = areas.blocks[areas.blocks["pop2020"] > 0]
    details += _unplaced(blocks, pd.Series(True, index=blocks.index), "island", "no_island")
    details += _unplaced(blocks, blocks["county"] == HONOLULU, "neighborhood", "no_neighborhood")
    details += _unplaced(blocks, blocks["county"].isin(DIVISION_COUNTIES), "division", "no_division")
    island_pop, state_pop = (
        int(areas.area.loc[areas.area["kind"] == "island", "pop2020"].sum()),
        int(blocks["pop2020"].sum()),
    )
    if island_pop != state_pop:
        details.append({"check": "island_total", "islands": island_pop, "state": state_pop})
    # A few populated blocks lie in no ZIP code area, so an area's ZIP shares add up to its share in one.
    pop = pd.concat([blocks.groupby(kind)["pop2020"].sum() for kind in KINDS])
    zipped = pd.concat([blocks[blocks["zcta"].notna()].groupby(kind)["pop2020"].sum() for kind in KINDS])
    sums = areas.overlap.groupby(["area_id", "geo_type"])["share"].sum()
    for area_id in areas.area["area_id"]:
        for geo_type in ("county", "house", "senate", "zcta"):
            have = pop.get(area_id, 0)
            want = zipped.get(area_id, 0) / have if geo_type == "zcta" and have else 1
            total = sums.get((area_id, geo_type), 0)
            if abs(total - want) > TOLERANCE:
                details.append({"check": "share_sum", "area_id": area_id, "geo_type": geo_type, "sum": total})
    for geo_id, name in names.divisions.items():
        if geo_id not in census or plain_name(name) != plain_name(census[geo_id]):
            details.append({"check": "division_name", "area_id": geo_id, "name": name})
    for board in sorted(boards ^ set(names.neighborhoods)):
        details.append({"check": "board_name", "board": board, "named": board in names.neighborhoods})
    # A misspelled or ambiguous alias target fails; a known name with no residents in this build only counts.
    empty = [u for u in areas.unmatched if u["known"] and u["matches"] == 0]
    details += [{"check": "alias_target", **u} for u in areas.unmatched if u not in empty]
    count = areas.area["kind"].value_counts()
    summary = (
        f"{count.get('island', 0)} islands with residents, {count.get('neighborhood', 0)} neighborhood board "
        f"areas, {count.get('division', 0)} county divisions, {areas.alias['plain'].nunique()} other names"
    )
    notes = [summary]
    if empty:
        notes.append(f"{len(empty)} other-name targets have no residents in this build and are left out")
    return CheckResult("areas", not details, True, len(areas.area), len(details), details, notes)
