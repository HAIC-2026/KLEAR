# Tests for the answer builder, its self-check and the template answers.
import json

import pytest

from klear.agent import config
from klear.agent.answer import NUMBER_HINT, SUBMIT_SPEC, allowed_geos, build_answer, template_answer
from klear.eval.contract import REFUSALS
from klear.eval.scorers import answer_problems

GOOD = "{geo:house:022} has {fact:f1} {label:f1} ({vintage:f1})."


@pytest.fixture
def s1(small):
    small.run("get_facts", {"indicator_ids": ["pop_65_plus"], "geos": ["house:022"]})
    assert "f1" in small.facts
    return small


def args(**kw):
    base = {
        "resolved": "house:022",
        "refusal": "none",
        "blocks": [GOOD],
        "facts": ["f1"],
        "programs": [],
        "caveats": [],
    }
    return base | kw


def test_values_come_from_session_not_model(s1):
    a, problems = build_answer(s1, args())
    assert problems == [] and a.facts[0].value == s1.facts["f1"].value == 1000.0
    assert a.resolved == ("house", "022") and a.contract == "1"


def test_model_cannot_supply_values(s1):
    a, problems = build_answer(s1, args(facts=["f1"], blocks=[GOOD]) | {"values": {"f1": 5}})
    assert problems == [] and a.facts[0].value == 1000.0


def test_unknown_ref_is_a_problem(s1):
    a, problems = build_answer(s1, args(blocks=["{fact:f9}"], facts=["f9"]))
    assert a is None and any("f9" in p for p in problems)


def test_typed_digit_is_a_problem(s1):
    _, problems = build_answer(s1, args(blocks=["About 12 people ({fact:f1}, {vintage:f1})."]))
    assert any("unsourced number" in p for p in problems)


def test_geo_outside_allowed_is_a_problem(s1):
    _, problems = build_answer(s1, args(blocks=["{geo:house:023} {fact:f1} {vintage:f1}"]))
    assert any("not allowed" in p for p in problems)


def test_county_of_district_is_allowed(s1):
    _, problems = build_answer(s1, args(blocks=[GOOD + " {geo:county:15003} {geo:state:15}"]))
    assert problems == []


def test_resolved_places_and_context_are_allowed(s1):
    s1.run("resolve_place", {"text": "Kaneohe"})
    block = "{geo:house:023} {fact:f1} {vintage:f1}"
    assert build_answer(s1, args(blocks=[block]))[1] == []
    s2 = type(s1)(s1.con)
    s2.run("get_facts", {"indicator_ids": ["pop_65_plus"], "geos": ["house:022"]})
    assert any("not allowed" in p for p in build_answer(s2, args(blocks=[block]))[1])
    assert build_answer(s2, args(blocks=[block]), context=("house", "023"))[1] == []


def test_geos_alone_do_not_allow(s1):
    s1.run("get_facts", {"indicator_ids": ["pop_65_plus"], "geos": ["house:023"]})
    assert ("house", "023") in s1.geos
    _, problems = build_answer(s1, args(blocks=["{geo:house:023} {fact:f1} {vintage:f1}"]))
    assert any("not allowed" in p for p in problems)


@pytest.mark.parametrize(
    "resolved",
    [
        "house",
        "house:",
        "nowhere:1",
        5,
        "a:b:c",
        "house:022 ",
        "house:22",
        "house:999",
        "house:a:b",
        "state:15",
    ],
)
def test_malformed_resolved(s1, resolved):
    a, problems = build_answer(s1, args(resolved=resolved))
    assert a is None and any("resolved" in p for p in problems)


def test_null_resolved_ok(s1):
    s1.run("get_facts", {"indicator_ids": ["pop_65_plus"], "geos": ["state:15"]})
    blocks = ["{fact:f2} {vintage:f2}"]
    a, problems = build_answer(s1, args(resolved=None, blocks=blocks, facts=["f2"]))
    assert a.resolved is None and problems == []


def test_unknown_program(s1):
    _, problems = build_answer(s1, args(programs=["nope"]))
    assert any("nope" in p for p in problems)
    a, problems = build_answer(s1, args(programs=["eitc_federal_ty2025"], blocks=[GOOD, "See Federal EITC."]))
    assert a.programs == ["eitc_federal_ty2025"] and problems == []


@pytest.mark.parametrize("text", ["See the EITC.", "Nothing named."])
def test_listed_program_must_be_named(s1, text):
    a, problems = build_answer(s1, args(programs=["eitc_federal_ty2025"], blocks=[GOOD, text]))
    assert a is None and problems == [
        "program eitc_federal_ty2025 is listed but not named in the text; write it as 'Federal EITC'"
    ]


