# Tests that not_available and out_of_scope come from the topic catalog in tools, answer checks and basic mode.
import json

import pytest

from klear.agent.answer import build_answer, template_answer
from klear.agent.loop import KlearAgent
from klear.agent.tools import Session
from klear.agent.topics import MEANING, Topic, asked_topic, load_topics, topics_in
from klear.eval.contract import Answer, render
from klear.eval.golden import load_golden
from klear.eval.scorers import _text_problems
from klear.text import misspellings
from klear.web.assistant import basic


@pytest.mark.parametrize(
    "question,topic,refusal",
    [
        ("How many eviction filings were there in House District 5?", "eviction filings", "not_available"),
        ("are people getting evicted in my district", "eviction filings", "not_available"),
        (
            "How many people are enrolled in WIC in House District 26?",
            "WIC enrollment by district",
            "not_available",
        ),
        ("What is the diabetes rate in Senate District 3?", "chronic disease", "not_available"),
        ("How long is the public housing waitlist?", "public housing and voucher waitlists", "not_available"),
        ("How many homeless residents are in House District 27?", "homelessness counts", "out_of_scope"),
        ("What is the crime rate in my district?", "crime", "out_of_scope"),
        ("Can you rank schools by test scores in Senate District 18?", "school test scores", "out_of_scope"),
        ("What salary do teachers earn here?", "salaries", "out_of_scope"),
        ("How many car crashes happen in House District 40?", "traffic and crashes", "out_of_scope"),
        ("How many tourists visit Senate District 6?", "visitors and tourism", "out_of_scope"),
    ],
)
def test_questions_find_their_topic(question, topic, refusal):
    found = asked_topic(question)
    assert found is not None and found.topic.startswith(topic) and found.refusal == refusal


@pytest.mark.parametrize(
    "question",
    [
        "How many kūpuna live in House District 22?",
        "What share of renters pay too much rent?",
        "How many residents receive SNAP?",
        "Is WIC available for families here?",
        "How many owner-occupied homes are there?",
        "Which programs could help people visit a health center?",
    ],
)
def test_questions_on_held_measures_find_no_topic(question):
    assert topics_in(question) == []


def test_topics_needing_different_refusals_give_none():
    assert asked_topic("Compare crime and eviction filings in my district") is None


def test_catalog_loads_and_reads_cleanly():
    topics = load_topics()
    assert {t.refusal for t in topics} == set(MEANING) and len(topics) >= 8
    for t in topics:
        assert t.text().count("Figures on") == 1 and t.where in t.text()
        assert _text_problems(t.text()) == [], t.topic
        assert misspellings(t.text()) == [], t.topic


def test_catalog_rejects_unknown_refusal(tmp_path):
    path = tmp_path / "topics.yaml"
    path.write_text(
        "topics:\n  - {topic: x, refusal: none, match: [x], source: y, where: z}\n", encoding="utf-8"
    )
    with pytest.raises(ValueError, match="refusal must be one of"):
        load_topics.__wrapped__(path)


def test_no_topic_matches_a_stored_indicator(real_con):
    for indicator_id, label in real_con.execute("SELECT indicator_id, label FROM indicator").fetchall():
        assert topics_in(f"{indicator_id.replace('_', ' ')} {label}") == [], indicator_id


def test_golden_declines_agree_with_the_catalog():
    checked = 0
    for g in load_golden():
        if g.refusal in MEANING and (topic := asked_topic(g.question)) is not None:
            assert g.refusal == topic.refusal, g.id
            checked += 1
    assert checked >= 8


def test_search_indicators_reports_the_topic(small):
    got = json.loads(small.run("search_indicators", {"words": ["eviction", "filings"]}))
    (card,) = got["topics"]
    assert card["refusal"] == "not_available" and card["source"] == "Hawaiʻi State Judiciary"
    assert "topics" not in json.loads(small.run("search_indicators", {"words": ["kupuna"]}))


def _submit(refusal, text):
    return {
        "resolved": "house:022",
        "refusal": refusal,
        "blocks": [text],
        "facts": [],
        "programs": [],
        "caveats": [],
    }


def test_answer_check_follows_the_catalog_refusal(small):
    small.question = "How many evictions were filed in House District 22?"
    _, problems = build_answer(small, _submit("out_of_scope", "Eviction filings are not in KLEAR."))
    assert problems and problems[0].startswith(
        "KLEAR's topic list puts eviction filings inside what KLEAR covers"
    )
    _, problems = build_answer(small, _submit("not_available", "Eviction filings are not in KLEAR."))
    assert problems == [
        (
            "name Hawaiʻi State Judiciary so the reader knows where to find eviction filings: the Hawaiʻi State "
            "Judiciary, whose district courts hear landlord-tenant cases"
        )
    ]
    text = (
        "Eviction filings for {geo:house:022} are not in KLEAR yet. The Hawaiʻi State Judiciary keeps them."
    )
    answer, problems = build_answer(small, _submit("not_available", text))
    assert problems == [] and answer.refusal == "not_available"


def test_answer_check_leaves_answers_from_held_measures_alone(small):
    small.question = "Is housing insecurity a fair stand-in for homelessness in House District 22?"
    small.run("get_facts", {"indicator_ids": ["pop_65_plus"], "geos": ["house:022"]})
    text = "{label:f1} in {geo:house:022}: {fact:f1} ({vintage:f1}). It is not a count of homelessness."
    _, problems = build_answer(small, {**_submit("none", text), "facts": ["f1"]})
    assert problems == []


def test_templates_and_fallback_use_the_topic(small):
    small.question = "What is the crime rate in House District 22?"
    answer = template_answer(small, ("house", "022"), "out_of_scope")
    assert answer.blocks == [next(t for t in load_topics() if t.topic == "crime").text()]
    fallback = KlearAgent._fallback(small, ("house", "022"), {"refusal": "not_available"})
    assert fallback.refusal == "out_of_scope" and "Attorney General" in render(fallback, small.con)


def test_basic_mode_says_which_refusal_applies(real_con):
    evict = basic(real_con, "How many evictions were filed in my district?", ("house", "005"))
    crime = basic(real_con, "What is the crime rate in my district?", ("house", "005"))
    assert evict["refusal"] == "not_available" and "not in KLEAR's district data yet" in evict["text"]
    assert "Hawaiʻi State Judiciary" in evict["text"]
    assert crime["refusal"] == "out_of_scope" and "outside what KLEAR covers" in crime["text"]
    assert "Attorney General" in crime["text"]


def test_topic_text_says_which_refusal_applies():
    t = Topic("eviction filings", "not_available", ("evict",), "Judiciary", "the Judiciary")
    assert t.text() == (
        "Figures on eviction filings are not in KLEAR's district data yet, although KLEAR covers the subject. "
        "For these figures, see the Judiciary."
    )
    assert Answer("1", None, "out_of_scope", [], [], [], []).refusal in MEANING
    assert isinstance(Session, type)
