# Tests for reading the Legislature's member listings.
import json
from datetime import date

import pytest

from klear.etl import members
from klear.store.db import MEMBER_COLS

HOUSE = """Thirty-Third Legislature, 2025–2026
Representatives at a Glance
District
# Party REPRESENTATIVE Committee CHAIR Committee Vice Chair Committee Assignments Room Phone (808)
41 R ALCOS III, David (Asst. Minority Leader)   FIN, HLT, HSH  324 586-6080
11 D AMATO, Terez   HED EDN, HLT, HSH 328 586-8525
2 D KEOHOKAPU-LEE LOY, Sue L.   HLT FIN, HSH 332 586-6120
12 D MORIWAKI, Sharon Y .  WAM GVO, LBT 215 586-6740
4 D RICHARDS III, Herbert M. “Tim”  (Assistant Majority Whip) HWN AEN EIG, WAM 202 586-6760
18 D BASS, Danielle M.M.     217 586-7100
 HSG – Housing;
Public Access Room (PAR)                https://lrb.hawaii.gov/par/                  808-587-0478
                10/5/2026
"""


@pytest.fixture
def listing(monkeypatch, tmp_path):
    path = tmp_path / "glance.pdf"
    path.write_bytes(b"%PDF")
    monkeypatch.setattr(members, "_text", lambda p: HOUSE)
    return path


def test_listing_rows_keep_names_as_printed_without_party_or_office(listing):
    df = members.parse_listing(listing)
    assert list(df.itertuples(index=False, name=None)) == [
        (41, "ALCOS III, David"),
        (11, "AMATO, Terez"),
        (2, "KEOHOKAPU-LEE LOY, Sue L."),
        (12, "MORIWAKI, Sharon Y."),
        (4, "RICHARDS III, Herbert M. “Tim”"),
        (18, "BASS, Danielle M.M."),
    ]


def test_listing_date_and_legislature_come_from_the_page(listing):
    assert members.listing_date(listing) == date(2026, 10, 5)
    assert members.legislature(listing) == "Thirty-Third Legislature, 2025-2026"


def test_member_rows_link_each_page_or_fall_back_to_the_profiles(listing, monkeypatch):
    links = {"alcosiii": "https://www.capitol.hawaii.gov/legislature/memberpage.aspx?member=1"}
    monkeypatch.setattr(members, "profile_links", lambda p: links)
    df = members.members_frame("house", listing, listing, date(2026, 11, 12))
    assert list(df.columns) == MEMBER_COLS
    assert "party" not in " ".join(df.columns)
    first = df.iloc[0]
    assert (first.geo_id, first.profile_url) == ("041", links["alcosiii"])
    assert (df.profile_url == members.CHAMBERS["house"][3]).sum() == len(df) - 1
    assert set(df.listed_on) == {"2026-10-05"} and set(df.retrieved_on) == {"2026-11-12"}


def test_surname_keys_match_across_the_two_listings():
    assert members.surname_key("SAN  BUENAVENTURA , Joy A. (D)") == members.surname_key(
        "SAN BUENAVENTURA, Joy A."
    )
    assert members.surname_key("McKELVEY, Angus L.K.") == "mckelvey"


def test_retrieval_date_is_the_day_in_hawaii(tmp_path):
    path = tmp_path / "glance.pdf"
    meta = {"retrieved_at": "2026-11-13T03:13:48+00:00"}
    (tmp_path / "glance.pdf.meta.json").write_text(json.dumps(meta), encoding="utf-8")
    assert members._retrieved(path) == date(2026, 11, 12)
