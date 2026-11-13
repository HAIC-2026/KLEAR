# Tests that the system prompt teaches every required rule and its examples follow them.
import difflib
import json
import re

import pytest

from klear.agent.answer import build_answer
from klear.agent.prompt import SYSTEM
from klear.agent.tools import TOOL_NAMES, Session
from klear.eval.contract import MARKER, REFUSALS
from klear.eval.golden import load_golden
from klear.eval.scorers import _names_program, _plain, _text_problems, program_names

# One or more phrases per bullet of eval/README.md "What the system prompt must teach"; the last bullet (max_call_cost)
# is a code rule, not a prompt rule.
README_BULLETS = {
    "no digits or number words": ("digit", "number words", "unit words"),
    "share not percent": ("share, not percent",),
    "bullets": ("* ", "- "),
    "vintage with its fact": ("same block or caveat", "every source", "each distinct source_id"),
    "allowed geographies": ("{geo:TYPE:ID}", "its counties", "the state"),
    "resolve when declining": ("even when you decline",),
    "Hawaiian spelling": ("ʻokina", "kahakō"),
    "labels": ("{label:fN}", "age threshold"),
    "unreliable, programs, direction": (
        "unreliable",
        "name field",
        "higher",
        "lower",
        "within the margin of error",
    ),
    "partisan wording": ("party names",),
}
WRITING = (
    "no HTML tags or entities",
    "inside a link, image or code span",
    "directly asks for a quantity KLEAR does not hold",
    "answer it with refusal none and say plainly what the measure is not",
    "no years or numbers",
    "Read each fact's method",
    "modeled estimate",
    "apportioned to the district from ZIP code or tract data",
    "place_caveats",
    "never restate them",
    "measure of the same program at that place",
    "names a group with a Hawaiian word (kūpuna, keiki, ʻohana)",
    "In an answer with facts, put it alongside the {label:fN} marker",
    "in a decline, which has no label markers, write it in plain text",
)
WORKFLOW = (
    "kūpuna on fixed incomes",
    "SSI, Kūpuna Care, SNAP and Med-QUEST",
    "resolve_place",
    "search_indicators",
    "get_facts",
    "compare",
    "rank_districts",
    "ranges overlap",
    "alone in its tier",
    "When a fact has a note",
    "list_programs",
    "find_sites",
    "submit_answer exactly once",
    "exactly as resolve_place returned",
    "reliable false",
    "{vintage:fN}",
)
STATUS_PHRASES = (
    "or more",
    "or less",
    "fixed to official population totals",
    "treat as uncertain",
    "too few cases for the source to publish",
    "not applicable here",
)
TRIGGERS = (
    "address",
    "please give a place, ZIP code or district",
    "candidates",
    "how to vote",
    "votes or record",
    "WIC enrollment",
    "agency",
    "homelessness",
    "crime",
    "school",
    "salaries",
)
EXAMPLE = re.compile(r"<example>(.*?)</example>", re.DOTALL)
# Ids that may hold digits: geographies, fact refs and snake_case indicator ids.
IDS = re.compile(r"\b(?:house|senate|county|zcta|state):\d+\b|\bf\d+\b|\b[a-z]+(?:_[a-z0-9]+)+\b")


def _examples() -> list[str]:
    return EXAMPLE.findall(SYSTEM)


def _submits(example: str) -> list[dict]:
    return [
        json.loads(line.split(" ", 1)[1])
        for line in example.splitlines()
        if line.startswith("submit_answer ")
    ]


@pytest.mark.parametrize("bullet", sorted(README_BULLETS))
def test_prompt_covers_readme_bullet(bullet):
    missing = [p for p in README_BULLETS[bullet] if p not in SYSTEM]
    assert not missing, f"{bullet}: {missing}"


@pytest.mark.parametrize("phrase", [*WRITING, *WORKFLOW, *STATUS_PHRASES, *TRIGGERS, *REFUSALS[1:]])
def test_prompt_covers_workflow_statuses_and_refusals(phrase):
    assert phrase in SYSTEM


def test_prompt_has_two_examples_with_one_submit_each():
    examples = _examples()
    assert len(examples) == 2
    assert all(len(_submits(e)) == 1 for e in examples)


def test_examples_have_no_digit_outside_markers_and_ids():
    for example in _examples():
        rest = IDS.sub(" ", MARKER.sub(" ", example))
        assert not re.search(r"\d", rest), rest


def test_example_answers_pass_text_checks():
    for example in _examples():
        args = _submits(example)[0]
        for text in [*args["blocks"], *args["caveats"]]:
            assert _text_problems(text) == [], text
        shown = {r for t in [*args["blocks"], *args["caveats"]] for k, r in MARKER.findall(t) if k == "fact"}
        assert set(args["facts"]) == shown


def test_prompt_is_short_and_clean():
    assert len(SYSTEM) / 4 < 2600
    assert chr(0x2014) not in SYSTEM and chr(0x60) not in SYSTEM


