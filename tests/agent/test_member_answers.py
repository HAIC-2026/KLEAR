# Tests for who represents a district in the agent tool, the answer checks, the free lookup and the profile.
import json

import pytest

from klear.agent.answer import build_answer
from klear.agent.loop import KlearAgent
from klear.eval.contract import ContractError, answer_from_json, answer_to_json, render
from klear.members import members_of
from klear.web.assistant import basic
from klear.web.display import member_cards
from tests.agent.fake_client import FakeClient, tool

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


# A district, town or chamber the question names comes before the selected district, in both paths.
@pytest.mark.parametrize(
    "question, selected, name",
    [
        ("Who represents House District 23?", ("house", "022"), "Representative PALAKIKO, Kawika"),
        ("Who is my state senator?", ("house", "023"), "Senator KAʻAI, Noelani"),
        ("Who is the senator for House District 22?", None, "Senator KAʻAI, Noelani"),
    ],
)
def test_free_lookup_answers_for_the_seat_the_question_names(small_con, question, selected, name):
    r = basic(small_con, question, selected)
    assert f"represented by {name}, as listed on" in r["text"]
    assert [c["title"] + " " + c["name"] for c in r["members"]] == [name]


def test_free_lookup_never_names_the_selected_member_for_another_district(small_con):
    r = basic(small_con, "Who represents House District 18?", ("house", "022"))
    assert "KAʻAI" not in r["text"] and not r.get("members")
    assert "House districts" in r["text"] and "Name the district" in r["text"]


def test_free_lookup_lists_every_member_of_a_town_that_spans_districts(small_con):
    r = basic(small_con, "Who represents Kāneʻohe?", ("house", "022"))
    assert r["text"].startswith("Kāneʻohe spans more than one district.")
    assert (
        "- House District 22, with most of Kāneʻohe's residents: Representative KAʻAI, Leilani M."
        in r["text"]
    )
    assert (
        "- House District 23, with part of Kāneʻohe's residents: Representative PALAKIKO, Kawika" in r["text"]
    )
    assert "- Senate District 12, with all of Kāneʻohe's residents: Senator KAʻAI, Noelani" in r["text"]
    assert "Names of representatives are as listed on" in r["text"] and BEFORE in r["text"]
    assert len(r["members"]) == 3 and "district" not in r
    senate = basic(small_con, "Who is the senator for Kāneʻohe?", ("house", "022"))
    assert senate["text"].startswith(
        "Kāneʻohe is in Senate District 12, which is represented by Senator KAʻAI"
    )


def test_free_lookup_asks_which_place_when_a_town_name_is_shared(small_con):
    r = basic(small_con, "Who represents Kailua?", ("house", "022"))
    assert r["text"].startswith("Your question could mean Kailua on Oʻahu or Kailua on Hawaiʻi Island.")
    assert "KAʻAI" not in r["text"] and not r.get("members")


def test_free_lookup_asks_for_a_district_when_none_is_chosen(small_con):
    r = basic(small_con, "Who is my representative?", None)
    assert "Name the district" in r["text"] and not r.get("members")


@pytest.mark.parametrize(
    "question",
    [
        "Which district does Representative Mele Kaʻai represent?",
        "Which district does Mele Kaai represent?",
        "which district does mele kaai represent",
        "Which district does Senator Leilani Kaʻai represent?",
    ],
)
def test_free_lookup_never_matches_a_surname_under_another_given_name_or_title(small_con, question):
    r = basic(small_con, question, ("house", "022"))
    assert "KAʻAI" not in r["text"] and not r.get("members")
    assert "could not find a listed member with that exact name" in r["text"]


def _seat_args(blocks, resolved="house:022"):
    return _args(blocks) | {"resolved": resolved}


def test_agent_answer_may_not_name_the_selected_member_for_another_district(small):
    small.question, small.context = "Who represents House District 23?", ("house", "022")
    _run(small, "find_member", {"district": "house:022"})
    _, problems = build_answer(small, _seat_args(["{geo:house:022} is represented by {member:house:022}."]))
    assert any("{member:house:022} holds a seat the question does not ask about" in p for p in problems)
    assert any("it asks about house:023" in p for p in problems)
    _run(small, "find_member", {"district": "house:023"})
    a, problems = build_answer(
        small, _seat_args(["{geo:house:023} is represented by {member:house:023}."], "house:023")
    )
    assert problems == [] and "PALAKIKO" in render(a, small.con)