@pytest.mark.parametrize(
    ("question", "required", "bad"),
    [
        ("Bring up the profile for House 22.", "pop_under_18", False),
        ("Bring up the profile for House 22.", "pop_65_plus", False),
        ("Show me the profile of House 22.", "sites_lihtc", True),
        ("How many sites are in House 22?", "sites_lihtc", False),
    ],
)
def test_question_asking_for_a_fact_requires_it(s1, monkeypatch, question, required, bad):
    monkeypatch.setattr(config, "REQUIRED_FACTS", ((("profile",), (required,)),))
    s1.question = question
    a, problems = build_answer(s1, args())
    expected = [f"this question asks for {required} at house:022; call get_facts for it and cite it"]
    assert (a is None, problems) == ((True, expected) if bad else (False, []))


def test_question_asking_to_compare_requires_main_county_and_state(s1, monkeypatch):
    monkeypatch.setattr(config, "REQUIRED_COMPARISONS", ((("fixed",), ("pop_65_plus",)),))
    monkeypatch.setattr(config, "REQUIRED_PROGRAMS", ())
    s1.question = "Who lives on a fixed income in House 22?"
    a, problems = build_answer(s1, args())
    assert a is None and problems == [
        f"this question compares pop_65_plus at house:022 with {g}; call get_facts for it there and cite it"
        for g in ("county:15003", "state:15")
    ]
    s1.run("get_facts", {"indicator_ids": ["pop_65_plus"], "geos": ["county:15003", "state:15"]})
    text = GOOD + " Compare {geo:county:15003} at {fact:f2} and {geo:state:15} at {fact:f3}."
    a, problems = build_answer(s1, args(blocks=[text], facts=["f1", "f2", "f3"]))
    assert problems == [] and a is not None


def test_question_asking_about_programs_requires_them(s1, monkeypatch):
    monkeypatch.setattr(config, "REQUIRED_COMPARISONS", ())
    monkeypatch.setattr(config, "REQUIRED_PROGRAMS", ((("fixed",), ("snap_fy2027",)),))
    s1.question = "Who lives on a fixed income in House 22?"
    a, problems = build_answer(s1, args())
    assert a is None and problems == [
        "this question asks about snap_fy2027; list it in programs and write it as 'SNAP'"
    ]
    a, problems = build_answer(s1, args(blocks=[GOOD + " SNAP can help."], programs=["snap_fy2027"]))
    assert problems == [] and a.programs == ["snap_fy2027"]
    _, problems = build_answer(s1, args(refusal="not_available"))
    assert not any("asks about" in p for p in problems)


def test_required_facts_skip_refusals(s1, monkeypatch):
    monkeypatch.setattr(config, "REQUIRED_FACTS", ((("profile",), ("sites_lihtc",)),))
    s1.question = "Profile of House 22"
    _, problems = build_answer(s1, args(refusal="not_available"))
    assert not any("asks for" in p for p in problems)


@pytest.mark.parametrize(("listed", "bad"), [(["eitc_federal_ty2025"], True), (["wic_2026_27"], False)])
def test_not_available_with_program_measure_is_a_problem(s1, monkeypatch, listed, bad):
    monkeypatch.setattr(config, "MEASURES", {"eitc_federal_ty2025": ("pop_65_plus",)})
    names = {"eitc_federal_ty2025": "Federal EITC", "wic_2026_27": "WIC"}
    text = GOOD + f" See {names[listed[0]]}."
    a, problems = build_answer(s1, args(refusal="not_available", programs=listed, blocks=[text]))
    expected = [
        "the answer cites pop_65_plus, a measure of a listed program, so set refusal none and say plainly "
        + "what those figures do not show"
    ]
    assert problems == (expected if bad else []) and (a is None) == bad


def test_typed_number_gets_marker_hint(s1):
    _, problems = build_answer(s1, args(blocks=[GOOD + " District 9 is nearby."]))
    assert problems[-1] == NUMBER_HINT and any("unsourced number" in p for p in problems)


def test_bad_shapes_are_problems(s1):
    for bad in ({"blocks": "x"}, {"facts": [1]}, {"refusal": "maybe"}, {"caveats": None}):
        a, problems = build_answer(s1, args(**bad))
        assert a is None and problems


def test_unused_fact_is_a_problem(s1):
    _, problems = build_answer(s1, args(blocks=["Nothing here."]))
    assert any("never used" in p for p in problems)


def test_template_answer_passes_answer_problems(s1):
    a = template_answer(s1, ("house", "022"), "none")
    assert answer_problems(a, s1.con, allowed_geos(s1, ("house", "022"))) == []
    assert a.refusal == "none" and [f.ref for f in a.facts] == ["f1"]
    assert a.blocks[0].startswith("* {label:f1} in {geo:house:022}: {fact:f1} ({vintage:f1})")