STATED = re.compile(r"\b(f\d+)\b[^()]*\(([^)]*)\)")
SELECTED = re.compile(r"selected district as (house|senate):(\d+)")
DECODE = json.JSONDecoder().raw_decode


def _calls(example: str) -> list[tuple[str, dict, str]]:
    """Tool calls of an example as name, arguments and the stated result text."""
    out = []
    for line in example.splitlines():
        name, _, rest = line.partition(" ")
        if name in TOOL_NAMES:
            args, end = DECODE(rest)
            out.append((name, args, rest[end:]))
    return out


def _context(example: str) -> tuple[str, str] | None:
    m = SELECTED.search(example)
    return m.groups() if m else None


def _pairs(example: str) -> set[tuple[str, str, str]]:
    """Indicator and geography pairs an example fetches."""
    pairs = set()
    for name, args, said in _calls(example):
        if name == "get_facts":
            pairs |= {(i, *g.split(":")) for i in args["indicator_ids"] for g in args["geos"]}
        elif name == "compare":
            geos = {args["geo"], *re.findall(r"\b(?:county|state|house|senate):\d+", said)}
            pairs |= {(args["indicator_id"], *g.split(":")) for g in geos}
    return pairs


def test_examples_are_not_golden_questions():
    goldens = load_golden()
    used = {tuple(f) for g in goldens for f in g.facts}
    for example in _examples():
        question = re.search(r"Question: (.*)", example).group(1)
        for g in goldens:
            ratio = difflib.SequenceMatcher(None, question.casefold(), g.question.casefold()).ratio()
            assert ratio < 0.6, (question, g.id)
        assert not _pairs(example) & used
        assert {i for i, *_ in _pairs(example)}.isdisjoint({f[0] for f in used})


def test_example_outcomes_match_the_built_store(real_con):
    for example in _examples():
        context = _context(example)
        session = Session(real_con)
        session.context = context
        for name, args, said in _calls(example):
            got = json.loads(session.run(name, args))
            assert "error" not in got
            if name == "resolve_place":
                (match,) = got["matches"]
                geos = {o["geo"] for o in match.get("overlaps", [])} or {match["geo"]}
                assert set(re.findall(r"\b(?:county|state|house|senate):\d+", said)) == geos
                continue
            if name == "search_indicators":
                ids = [i["indicator_id"] for i in got["indicators"]]
                assert all(i in ids for i in re.findall(r"\b[a-z]+(?:_[a-z0-9]+)+\b", said))
                continue
            actual = {f["ref"]: {"reliable": str(f["reliable"]).lower()} for f in got["facts"]}
            for c in got.get("comparisons", []):
                for f in c["facts"]:
                    actual[f["ref"]] = {"reliable": str(f["reliable"]).lower(), "direction": c["direction"]}
            for ref, stated in STATED.findall(said):
                claims = dict(part.strip().split(" ") for part in stated.split(","))
                assert claims == actual[ref], (ref, claims, actual[ref])
        answer, problems = build_answer(session, _submits(example)[0], context)
        assert problems == [] and answer is not None


@pytest.mark.parametrize("rule_id", sorted(program_names()))
def test_program_name_field_passes_build_and_scorer(real_con, rule_id):
    session = Session(real_con)
    (rule,) = [
        r
        for p in json.loads(session.run("list_programs", {}))["programs"]
        for r in p["rules"]
        if r["rule_id"] == rule_id
    ]
    text = f"The {rule['name']} program may help."
    args = {
        "resolved": None,
        "refusal": "none",
        "blocks": [text],
        "facts": [],
        "programs": [rule_id],
        "caveats": [],
    }
    answer, problems = build_answer(session, args, None)
    assert problems == [] and answer is not None
    assert _names_program(_plain(text), program_names()[rule_id])


def test_prompt_states_the_call_budget_and_parallel_calls():
    for phrase in ("at most six replies", "counting the final", "several tool calls in one reply"):
        assert phrase in SYSTEM
    assert "Never call a tool again for something a tool already returned" in SYSTEM


def test_example_caveats_use_only_geographies_a_tool_returned():
    for example in _examples():
        returned = set()
        for name, args, said in _calls(example):
            if name == "resolve_place":
                returned |= set(re.findall(r"\b(?:county|state|house|senate):\d+", said))
            elif name == "compare":
                returned |= {args["geo"], *re.findall(r"\b(?:county|state|house|senate):\d+", said)}
        args = _submits(example)[0]
        cited = {f"{t}:{i}" for text in args["caveats"] for t, i in re.findall(r"\{geo:(\w+):(\w+)\}", text)}
        assert cited <= returned
        if args["resolved"] and args["resolved"].startswith("house"):
            assert not any("{geo:senate" in text for text in [*args["blocks"], *args["caveats"]])
