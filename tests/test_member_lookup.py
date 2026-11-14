# Tests for finding listed legislators by district or exact name, and the note each mention carries.
from dataclasses import replace
from datetime import date

import pytest

from klear.members import as_listed, by_name, members_of, mention, named_in, timing
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


def test_names_in_a_question_narrow_by_given_name_and_title(con):
    assert _names(named_in(con, "Which district does Noelani Kaʻai represent?")) == [
        ("senate:012", "KAʻAI, Noelani")
    ]
    assert _names(named_in(con, "Which district does Rep. Kaai represent?")) == [
        ("house:022", "KAʻAI, Leilani M.")
    ]
    assert len(named_in(con, "Which district does Kaai represent?")) == 2
    assert named_in(con, "How many kūpuna live here?") == []


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
