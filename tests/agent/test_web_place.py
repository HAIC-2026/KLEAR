"""Questions that name their own place, asked with no district chosen, and follow-ups that keep their subject."""

import pytest

from klear.agent.earlier import Exchange, earlier_block, parse_earlier
from klear.web.answers import NAME_A_PLACE, NEEDS_ASSISTANT
from klear.web.assistant import Assistant, DailyBudget, basic
from tests.agent.fake_client import FakeClient, tool

KUPUNA = "How many kūpuna live in House District 22?"


def places(result: dict) -> set[str]:
    return {f["geography"] for f in result["facts"]}


def test_a_town_in_one_district_is_answered_for_it_and_chooses_it(small_con):
    result = basic(small_con, "How many kūpuna live in Waikīkī?", None)
    assert result["lead"][0] == "Waikīkī is in House District 22."
    assert places(result) == {"House District 22"}
    assert result["district"] == {"geo": "house:022", "name": "House District 22"}
    assert result["subject"]["geo"] == "house:022" and "pop_65_plus" in result["subject"]["indicators"]


def test_a_town_across_districts_offers_the_search_choices(small_con):
    result = basic(small_con, "How many kupuna live in Kaneohe?", None)
    assert result["text"].startswith("Kāneʻohe spans more than one House district. Choose a district below")
    [match] = result["choices"]
    assert [o["geo"] for o in match["overlaps"]][:2] == ["house:022", "house:023"]
    assert result["facts"] == [] and "district" not in result and "subject" not in result


def test_a_town_partly_in_the_chosen_district_is_answered_for_that_district(small_con):
    result = basic(small_con, "How many kūpuna live in Kāneʻohe?", ("house", "023"))
    assert "choices" not in result and result["lead"][0] == "Kāneʻohe is partly in House District 23."
    assert places(result) == {"House District 23"}


def test_a_named_district_replaces_the_chosen_one(small_con):
    result = basic(small_con, KUPUNA, ("house", "023"))
    assert places(result) == {"House District 22"}
    assert result["district"] == {"geo": "house:022", "name": "House District 22"}


def test_a_county_or_the_whole_state_is_answered_for_itself(small_con):
    county = basic(small_con, "How many kūpuna live in Honolulu County?", None)
    assert places(county) == {"Honolulu County"} and county["subject"]["geo"] == "county:15003"
    assert "district" not in county
    state = basic(small_con, "How many kūpuna live across the whole state?", None)
    assert places(state) == {"Hawaiʻi"} and state["subject"]["name"] == "Hawaiʻi"
    programs = basic(small_con, "What support programs could residents of Honolulu County explore?", None)
    assert programs["text"].startswith("Programs are matched to one House or Senate district.")


@pytest.mark.parametrize("district", [None, ("house", "022")])
def test_a_question_ranking_every_district_needs_the_assistant(small_con, district):
    result = basic(small_con, "Which Senate districts have the highest uninsured rate?", district)
    assert result["text"] == NEEDS_ASSISTANT and result["facts"] == []


def test_a_question_with_no_place_says_what_to_add(small_con):
    assert basic(small_con, "How many kūpuna live here?", None)["text"] == NAME_A_PLACE
    missing = basic(small_con, "How many kūpuna live in House District 99?", None)["text"]
    assert missing.startswith("Hawaiʻi has") and missing.endswith(NAME_A_PLACE)


def test_a_follow_up_keeps_the_measure_and_place_of_the_last_answer(small_con):
    first = basic(small_con, KUPUNA, None)
    earlier = (Exchange(KUPUNA, ("house", "022"), tuple(first["subject"]["indicators"])),)
    for district in (None, ("house", "022")):
        follow = basic(small_con, "And how does that compare with the county?", district, earlier=earlier)
        assert places(follow) == {"House District 22", "Honolulu County", "Hawaiʻi"}
        assert {f["label"] for f in follow["facts"]} == {f["label"] for f in first["facts"]}
    alone = basic(small_con, "And how does that compare with the county?", ("house", "022"))
    assert alone["refusal"] == "not_available"
    # A question with its own topic keeps it, and only borrows the place.
    poverty = basic(small_con, "What about the poverty rate there?", None, earlier=earlier)
    assert places(poverty) <= {"House District 22"}
    assert not {f["label"] for f in poverty["facts"]} & {"Kūpuna (65 and over)"}


