# Tests for who represents a district in the agent tool, the answer checks, the free lookup and the profile.
import json

import pytest

from klear.agent.answer import build_answer
from klear.eval.contract import ContractError, answer_from_json, answer_to_json, render
from klear.web.assistant import basic
from klear.web.display import member_cards

AS_LISTED = "as listed on the Legislative Reference Bureau's Representatives at a Glance on November 12, 2026"
BEFORE = "before the November 3, 2026 general election"


def _run(session, tool, args):
    return json.loads(session.run(tool, args))


def test_find_member_by_district_and_by_exact_name(small):
    got = _run(small, "find_member", {"district": "house:022"})
    (m,) = got["members"]
    assert m["marker"] == "{member:house:022}" and m["listed_name"] == "KAʻAI, Leilani M."
    assert m["geo"] == "house:022" and m["name"] == "House District 22" and m["title"] == "Representative"
    assert m["listed"] == AS_LISTED and BEFORE in m["timing"]
    assert m["official_page"].startswith("https://")
    assert "party" not in json.dumps(got).casefold()
    assert _run(small, "find_member", {"name": "Noelani Kāʻai"})["members"][0]["geo"] == "senate:012"
    assert ("senate", "012") in small.members and ("senate", "012") in small.resolved


def test_find_member_lists_candidates_and_never_guesses(small):
    got = _run(small, "find_member", {"name": "kaai"})
    assert [m["geo"] for m in got["members"]] == ["house:022", "senate:012"] and "Several" in got["note"]
    none = _run(small, "find_member", {"name": "Kaaii"})
    assert none["members"] == [] and "exact name" in none["note"]
    assert "error" in _run(small, "find_member", {})
    assert "error" in _run(small, "find_member", {"district": "county:15003"})


def test_district_profile_names_the_member(small):
    got = _run(small, "district_profile", {"district": "house:022"})
    assert [m["marker"] for m in got["members"]] == ["{member:house:022}"]


def _args(blocks, refusal="none"):
    return {
        "resolved": "house:022",
        "refusal": refusal,
        "blocks": blocks,
        "facts": [],
        "programs": [],
        "caveats": [],
    }


def test_member_answer_renders_the_note_and_adds_the_timing_caveat(small):
    _run(small, "find_member", {"district": "house:022"})
    a, problems = build_answer(small, _args(["{geo:house:022} is represented by {member:house:022}."]))
    assert problems == []
    assert a.caveats == ["{listing:house:022}"]
    text = render(a, small.con)
    assert f"represented by Representative KAʻAI, Leilani M. ({AS_LISTED})." in text and BEFORE in text
    assert answer_from_json(answer_to_json(a)) == a


def test_member_markers_need_find_member_and_names_are_never_typed(small):
    _, problems = build_answer(small, _args(["{geo:house:022} is represented by {member:house:022}."]))
    assert any("call find_member first" in p for p in problems)
    _run(small, "find_member", {"district": "house:022"})
    _, problems = build_answer(small, _args(["{geo:house:022} is represented by Leilani Kaai."]))
    assert any("never typed out" in p for p in problems)


def test_partisan_declines_may_not_name_members(small):
    _run(small, "find_member", {"district": "house:022"})
    _, problems = build_answer(small, _args(["I do not advise on {member:house:022}."], "decline_partisan"))
    assert any("use only geo markers" in p for p in problems)


def test_member_marker_needs_a_district():
    with pytest.raises(ContractError):
        answer_from_json(
            {
                "contract": "1",
                "resolved": None,
                "refusal": "none",
                "blocks": ["{member:county:15003}"],
                "facts": [],
                "programs": [],
                "caveats": [],
            }
        )


@pytest.mark.parametrize(
    "question, district, name",
    [
        ("Who represents House District 22?", ("senate", "012"), "Representative KAʻAI, Leilani M."),
        ("Who represents HD 23?", ("house", "022"), "Representative PALAKIKO, Kawika"),
        ("Who is my senator?", ("senate", "012"), "Senator KAʻAI, Noelani"),
    ],
)
def test_free_lookup_answers_who_represents_a_district(small_con, question, district, name):
    r = basic(small_con, question, district)
    assert r["refusal"] == "none" and r["facts"] == []
    assert f"is represented by {name}, as listed on the Legislative Reference Bureau's" in r["text"]
    assert BEFORE in r["text"] and r["members"][0]["url"].startswith("https://")


def test_free_lookup_answers_which_district_a_member_represents(small_con):
    r = basic(small_con, "Which district does Representative Leilani Kaai represent?", ("house", "023"))
    assert r["text"].startswith(
        f"Representative KAʻAI, Leilani M. represents House District 22, {AS_LISTED}."
    )
    assert r["district"] == {"geo": "house:022", "name": "House District 22"}
    both = basic(small_con, "Which district does Kaʻai represent?", ("house", "022"))
    assert both["text"].startswith("Several listed members match that name:\n- Representative KAʻAI")
    assert "Senate District 12" in both["text"] and len(both["members"]) == 2
    missing = basic(small_con, "Which district does Representative Kaaii represent?", ("house", "022"))
    assert "could not find a listed member with that exact name" in missing["text"]


def test_free_lookup_asks_for_a_district_instead_of_guessing_from_a_town(small_con):
    r = basic(small_con, "Who represents Kailua?", ("house", "022"))
    assert "Name the district" in r["text"] and not r.get("members")


@pytest.mark.parametrize(
    "question",
    [
        "Should I vote for Representative Kaʻai?",
        "Which candidate should win House District 22, Kaai or someone else?",
        "Is Senator Kaai a Democrat or a Republican?",
    ],
)
def test_free_lookup_still_declines_partisan_questions_about_members(small_con, question):
    r = basic(small_con, question, ("house", "022"))
    assert r["refusal"] == "decline_partisan" and "KAʻAI" not in r["text"]


def test_data_questions_are_not_taken_for_member_questions(small_con):
    r = basic(small_con, "How many kūpuna live in this district?", ("house", "022"))
    assert not r.get("members") and r["facts"]


def test_member_cards_link_the_official_page_and_the_listing(small_con):
    (card,) = member_cards(small_con, [("senate", "012")])
    assert card["title"] == "Senator" and card["district"] == "Senate District 12"
    assert card["source"] == "Legislative Reference Bureau, Senators at a Glance"
    assert card["source_url"] == "https://lrb.hawaii.gov/par/current-legislature/"
