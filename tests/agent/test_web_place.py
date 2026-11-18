"""Questions that name their own place, asked with no district chosen, and follow-ups that keep their subject."""

import pandas as pd
import pytest
import yaml

from klear.agent.earlier import Exchange, earlier_block, parse_earlier
from klear.eval.agents import ScriptClient, load_script
from klear.eval.golden import ROOT
from klear.store.db import add_coverage, add_facts, add_indicators
from klear.web.answers import NAME_A_PLACE
from klear.web.assistant import Assistant, DailyBudget, basic
from tests.agent.fake_client import FakeClient, tool
from tests.fixtures.small_store import _fact

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
def test_a_ranking_the_store_cannot_make_says_so_and_points_to_rankings(small_con, district):
    result = basic(small_con, "Which Senate districts have the highest uninsured rate?", district)
    assert result["refusal"] == "not_available" and result["facts"] == []
    assert result["text"].startswith("I could not match that ranking to a published district measure.")
    assert result["actions"] == [{"text": "Open Rankings", "url": "/rankings"}]
    assert "elsewhere" not in result


@pytest.mark.parametrize("district", [None, ("house", "022"), ("senate", "012")])
def test_a_ranking_question_is_answered_from_the_store_with_its_rankings_link(small_con, district):
    result = basic(small_con, "Which House districts have the most kūpuna?", district)
    lead = "\n".join(result["lead"])
    assert result["refusal"] == "none" and result["mode"] == "basic"
    assert lead.startswith("House districts with the most kūpuna (65 and over), from KLEAR's data store:")
    # House 22's range sits above House 23's, so each is a group of its own, highest first.
    assert "- First group: House District 22 (1,000 ± 100)." in lead
    assert "- Next group: House District 23 (100 ± 80)." in lead
    assert {f["geography"] for f in result["facts"]} == {"House District 22", "House District 23"}
    assert "district" not in result and result["subject"]["indicators"] == ["pop_65_plus"]
    lowest = basic(small_con, "Which House districts have the fewest kūpuna?", district)
    assert "- First group: House District 23 (100 ± 80)." in "\n".join(lowest["lead"])


def _kupuna_share(con):
    add_indicators(
        con,
        pd.DataFrame(
            [("pct_65_plus", "Percent of population 65 years and over", "percent", "people", None, False)],
            columns=["indicator_id", "label", "unit", "universe", "acs_var", "additive"],
        ),
    )
    add_coverage(
        con,
        pd.DataFrame([("pct_65_plus", g) for g in ("house", "senate")], columns=["indicator_id", "geo_type"]),
    )
    add_facts(
        con,
        pd.DataFrame(
            [
                _fact("house", "022", "pct_65_plus", 18.5, 1.5),
                _fact("house", "023", "pct_65_plus", 22.0, 1.6),
                _fact("senate", "012", "pct_65_plus", 19.0, 1.2),
            ]
        ),
    )
    return con


@pytest.mark.parametrize(
    ("question", "chamber", "first"),
    [
        (
            "Which House districts have the highest share of kūpuna?",
            "house",
            "House District 23 (22.0% ± 1.6 points)",
        ),
        (
            "Which Senate districts have the highest share of kūpuna?",
            "senate",
            "Senate District 12 (19.0% ± 1.2 points)",
        ),
    ],
)
def test_the_kupuna_ranking_starter_is_answered_with_its_measure_and_chamber(
    small_con, question, chamber, first
):
    result = basic(_kupuna_share(small_con), question, None)
    assert result["refusal"] == "none" and result["subject"]["indicators"] == ["pct_65_plus"]
    assert f"- First group: {first}." in result["lead"][0]
    assert result["actions"] == [
        {
            "text": f"See every {chamber.title()} district ranked and on the map",
            "url": f"/rankings?measure=pct_65_plus&chamber={chamber}",
        }
    ]
    # A share is never answered with a count, which would rank districts by their size.
    count_only = basic(small_con, "Which House districts have the highest share of keiki?", None)
    assert count_only["refusal"] == "not_available" and count_only["facts"] == []


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


def _golden(golden_id: str) -> dict:
    return yaml.safe_load((ROOT / "eval/golden/follow_up" / f"{golden_id}.yaml").read_text(encoding="utf-8"))


def _earlier(golden: dict) -> tuple[Exchange, ...]:
    return tuple(
        Exchange(e["question"], tuple(e["district"]), tuple(e["indicators"])) for e in golden["earlier"]
    )


@pytest.mark.parametrize("district", [None, ("house", "022")])
def test_a_follow_up_comparing_with_the_whole_state_keeps_its_place(small_con, district):
    first = basic(small_con, KUPUNA, None)
    earlier = (Exchange(KUPUNA, ("house", "022"), tuple(first["subject"]["indicators"])),)
    for question in (
        "How does that compare with Hawaiʻi as a whole?",
        "Is that higher than across the state?",
    ):
        follow = basic(small_con, question, district, earlier=earlier)
        assert places(follow) == {"House District 22", "Hawaiʻi"}
        assert follow["subject"]["geo"] == "house:022"
        assert "District and state figures are shown side by side." in follow["text"]


def test_the_golden_county_follow_up_compares_the_county_with_the_state(real_con):
    golden = _golden("follow_up-006")
    result = basic(real_con, golden["question"], None, earlier=_earlier(golden))
    assert {tuple(f) for f in golden["facts"]} <= {
        (i, *p.split(":")) for i in result["subject"]["indicators"] for p in result["subject"]["places"]
    }
    assert result["subject"]["geo"] == "county:15009"
    assert places(result) == {"Maui County", "Hawaiʻi"}
    assert "County and state figures are shown side by side." in result["text"]


def test_the_assistant_keeps_the_county_of_a_follow_up_compared_with_the_state(real_con, tmp_path):
    golden = _golden("follow_up-006")
    path = ROOT / "eval/scripts/follow_up-006.yaml"
    budget = DailyBudget(5.0, tmp_path / "web_spend.json")
    client = ScriptClient(load_script(path), path)
    result = Assistant(budget, client=client).ask(
        real_con, golden["question"], None, earlier=_earlier(golden)
    )
    assert result["mode"] == "assistant" and result["subject"]["geo"] == "county:15009"


@pytest.mark.parametrize("district", [None, ("house", "022")])
def test_a_question_only_about_the_whole_state_answers_for_the_state(small_con, district):
    earlier = (Exchange(KUPUNA, ("house", "022"), ("pop_65_plus",)),)
    for question in ("How many kūpuna live statewide?", "How many kūpuna live across the whole state?"):
        for before in ((), earlier):
            result = basic(small_con, question, district, earlier=before)
            assert places(result) == {"Hawaiʻi"} and result["subject"]["geo"] == "state:15"


@pytest.mark.parametrize("district", [None, ("house", "022")])
def test_kupuna_on_fixed_incomes_statewide_are_not_compared_with_the_state(small_con, district):
    result = basic(small_con, "How many kūpuna live on fixed incomes statewide?", district)
    assert result["lead"][0].startswith("Kūpuna and fixed incomes in Hawaiʻi.\n")
    assert "compared with" not in result["text"] and "margin of error of" not in result["text"]
    assert places(result) == {"Hawaiʻi"} and result["subject"]["geo"] == "state:15"
    assert len(result["facts"]) == len({f["label"] for f in result["facts"]})


def test_a_follow_up_naming_hawaii_as_a_whole_matches_no_measure_of_its_own(real_con):
    from klear.web.answers import indicators

    assert indicators(real_con, "How does that compare with Hawaiʻi as a whole?") == []