def test_earlier_exchanges_are_checked_and_unknown_ids_dropped(small_con):
    assert parse_earlier(small_con, None) == ()
    raw = [
        {"question": " q ", "district": "house:22", "indicators": ["pop_65_plus", "nope", "pop_65_plus"]},
        {"question": "r", "district": "zcta:96744"},
    ]
    assert parse_earlier(small_con, raw) == (
        Exchange("q", ("house", "022"), ("pop_65_plus",)),
        Exchange("r", None, ()),
    )
    assert parse_earlier(small_con, [{"question": "q", "district": "house:099"}])[0].district is None


@pytest.mark.parametrize(
    "raw",
    [
        "q",
        [{"question": "q"}] * 3,
        [{"question": "q", "answer": "made-up figures"}],
        [{"question": "x" * 501}],
        [{"question": " "}],
        [{"question": "q", "district": 22}],
        [{"question": "q", "indicators": "pop_65_plus"}],
        [{"question": "q", "indicators": ["Pop 65"]}],
        [{"question": "q", "indicators": ["pop_65_plus"] * 41}],
    ],
)
def test_earlier_exchanges_of_the_wrong_shape_or_size_are_refused(small_con, raw):
    with pytest.raises(ValueError):
        parse_earlier(small_con, raw)


def test_the_earlier_block_names_questions_places_and_measures_only(small_con):
    block = earlier_block(small_con, (Exchange(KUPUNA, ("house", "022"), ("pop_65_plus",)),))
    assert block == (
        "Earlier in this conversation, oldest first:\n"
        f"- Question: {KUPUNA}\n  Answered for: House District 22 (house:022)\n  Measures shown: pop_65_plus"
    )
    assert earlier_block(small_con, ()) == ""


SPANS = {
    "resolved": None,
    "refusal": "none",
    "blocks": ["Kāneʻohe spans more than one House district."],
    "facts": [],
    "programs": [],
    "caveats": [],
}


def test_the_assistant_gets_earlier_questions_and_offers_choices_for_a_town(small_con, tmp_path):
    fake = FakeClient(
        [tool("resolve_place", {"text": "Kāneʻohe"}), tool("submit_answer", SPANS, "tu_submit")]
    )
    earlier = (Exchange(KUPUNA, ("house", "022"), ("pop_65_plus",)),)
    budget = DailyBudget(5.0, tmp_path / "web_spend.json")
    result = Assistant(budget, client=fake).ask(small_con, "And in Kāneʻohe?", None, earlier=earlier)
    assert result["mode"] == "assistant" and [m["name"] for m in result["choices"]] == ["Kāneʻohe"]
    sent = fake.requests[0]["messages"][0]["content"][0]["text"]
    assert sent.startswith("And in Kāneʻohe?\n\nEarlier in this conversation")
    assert "Selected district" not in sent and "Measures shown: pop_65_plus" in sent


def test_an_assistant_answer_about_a_county_keeps_it_as_its_subject(small_con, tmp_path):
    county = {
        "resolved": None,
        "refusal": "none",
        "blocks": ["{label:f1} in {geo:county:15003}: {fact:f1} ({vintage:f1})."],
        "facts": ["f1"],
        "programs": [],
        "caveats": [],
    }
    script = [
        tool("resolve_place", {"text": "Honolulu County"}),
        tool("get_facts", {"indicator_ids": ["pop_65_plus"], "geos": ["county:15003"]}),
        tool("submit_answer", county, "tu_submit"),
    ]
    budget = DailyBudget(5.0, tmp_path / "web_spend.json")
    result = Assistant(budget, client=FakeClient(script)).ask(small_con, "Kūpuna in Honolulu County?", None)
    assert result["subject"] == {
        "geo": "county:15003",
        "name": "Honolulu County",
        "indicators": ["pop_65_plus"],
        "places": ["county:15003"],
    }
    assert "district" not in result and "choices" not in result
