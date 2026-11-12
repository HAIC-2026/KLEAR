# Validation: House and Senate district overlaps match their blocks and each district's shares sum to 1 (gated).
from klear.validation import CheckResult

TOLERANCE = 1e-9
OTHER = {"house": "senate", "senate": "house"}


def check_district_overlap(con) -> CheckResult:
    details: list[dict] = []
    blocks = con.execute("SELECT county, house, senate, pop2020 FROM blocks WHERE pop2020 > 0").df()
    overlap = con.execute("SELECT * FROM district_overlap").df()
    notes, n = [], 0
    for chamber, other in OTHER.items():
        ids = [
            r[0]
            for r in con.execute(
                "SELECT geo_id FROM geography WHERE geo_type = ? ORDER BY 1", [chamber]
            ).fetchall()
        ]
        n += len(ids)
        for geo_type in (other, "county"):
            part = overlap[(overlap["district_type"] == chamber) & (overlap["geo_type"] == geo_type)]
            sums = part.groupby("district_id")["share"].sum()
            for geo_id in ids:
                total = sums.get(geo_id)
                if total is None or abs(total - 1) > TOLERANCE:
                    details.append(
                        {
                            "check": "share_sum",
                            "district": f"{chamber}:{geo_id}",
                            "geo_type": geo_type,
                            "sum": total,
                        }
                    )
            want = blocks.groupby([chamber, geo_type])["pop2020"].sum().to_dict()
            have = {
                (d, g): p for d, g, p in part[["district_id", "geo_id", "pop2020"]].itertuples(index=False)
            }
            for key in sorted(want.keys() | have.keys()):
                if want.get(key) != have.get(key):
                    details.append(
                        {
                            "check": "pop_mismatch",
                            "district": f"{chamber}:{key[0]}",
                            "geo": f"{geo_type}:{key[1]}",
                            "blocks": want.get(key),
                            "stored": have.get(key),
                        }
                    )
        spans = _spans(overlap, chamber, other)
        notes.append(
            f"{chamber.title()}: {sum(c > 1 for c in spans.values())} of {len(ids)} districts hold people in more "
            f"than one {other.title()} district, at most {max(spans.values(), default=0)}"
        )
    return CheckResult("district_overlap", not details, True, n, len(details), details, notes)


def _spans(overlap, chamber: str, other: str) -> dict[str, int]:
    """Number of districts of the other chamber holding people of each district."""
    part = overlap[(overlap["district_type"] == chamber) & (overlap["geo_type"] == other)]
    return part.groupby("district_id")["geo_id"].nunique().to_dict()
