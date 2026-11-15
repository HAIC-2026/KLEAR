# Tests for finding listed legislators by district or exact name, and the note each mention carries.
from dataclasses import replace
from datetime import date

import pytest

from klear.members import (
    as_listed,
    by_name,
    members_of,
    mention,
    named_in,
    reading_name,
    starting_with,
    timing,
)
from tests.fixtures.small_store import build_small_store


@pytest.fixture
def con(tmp_path):
    con = build_small_store(tmp_path / "small.duckdb")
    yield con
    con.close()


def _names(found):
    return [(m.geo, m.name) for m in found]


@pytest.mark.parametrize(
    "typed",
    ["Leilani Kaai", "KAʻAI, Leilani M.", "leilani kaʻai", "Representative Leilani Kāʻai", "Leilani M Kaai"],
)
def test_names_match_ignoring_case_okina_kahako_and_titles(con, typed):
    assert _names(by_name(con, typed)) == [("house:022", "KAʻAI, Leilani M.")]


def test_a_shared_surname_lists_every_candidate_unless_a_title_narrows_it(con):
    assert _names(by_name(con, "Kaai")) == [
        ("house:022", "KAʻAI, Leilani M."),
        ("senate:012", "KAʻAI, Noelani"),
    ]
    assert _names(by_name(con, "Senator Kaai")) == [("senate:012", "KAʻAI, Noelani")]


@pytest.mark.parametrize("typed", ["Kai", "Leilani", "Leilani Kaaii", "Mele Kaai", "Senator", ""])
def test_only_exact_names_match(con, typed):
    assert by_name(con, typed) == []


@pytest.mark.parametrize(
    ("listed", "reads"),
    [
        ("GARRETT, Andrew Takuya", "Andrew Takuya Garrett"),
        ("KAʻAI, Leilani M.", "Leilani M. Kaʻai"),
        ("McKELVEY, Angus L.K.", "Angus L.K. McKelvey"),
        ("KEOHOKAPU-LEE LOY, Sue L.", "Sue L. Keohokapu-Lee Loy"),
        ("IHARA JR., Les", "Les Ihara Jr."),
        ("DOE III, Jane", "Jane Doe III"),
        ("Jane Doe", "Jane Doe"),
    ],
)
def test_listed_names_read_given_name_first(listed, reads):
    assert reading_name(listed) == reads


def test_the_search_box_finds_members_by_the_start_of_any_name_word(con):
    assert _names(starting_with(con, "pala", 3)) == [("house:023", "PALAKIKO, Kawika")]
    assert _names(starting_with(con, "Rep. Kaʻai", 3)) == [("house:022", "KAʻAI, Leilani M.")]
    assert _names(starting_with(con, "noe ka", 3)) == [("senate:012", "KAʻAI, Noelani")]
    assert len(starting_with(con, "kaai", 3)) == 2 and starting_with(con, "kaai", 1)[0].geo == "house:022"
    assert starting_with(con, "Senator", 3) == starting_with(con, "mele", 3) == []


def test_names_in_a_question_narrow_by_given_name_and_title(con):
    assert _names(named_in(con, "Which district does Noelani Kaʻai represent?")) == [
        ("senate:012", "KAʻAI, Noelani")
    ]
    assert _names(named_in(con, "Which district does Rep. Kaai represent?")) == [
        ("house:022", "KAʻAI, Leilani M.")
    ]
    assert len(named_in(con, "Which district does Kaai represent?")) == 2
    assert named_in(con, "How many kūpuna live here?") == []


@pytest.mark.parametrize(
    "question",
    [
        "Which district does Representative Mele Kaʻai represent?",
        "Which district does Mele Kaai represent?",
        "which district does mele kaai represent",
        "Which district does Senator Leilani Kaʻai represent?",
        "Who represents the kaai side of the island?",
        "Which district does Kaʻai Village belong to?",
    ],
)
def test_names_in_a_question_never_match_under_another_given_name_or_title(con, question):
    # by_name rejects the same names typed on their own.
    assert named_in(con, question) == []


def test_names_in_a_question_match_given_names_in_either_order_and_possessives(con):
    leilani = [("house:022", "KAʻAI, Leilani M.")]
    assert _names(named_in(con, "Which district does KAʻAI, Leilani M. represent?")) == leilani
    assert _names(named_in(con, "Who represents Leilani Kaai's district?")) == leilani
    assert _names(named_in(con, "which district does leilani m kaai represent")) == leilani
    assert len(named_in(con, "Who represents Kaʻai's district?")) == 2


def test_a_surname_that_is_a_place_name_needs_a_title(con):
    con.execute("UPDATE member SET name = 'KAILUA, Pua' WHERE geo_id = '023'")
    assert named_in(con, "Who represents Kailua?") == []
    assert _names(named_in(con, "Which district does Representative Kailua represent?")) == [
        ("house:023", "KAILUA, Pua")
    ]


def test_every_mention_says_where_and_when_it_was_listed(con):
    (m,) = members_of(con, "house", "022")
    assert as_listed(m) == (
        "as listed on the Legislative Reference Bureau's Representatives at a Glance on November 12, 2026"
    )
    assert mention([m]) == f"Representative KAʻAI, Leilani M. ({as_listed(m)})"
    assert mention([]) == ""


def test_timing_says_plainly_when_the_listing_may_predate_the_results(con):
    (m,) = members_of(con, "house", "022")
    assert "dated October 5, 2026, before the November 3, 2026 general election" in timing(m)
    later = replace(m, listed_on=date(2026, 12, 1))
    assert "may predate the November 3, 2026 general election results taking effect" in timing(later)
