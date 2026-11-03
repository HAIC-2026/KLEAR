# Validation 9: place populations match their blocks, overlap shares sum to 1 and key towns are spelled exactly (gated).
import pandas as pd

from klear.etl.places_gazetteer import plain_name
from klear.validation import CheckResult

REQUIRED = {"kaneohe": "Kāneʻohe", "waianae": "Waiʻanae", "hilo": "Hilo", "kailua": "Kailua"}
TOLERANCE = 1e-9


def check_gazetteer(
    con, block_place: pd.Series, census: dict[str, str], overrides: dict[str, str]
) -> CheckResult:
    details: list[dict] = []
    place = con.execute("SELECT place_id, name, plain, pop2020 FROM place").df()
    blocks = con.execute("SELECT block, zcta, pop2020 FROM blocks").df()
    blocks["place_id"] = blocks["block"].map(block_place)
    blocks = blocks[blocks["place_id"].notna()]
    want = blocks.groupby("place_id")["pop2020"].sum()
    have = place.set_index("place_id")["pop2020"]
    for pid in sorted(set(want.index) | set(have.index)):
        if want.get(pid, 0) != have.get(pid, 0) and not (pid not in have.index and want[pid] == 0):
            details.append(
                {"check": "pop_mismatch", "place_id": pid, "expected": want.get(pid), "stored": have.get(pid)}
            )
    sums = {
        (p, t): s
        for p, t, s in con.execute(
            "SELECT place_id, geo_type, sum(share) FROM place_overlap GROUP BY ALL"
        ).fetchall()
    }
    with_zcta = set(blocks.loc[blocks["zcta"].notna() & (blocks["pop2020"] > 0), "place_id"])
    for pid in place.loc[place["pop2020"] > 0, "place_id"]:
        for geo_type in ("county", "house", "senate", "zcta"):
            if geo_type == "zcta" and pid not in with_zcta:
                continue
            total = sums.get((pid, geo_type))
            if total is None or abs(total - 1) > TOLERANCE:
                details.append({"check": "share_sum", "place_id": pid, "geo_type": geo_type, "sum": total})
    for plain, name in REQUIRED.items():
        found = place.loc[place["plain"] == plain, "name"].tolist()
        if name not in found:
            details.append({"check": "missing_place", "expected": name, "found": found})
    for pid, name in overrides.items():
        if pid not in census or plain_name(name) != plain_name(census[pid]):
            details.append({"check": "override_mismatch", "place_id": pid, "name": name})
    ascii_only = int(place["name"].map(str.isascii).sum())
    note = f"{len(place)} places, {len(place) - ascii_only} with a Hawaiian spelling, "
    notes = [note + f"{ascii_only} keep an ASCII-only name"]
    return CheckResult("gazetteer", not details, True, len(place), len(details), details, notes)