def test_agent_answer_names_every_seat_a_town_spans(small):
    small.question, small.context = "Who represents Kāneʻohe?", ("house", "022")
    for d in ("house:022", "house:023", "senate:012"):
        _run(small, "find_member", {"district": d})
    _, problems = build_answer(small, _seat_args(["{member:house:022} represents part of it."]))
    assert any("instead of leaving out house:023, senate:012" in p for p in problems)
    every = "{member:house:022}, {member:house:023} and {member:senate:012} represent parts of it."
    assert build_answer(small, _seat_args([every]))[1] == []


def test_agent_answer_gives_the_senator_when_asked_for_the_other_chamber(small):
    small.question, small.context = "Who is my state senator?", ("house", "023")
    for d in ("house:023", "senate:012"):
        _run(small, "find_member", {"district": d})
    _, problems = build_answer(small, _seat_args(["{member:house:023} represents you."], "house:023"))
    assert any("it asks about senate:012" in p for p in problems)
    assert build_answer(small, _seat_args(["{member:senate:012} represents you."], "senate:012"))[1] == []


def test_agent_answer_lists_every_member_sharing_a_surname(small):
    small.question, small.context = "Which district does Kaʻai represent?", ("house", "023")
    _run(small, "find_member", {"name": "Kaʻai"})
    _, problems = build_answer(small, _seat_args(["{member:house:022} represents {geo:house:022}."]))
    assert any("instead of leaving out senate:012" in p for p in problems)
    both = (
        "{member:house:022} represents {geo:house:022}, and {member:senate:012} represents {geo:senate:012}."
    )
    assert build_answer(small, _seat_args([both]))[1] == []


def test_agent_answer_may_not_name_a_member_for_a_wrong_given_name(small):
    small.question, small.context = (
        "Which district does Representative Mele Kaʻai represent?",
        ("house", "023"),
    )
    _run(small, "find_member", {"name": "Kaʻai"})
    _, problems = build_answer(small, _seat_args(["{member:house:022} represents {geo:house:022}."]))
    assert any("{member:house:022} holds a seat the question does not ask about" in p for p in problems)


# The exact questions from review, on the built store.
def test_built_store_answers_the_named_district_and_town_not_the_selected_one(real_con):
    selected = ("house", "022")
    (mine,) = members_of(real_con, *selected)
    r = basic(real_con, "Who represents House District 18?", selected)
    assert r["text"].startswith("House District 18 is represented by ") and mine.name not in r["text"]
    hilo = basic(real_con, "Who represents Hilo?", selected)
    assert hilo["text"].startswith("Hilo spans more than one district.") and mine.name not in hilo["text"]
    assert {c["district"] for c in hilo["members"]} == {
        "House District 1",
        "House District 2",
        "House District 3",
        "Senate District 1",
    }
    senator = basic(real_con, "Who is my state senator?", selected)
    assert all(c["title"] == "Senator" for c in senator["members"]) and mine.name not in senator["text"]


def test_agent_loop_sends_back_the_selected_member_and_answers_for_the_named_district(small_con):
    def submit(blocks, resolved):
        return tool("submit_answer", _args(blocks) | {"resolved": resolved}, "tu_submit")

    fake = FakeClient(
        [
            tool("find_member", {"district": "house:022"}),
            submit(["{geo:house:022} is represented by {member:house:022}."], "house:022"),
            tool("find_member", {"district": "house:023"}),
            submit(["{geo:house:023} is represented by {member:house:023}."], "house:023"),
        ]
    )
    answer, _ = KlearAgent(small_con, client=fake).answer(
        "x", "Who represents House District 23?", ("house", "022")
    )
    sent = json.dumps(fake.requests[2]["messages"][-1]["content"], default=vars)
    assert "holds a seat the question does not ask about" in sent
    text = render(answer, small_con)
    assert answer.resolved == ("house", "023") and "PALAKIKO, Kawika" in text and "KAʻAI" not in text


def test_free_lookup_lists_each_district_the_question_names(small_con):
    r = basic(small_con, "Who represents House District 22 and House District 23?", None)
    assert r["text"].startswith(
        "- House District 22 is represented by Representative KAʻAI, Leilani M.\n"
        "- House District 23 is represented by Representative PALAKIKO, Kawika.\n\nNames of representatives are "
    )
    assert len(r["members"]) == 2