@pytest.mark.parametrize("refusal", [r for r in REFUSALS if r != "none"])
def test_refusal_templates_pass(s1, refusal):
    a = template_answer(s1, ("house", "022"), refusal)
    assert a.refusal == refusal and a.facts == []
    assert answer_problems(a, s1.con, allowed_geos(s1, ("house", "022"))) == []
    text = " ".join(a.blocks).casefold()
    assert not any(
        w in text
        for w in (
            "republican",
            "democrat",
            "party",
            "parties",
            "candidate",
            "ballot",
            "vote",
            "win",
            "elect",
            "should",
        )
    )


def test_template_without_facts_or_district(small):
    a = template_answer(small, None, "none")
    assert a.facts == [] and answer_problems(a, small.con, allowed_geos(small, None)) == []


def test_submit_spec():
    props = SUBMIT_SPEC["input_schema"]["properties"]
    assert SUBMIT_SPEC["name"] == "submit_answer"
    assert props["refusal"]["enum"] == list(REFUSALS)
    assert set(props) == {"resolved", "refusal", "blocks", "facts", "programs", "caveats"}
    json.dumps(SUBMIT_SPEC)


def test_template_drops_facts_outside_allowed(s1):
    s1.run("get_facts", {"indicator_ids": ["pop_65_plus"], "geos": ["house:023"]})
    s2 = type(s1)(s1.con)
    s2.run("get_facts", {"indicator_ids": ["pop_65_plus"], "geos": ["house:022", "house:023"]})
    a = template_answer(s2, ("house", "022"), "none")
    assert {f.geo_id for f in a.facts} == {"022"}
    assert answer_problems(a, s2.con, allowed_geos(s2, ("house", "022"))) == []
    s3 = type(s1)(s1.con)
    s3.run("get_facts", {"indicator_ids": ["pop_65_plus"], "geos": ["house:023"]})
    a = template_answer(s3, ("house", "022"), "none")
    assert a.facts == [] and answer_problems(a, s3.con, allowed_geos(s3, ("house", "022"))) == []


def test_decline_answers_cite_no_facts(s1):
    for refusal in ("decline_individual", "decline_partisan"):
        _, problems = build_answer(s1, args(refusal=refusal))
        assert any("no facts" in p for p in problems)
        a, problems = build_answer(
            s1, args(refusal=refusal, blocks=["Neutral data only, see {geo:house:022}."], facts=[])
        )
        assert problems == [] and a.refusal == refusal
    _, problems = build_answer(
        s1, args(refusal="not_available", blocks=["Not published; nearby {fact:f1} {vintage:f1}."])
    )
    assert problems == []


def test_typed_place_names(s1):
    ok = "{fact:f1} {vintage:f1}"
    _, problems = build_answer(s1, args(blocks=["Maui has " + ok]))
    assert any("names place maui that was not resolved" in p for p in problems)
    s2 = type(s1)(s1.con)
    s2.run("get_facts", {"indicator_ids": ["pop_65_plus"], "geos": ["house:022"]})
    s2.resolved = set()
    _, problems = build_answer(s2, args(blocks=["Hawaiʻi has " + ok, "Kāneʻohe has " + ok]))
    assert any("Kāneʻohe" in p for p in problems) and not any("hawaii" in p for p in problems)
    s2.run("resolve_place", {"text": "kaneohe"})
    _, problems = build_answer(s2, args(blocks=["Kāneʻohe has " + ok]))
    assert problems == []
    _, problems = build_answer(s2, args(blocks=["The city has " + ok]))
    assert problems == []


@pytest.mark.parametrize(
    "typed",
    [
        "Ma**ui**",
        "Mau\u200bi",
        "Mau\u200di",
        "Ka\u200bneohe",
        "Mau\u0301i",
        "Kaneohe",
        "Kāneʻohe",
        "**Maui**",
        "Ma_u_i",
    ],
)
def test_typed_place_bypasses_are_rejected(s1, typed):
    _, problems = build_answer(s1, args(blocks=[f"{typed} has {{fact:f1}} {{vintage:f1}}"]))
    assert any("not resolved" in p for p in problems)


def test_lowercase_common_words_do_not_trigger(session):
    session.run("get_facts", {"indicator_ids": ["pop_65_plus"], "geos": ["house:022"]})
    ok = " {fact:f1} {vintage:f1}"
    assert build_answer(session, args(blocks=["The volcano is active." + ok]))[1] == []
    _, problems = build_answer(session, args(blocks=["Volcano has" + ok]))
    assert any("Volcano" in p for p in problems)


def test_hawaiian_group_word_with_label_and_in_a_decline(s1):
    a, problems = build_answer(
        s1, args(blocks=["Kūpuna in {geo:house:022} ({label:f1}): {fact:f1} ({vintage:f1})."])
    )
    assert problems == [] and a is not None
    decline = args(
        refusal="decline_partisan",
        facts=[],
        blocks=["KLEAR shares neutral public data about kūpuna in {geo:house:022}, not voting advice."],
    )
    a, problems = build_answer(s1, decline)
    assert problems == [] and a.refusal == "decline_partisan"


