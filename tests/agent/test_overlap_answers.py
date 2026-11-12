# Tests that the assistant and the basic lookup name every district of the other chamber with its share.
import json

import pytest

from klear.agent.answer import asked_chamber, build_answer, template_answer
from klear.agent.tools import Session
from klear.eval.contract import Answer, district_overlap, render
from klear.web.assistant import basic
from klear.web.display import build

H22, S8 = ("house", "022"), ("senate", "008")


@pytest.mark.parametrize(
    "question,geo,chamber",
    [
        ("Which Senate district contains House District 22?", H22, "senate"),
        ("What Senate district covers my district?", H22, "senate"),
        ("senate dist for hse 26??", H22, "senate"),
        ("Is my district in more than one senate district?", H22, "senate"),
        ("Who is my state senator?", H22, "senate"),
        ("Which House districts make up Senate District 8?", S8, "house"),
        (
            "I am looking for Hawaiʻi Senate District 8, not the House district. Can you bring up its profile?",
            S8,
            None,
        ),
        ("Please switch from the selected House district to Hawaiʻi Senate District 15.", S8, None),
        ("Which Senate district has the most kūpuna?", H22, None),
        ("Compare House District 22 with Senate District 11", H22, None),
        ("How many people own a house in Senate District 8?", S8, None),
        ("How does poverty in House District 22 compare with the Senate district average?", H22, None),
        ("Which Senate district contains this place?", ("county", "15003"), None),
    ],
)
def test_asked_chamber(question, geo, chamber):
    assert asked_chamber(question, geo) == chamber


def _session(con, question):
    s = Session(con)
    s.question = question
    return s


def _submit(blocks, resolved="house:022"):
    return {
        "resolved": resolved,
        "refusal": "none",
        "blocks": blocks,
        "facts": [],
        "programs": [],
        "caveats": [],
    }


def test_resolve_place_lists_other_chamber_districts_with_share_markers(session):
    (match,) = json.loads(session.run("resolve_place", {"text": "House District 22"}))["matches"]
    senate = [o for o in match["overlaps"] if o["geo"].startswith("senate:")]
    assert [o["geo"] for o in senate] == ["senate:011", "senate:010"]
    assert senate[0]["marker"] == "{share:house:022:senate:011}" and senate[0]["name"] == "Senate District 11"
    assert match["overlaps"][-1]["geo"] == "county:15003"
    assert session.resolved == {H22}


def test_every_house_district_shares_add_up_to_all_its_people(real_con):
    houses = [
        r[0] for r in real_con.execute("SELECT geo_id FROM geography WHERE geo_type = 'house'").fetchall()
    ]
    assert len(houses) == 51
    for h in houses:
        rows = district_overlap(real_con, ("house", h))
        assert sum(s for t, _, _, s in rows if t == "senate") == pytest.approx(1)
        assert sum(s for t, _, _, s in rows if t == "county") == pytest.approx(1)


def test_answer_must_name_every_senate_district_with_its_share(real_con):
    s = _session(real_con, "Which Senate district is House District 22 in?")
    s.run("resolve_place", {"text": "House District 22"})
    _, problems = build_answer(s, _submit(["{geo:house:022} lies mostly in {geo:senate:011}."]))
    assert problems == [
        (
            "this question asks which Senate districts hold house:022; name every one with its share of the district, "
            "using these markers: {geo:senate:011} with {share:house:022:senate:011}, {geo:senate:010} with "
            "{share:house:022:senate:010}"
        )
    ]
    good = (
        "* {geo:senate:011}: {share:house:022:senate:011}\n* {geo:senate:010}: {share:house:022:senate:010}"
    )
    answer, problems = build_answer(s, _submit([good]))
    assert problems == []
    assert render(answer, real_con) == (
        "* Senate District 11: 89.2% of the 2020 Census population of House District 22\n"
        "* Senate District 10: 10.8% of the 2020 Census population of House District 22"
    )


def test_other_chamber_districts_are_allowed_without_a_tool_call(real_con):
    s = _session(real_con, "How many kūpuna live in House District 22?")
    s.run("get_facts", {"indicator_ids": ["pop_65_plus"], "geos": ["house:022"]})
    text = (
        "{geo:house:022} has {fact:f1} kūpuna, {label:f1} ({vintage:f1}). It lies mostly in {geo:senate:011}."
    )
    _, problems = build_answer(s, {**_submit([text]), "facts": ["f1"]})
    assert problems == []
    _, problems = build_answer(s, {**_submit([text.replace("senate:011", "senate:020")]), "facts": ["f1"]})
    assert problems == ["geography senate 020 is not allowed here"]


def test_a_share_of_districts_that_do_not_overlap_is_rejected(real_con):
    s = _session(real_con, "Which Senate districts hold House District 22?")
    _, problems = build_answer(
        s, _submit(["{geo:house:022}: {share:house:022:senate:011}, {share:house:022:senate:012}"])
    )
    assert any("render failed" in p or "not allowed" in p for p in problems)


def test_fallback_template_lists_the_overlaps(real_con):
    s = _session(real_con, "what senate dist is house 22 in")
    text = render(template_answer(s, H22, "none"), real_con)
    assert text.startswith(
        "Senate districts holding residents of House District 22, with each one's share:\n"
    )
    assert "* Senate District 10: 10.8% of the 2020 Census population of House District 22" in text


def test_site_text_renders_share_markers(real_con):
    answer = Answer("1", H22, "none", ["{geo:senate:010} holds {share:house:022:senate:010}."], [], [], [])
    assert build(real_con, answer)["lead"] == [
        "Senate District 10 holds 10.8% of the 2020 Census population of House District 22."
    ]


@pytest.mark.parametrize(
    "question,selected,text",
    [
        (
            "Which Senate district contains House District 22?",
            ("senate", "005"),
            (
                "House District 22 spans 2 Senate districts. By share of its 2020 Census population: "
                "Senate District 11 (89.2%) and Senate District 10 (10.8%)."
            ),
        ),
        (
            "what senate district is this in",
            ("house", "002"),
            "House District 2 lies entirely in Senate District 1, which holds all of its 2020 Census population.",
        ),
        (
            "Which House districts make up my district?",
            ("senate", "007"),
            (
                "Senate District 7 spans 2 House districts. By share of its 2020 Census population: "
                "House District 13 (50.3%) and House District 12 (49.7%)."
            ),
        ),
    ],
)
def test_basic_mode_names_overlapping_districts(real_con, question, selected, text):
    got = basic(real_con, question, selected)
    assert got["text"] == text and got["refusal"] == "none" and got["mode"] == "basic"
