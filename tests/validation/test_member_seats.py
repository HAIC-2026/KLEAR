# Tests for the check that every district has exactly one listed member.
import pandas as pd

from klear.validation.members import check_members

PDF = {"house": "https://example.org/house.pdf", "senate": "https://example.org/senate.pdf"}
DISTRICTS = {"house": ["001", "002"], "senate": ["001"]}


def _members(rows):
    return pd.DataFrame(
        [
            {"geo_type": t, "geo_id": g, "name": n, "profile_url": u, "listed_on": "2026-10-05"}
            for t, g, n, u in rows
        ]
    )


def test_one_member_per_district_passes():
    df = _members(
        [
            ("house", "001", "A, One", "https://example.org/a"),
            ("house", "002", "B, Two", PDF["house"]),
            ("senate", "001", "C, Three", "https://example.org/c"),
        ]
    )
    res = check_members(df, DISTRICTS, PDF)
    assert res.passed and res.gate and res.n_compared == 3
    assert "House: 2 members for 2 districts" in res.notes[0] and "1 with their own page" in res.notes[0]


def test_missing_extra_unknown_and_repeated_members_fail():
    df = _members(
        [
            ("house", "001", "A, One", PDF["house"]),
            ("house", "001", "B, Two", PDF["house"]),
            ("house", "009", "A, One", PDF["house"]),
        ]
    )
    res = check_members(df, DISTRICTS, PDF)
    checks = sorted((d["check"], d.get("district")) for d in res.details)
    assert not res.passed
    assert checks == [
        ("duplicate_name", None),
        ("seats", "001"),
        ("seats", "001"),
        ("seats", "002"),
        ("unknown_district", "009"),
    ]
