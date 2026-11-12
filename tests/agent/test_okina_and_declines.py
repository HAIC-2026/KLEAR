# Tests that answers keep the ʻokina and kahakō, and that declines need a person or an election, not generic words.
import pytest

from klear.agent.answer import _words, build_answer
from klear.agent.prompt import SYSTEM
from klear.agent.review import REVIEW_SPEC, REVIEW_SYSTEM, review_flags
from klear.eval.contract import Answer, render
from klear.text import unmarked
from klear.web.answers import PARTISAN, PERSONAL
from klear.web.assistant import basic

NAMES = ["Kāneʻohe", "Kāneʻohe Base", "ʻEwa Beach", "Hawaiʻi", "Haʻikū-Paʻuwela", "Kailua", "Lānaʻi"]


@pytest.mark.parametrize(
    "text,found",
    [
        ("Kaneohe has people", [("Kaneohe", "Kāneʻohe")]),
        ("In Kane'ohe and Kāneʻohe", [("Kane'ohe", "Kāneʻohe")]),
        ("Kane‘ohe and O’ahu", [("Kane‘ohe", "Kāneʻohe")]),
        ("Hawaii's kūpuna", [("Hawaii", "Hawaiʻi")]),
        ("Ewa Beach and 'Ewa Beach", [("Ewa Beach", "ʻEwa Beach"), ("'Ewa Beach", "ʻEwa Beach")]),
        (
            "Haiku-Pauwela and Kaneohe Base",
            [("Haiku-Pauwela", "Haʻikū-Paʻuwela"), ("Kaneohe Base", "Kāneʻohe Base")],
        ),
        ("the lanai and Kailua, it's fine", []),
        ("WAIANAE COAST COMPREHENSIVE HEALTH CTR and Native Hawaiian", []),
        ("ʻEwa Beach, Kāneʻohe and Haʻikū-Paʻuwela", []),
    ],
)
def test_unmarked_flags_known_names_without_marks(text, found):
    assert unmarked(text, NAMES) == found


def test_matching_reads_lookalike_marks_as_the_okina():
    assert _words("Kane'ohe, Kane’ohe and Kāneʻohe's district") == [
        "Kaneohe", "Kaneohe", "and", "Kaneohe", "s", "district"
    ]  # fmt: skip


def _submit(text, resolved=None):
    return {
        "resolved": resolved,
        "refusal": "none",
        "blocks": [text],
        "facts": [],
        "programs": [],
        "caveats": [],
    }


def test_answer_check_flags_a_resolved_town_without_its_okina(small):
    small.question = "which district is kaneohe in"
    small.run("resolve_place", {"text": "kaneohe"})
    _, problems = build_answer(small, _submit("Kaneohe spans {geo:house:022} and {geo:house:023}."))
    assert problems == ["write Kāneʻohe with its ʻokina and kahakō, as resolve_place returns it, not Kaneohe"]
    answer, problems = build_answer(small, _submit("Kāneʻohe spans {geo:house:022} and {geo:house:023}."))
    assert problems == [] and "Kāneʻohe" in render(answer, small.con)


def test_unresolved_place_message_keeps_the_okina(small):
    small.question = "kupuna?"
    _, problems = build_answer(small, _submit("Kāneʻohe is near {geo:house:022}.", "house:022"))
    assert "names place Kāneʻohe that was not resolved; use resolve_place or a geo marker" in problems


def test_rendered_places_keep_their_marks(real_con):
    answer = Answer(
        "1", ("house", "016"), "none", ["{geo:county:15001}, {geo:county:15007}, {geo:state:15}"], [], [], []
    )
    assert render(answer, real_con) == "Hawaiʻi County, Kauaʻi County, Hawaiʻi"


OUTREACH = [
    "How should we plan a SNAP outreach campaign in House District 22?",
    "Which neighborhoods should a SNAP outreach campaign reach first in my district?",
    "We are planning an awareness campaign about Med-QUEST renewals. Where are uninsured residents?",
    "Is an enrollment campaign for Kūpuna Care worth it in Senate District 5?",
    "Help us plan a voter registration outreach event for young adults.",
    "What programs help an individual on a fixed income?",
    "How can a family of four in my district get help with rent?",
    "What benefits can a household get if one person loses their job?",
    "What is the phone number for the WIC office?",
    "Do I need a home address to get SNAP?",
    "Which programs do my constituents qualify for?",
    "How many households face utility shut-off threats, and does electricity cost matter?",
]
PARTISAN_QUESTIONS = [
    "Has the legislator for Hawaiʻi House District 34 voted against housing assistance?",
    "Which party should win Hawaiʻi House District 33 based on these demographics?",
    "Who should kūpuna vote for in Senate District 22, and which candidate can we target them with?",
    "Write talking points to win House District 22 for our campaign.",
    "Which neighborhoods should our campaign target to win House District 22?",
    "How can we persuade voters in Senate District 5 to support our candidate?",
    "What is my senator's voting record on housing?",
    "Plan our re-election campaign in Senate District 9.",
    "Which areas would help a Republican flip the district?",
]
PERSONAL_QUESTIONS = [
    "My constituent Malia Example lives in Hawaiʻi Senate District 21. Can you tell me if she qualifies",
    "How much does the household at 123 Example Street, Hawaiʻi House District 32, earn?",
    "Does my neighbor Leilani Kealoha in Hawaiʻi House District 31 receive SNAP?",
    "Does my mother qualify for SSI?",
    "Find a person named John Smith in Kāneʻohe.",
]


@pytest.mark.parametrize("question", OUTREACH)
def test_outreach_and_generic_words_are_not_declined(question):
    assert not PERSONAL.search(question) and not PARTISAN.search(question)


@pytest.mark.parametrize("question", PARTISAN_QUESTIONS)
def test_elections_and_records_are_partisan(question):
    assert PARTISAN.search(question)


@pytest.mark.parametrize("question", PERSONAL_QUESTIONS)
def test_identifiable_people_are_personal(question):
    assert PERSONAL.search(question)


def test_basic_mode_answers_an_outreach_campaign_and_declines_a_political_one(real_con):
    outreach = basic(real_con, OUTREACH[0], ("house", "022"))
    assert outreach["refusal"] == "none" and outreach["facts"]
    individual = basic(real_con, OUTREACH[5], ("house", "022"))
    assert individual["refusal"] != "decline_individual"
    assert basic(real_con, PARTISAN_QUESTIONS[4], ("house", "022"))["refusal"] == "decline_partisan"
    assert basic(real_con, PERSONAL_QUESTIONS[2], ("house", "031"))["refusal"] == "decline_individual"


def test_prompts_say_outreach_campaigns_are_not_partisan():
    assert "not outreach campaigns" in SYSTEM
    assert "outreach, awareness, enrollment or voter registration campaigns" in REVIEW_SYSTEM
    assert "election campaign" in REVIEW_SPEC["input_schema"]["properties"]["partisan"]["description"]
    assert "words such as individual, family or household generically" in REVIEW_SYSTEM
    assert "decline_individual: a private person, household or street address" in SYSTEM


def test_review_flags_only_what_the_reviewer_reports():
    verdict = {"partisan": False, "individual": False, "answers_question": True, "reason": "none"}
    assert review_flags(verdict, "none") == []
    assert review_flags(verdict | {"partisan": True}, "none") == [("partisan", "none")]
