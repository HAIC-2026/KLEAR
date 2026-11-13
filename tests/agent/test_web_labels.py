"""Every indicator has a short label and a plain definition, and the website shows them in place of the official
label, which the eval keeps."""

import re

import pandas as pd
import pytest

from klear.etl.chas import chas_indicator_rows
from klear.etl.cms import cms_indicator_rows
from klear.etl.hud import hud_indicator_rows
from klear.etl.irs import irs_indicator_rows
from klear.etl.places import PLACES_MEASURES
from klear.etl.profile import load_catalog
from klear.etl.sites import site_indicator_rows
from klear.eval.contract import Answer, FactRef, render
from klear.labels import MAX_SHORT, in_sentence, plain_labels
from klear.web import __main__ as web
from klear.web.display import build
from tests.agent.test_web_display import add_rate, rate

# The terms the glossary must explain, by anchor.
GLOSSARY_TERMS = (
    "estimate",
    "margin-of-error",
    "range",
    "modeled-estimate",
    "apportioned-estimate",
    "vintage",
    "acs-5-year",
    "poverty-guideline",
    "area-median-income",
    "cv",
)


def built_indicators() -> set[str]:
    """Every indicator id the build registers: the ACS catalog and each other source's rows."""
    other = pd.concat(
        [
            irs_indicator_rows(),
            cms_indicator_rows(),
            hud_indicator_rows(),
            site_indicator_rows(),
            chas_indicator_rows(),
        ]
    )
    return (
        {i.indicator_id for i in load_catalog()} | set(other["indicator_id"]) | set(PLACES_MEASURES.values())
    )


def test_every_indicator_the_build_registers_has_reader_text_and_nothing_else_does():
    assert set(plain_labels()) == built_indicators()


def test_short_labels_fit_a_tile_and_name_one_measure_each():
    shorts = [p.short for p in plain_labels().values()]
    assert all(len(s) <= MAX_SHORT for s in shorts), [s for s in shorts if len(s) > MAX_SHORT]
    assert len(set(shorts)) == len(shorts), sorted(s for s in shorts if shorts.count(s) > 1)


def test_definitions_are_one_sentence_that_says_more_than_the_label():
    for indicator, p in plain_labels().items():
        # a decimal like 1.5, or a site name like HealthCare.gov, is not a sentence end
        assert re.fullmatch(r"[A-Z][^.!?]*(?:\.\S[^.!?]*)*\.", p.plain), (indicator, p.plain)
        assert p.plain.casefold() != p.short.casefold(), indicator


def test_built_store_indicators_all_have_reader_text(real_con):
    stored = {r[0] for r in real_con.execute("SELECT indicator_id FROM indicator").fetchall()}
    assert stored <= set(plain_labels()), sorted(stored - set(plain_labels()))


def test_labels_read_in_lower_case_mid_sentence_except_names():
    assert in_sentence("Adults facing food insecurity") == "adults facing food insecurity"
    assert in_sentence("Kūpuna (65 and over)") == "kūpuna (65 and over)"
    assert in_sentence("Native Hawaiian alone") == "Native Hawaiian alone"
    assert in_sentence("EITC dollars claimed") == "EITC dollars claimed"


def _answer(blocks, caveats=()):
    f = FactRef("f1", "pop_65_plus", "house", "022", "acs", 1000.0, None, None, 100.0, "2020-2024")
    return Answer("1", ("house", "022"), "none", blocks, [f], [], list(caveats))


def test_website_writes_short_labels_and_the_eval_keeps_official_ones(small_con):
    blocks = [
        "* {label:f1}: {fact:f1}",
        "In {geo:house:022}, {label:f1} number {fact:f1}. {label:f1} are counted by the ACS.",
    ]
    out = build(small_con, _answer(blocks))
    assert out["lead"] == [
        "* Kūpuna (65 and over): 1,000",
        "In House District 22, kūpuna (65 and over) number 1,000. Kūpuna (65 and over) are counted by the ACS.",
    ]
    assert (
        render(_answer(blocks[:1]), small_con)
        == "* Population 65 years and over: 1,000 (margin of error ±100)"
    )


def test_tiles_and_charts_carry_what_the_label_means(small_con):
    other = FactRef("f2", "pop_65_plus", "house", "023", "acs", 100.0, None, None, 80.0, "2020-2024")
    answer = _answer(["x"])
    answer.facts.append(other)
    # Counts stay tiles, so both places get one.
    tiles = build(small_con, answer)["tiles"]
    assert [t["label"] for t in tiles] == ["Kūpuna (65 and over)"] * 2
    assert tiles[0]["meaning"] == {
        "plain": "People age 65 or older.",
        "official": "Population 65 years and over",
        "universe": "persons",
    }
    add_rate(small_con)
    rates = [rate("f1", "house", "022", 20.0, 1.0), rate("f2", "state", "15", 21.0, 0.5)]
    (chart,) = build(small_con, Answer("1", ("house", "022"), "none", ["x"], rates, [], []))["charts"]
    assert chart["label"] == "Share who are kūpuna (65 and over)"
    assert chart["meaning"]["plain"] == "The percent of all residents who are 65 or older."


def test_profile_rows_carry_the_official_label_and_universe(real_con):
    import json

    from klear.agent.tools import Session
    from klear.web.display import profile

    session = Session(real_con)
    raw = json.loads(session.run("district_profile", {"district": "house:022"}))
    rows = [r for s in profile(real_con, raw, session)["sections"] for r in s["rows"]]
    official = dict(real_con.execute("SELECT indicator_id, label FROM indicator").fetchall())
    for r in rows:
        assert r["label"] == plain_labels()[r["id"]].short
        assert (
            r["meaning"]["official"] == official[r["id"]]
            and r["meaning"]["plain"]
            and r["meaning"]["universe"]
        )


@pytest.mark.parametrize("term", GLOSSARY_TERMS)
def test_glossary_explains_each_term(term):
    html = (web.STATIC / "glossary.html").read_text(encoding="utf-8")
    assert re.search(rf'<div id="{term}"><dt>[^<]+', html), term


def test_glossary_is_served_and_linked_from_the_landing_page():
    assert "/glossary.html" in web.PUBLIC
    assert 'href="/glossary.html"' in (web.STATIC / "index.html").read_text(encoding="utf-8")