def test_counties_of_other_resolved_districts_are_not_allowed(small):
    small.run("resolve_place", {"text": "House 22"})
    small.run("get_facts", {"indicator_ids": ["pop_65_plus"], "geos": ["house:023"]})
    block = "{geo:house:023} {fact:f1} {vintage:f1} {geo:house:022}"
    assert build_answer(small, args(resolved="house:023", blocks=[block]))[1] == []
    _, problems = build_answer(small, args(resolved="house:023", blocks=[block + " {geo:county:15001}"]))
    assert any("not allowed" in p for p in problems)


@pytest.mark.parametrize(
    "text",
    [
        "I cannot say which party should win {geo:house:022}.",
        "No advice on Democrats or Republicans here.",
        "I will not tell anyone who to VOTE  FOR.",
        "Nothing on the **Green** Party.",
    ],
)
def test_partisan_decline_cannot_repeat_advocacy_words(s1, text):
    base = args(refusal="decline_partisan", facts=[])
    assert (
        build_answer(s1, base | {"blocks": ["KLEAR shares neutral public data on {geo:house:022}."]})[1] == []
    )
    _, problems = build_answer(s1, base | {"blocks": [text]})
    assert any("must not say" in p for p in problems)


def test_template_states_directions(small):
    small.run("compare", {"indicator_id": "pop_65_plus", "geo": "house:022"})
    a = template_answer(small, ("house", "022"), "none")
    text = "\n".join(a.blocks)
    assert "{geo:house:022} is lower than {geo:county:15003}" in text
    assert "{geo:house:022} is within the margin of error of {geo:county:15001}" in text
    assert answer_problems(a, small.con, allowed_geos(small, ("house", "022"))) == []


def test_template_flags_unreliable_and_method(small):
    small.run("get_facts", {"indicator_ids": ["pop_65_plus"], "geos": ["house:023", "house:022"]})
    small.tiers["f2"] = "M"
    a = template_answer(small, ("house", "023"), "none", None)
    lines = a.blocks[0].splitlines()
    assert lines[0].endswith(", an unreliable estimate") and "modeled" not in lines[0]
    small.resolved.add(("house", "022"))
    a = template_answer(small, ("house", "023"), "none", None)
    assert a.blocks[0].splitlines()[1].endswith("{vintage:f2}), a modeled estimate")


def test_template_names_known_programs_without_digits(s1):
    a = template_answer(
        s1, ("house", "022"), "none", None, ["eitc_federal_ty2025", "nope", "eitc_federal_ty2025"]
    )
    assert a.programs == ["eitc_federal_ty2025"] and a.blocks[-1] == "Related programs: Federal EITC."
    assert answer_problems(a, s1.con, allowed_geos(s1, ("house", "022"))) == []


def test_direction_against_the_facts_is_checked(small):
    small.run("compare", {"indicator_id": "pop_65_plus", "geo": "house:022"})
    county = next(r for r, f in small.facts.items() if f.geo_type == "county" and f.geo_id == "15003")
    cites = ["f1", county]
    good = f"{{geo:house:022}} has {{fact:f1}} ({{vintage:f1}}), lower than {{geo:county:15003}} at {{fact:{county}}}."
    a, problems = build_answer(small, args(blocks=[good], facts=cites))
    assert problems == [] and a is not None
    bad = good.replace("lower than", "higher than")
    _, problems = build_answer(small, args(blocks=[bad], facts=cites))
    assert problems == [
        "the text says {geo:house:022} is higher than {geo:county:15003}, but the facts show it is lower; "
        + "use the direction compare returns"
    ]


@pytest.mark.parametrize(("question", "text", "bad"), [
    ("How many kupuna live in House 22?", GOOD, True),
    ("How many kupuna live in House 22?", GOOD + " These are kūpuna.", False),
    ("How many seniors live in House 22?", GOOD, False),
])  # fmt: skip
def test_hawaiian_group_word_from_question_is_required(s1, question, text, bad):
    s1.question = question
    _, problems = build_answer(s1, args(blocks=[text]))
    assert problems == (
        ["the question says kūpuna; write kūpuna in the answer, spelled with ʻokina and kahakō"]
        if bad
        else []
    )


def test_template_writes_the_group_word(s1):
    s1.question = "keiki and kupuna in House 22"
    a = template_answer(s1, ("house", "022"), "none")
    assert a.blocks[0] == "Figures on kūpuna and keiki:"
    assert answer_problems(a, s1.con, allowed_geos(s1, ("house", "022"))) == []
