"""Reader text stays at about a grade 8 reading level: measure definitions, the glossary, the site's pages and
messages, and decline messages. Grades are Flesch-Kincaid estimates (tests/readability.py)."""

import re

import pytest

from klear.agent.answer import REFUSAL_TEXT
from klear.labels import plain_labels
from klear.web.__main__ import STATIC
from klear.web.answers import ask
from klear.web.geocode import NOTES as ADDRESS_NOTES
from tests.readability import grade, page_text, syllables

TARGET = 8.0
# One sentence full of names, like Supplemental Security Income, can read harder; none may read much harder.
CEILING = 12.5
# Blocks with fewer words are labels and buttons, which would pull a page's grade down.
PROSE_WORDS = 6


def test_the_estimate_tells_plain_text_from_hard_text():
    assert [syllables(w) for w in ("home", "people", "insurance", "kūpuna", "SNAP")] == [1, 2, 3, 3, 1]
    assert grade(["The cat sat on the mat."]) < 2
    assert grade(["Noninstitutionalized civilian populations demonstrate considerable heterogeneity."]) > 16


def test_measure_definitions():
    plains = {i: p.plain for i, p in plain_labels().items()}
    assert grade(list(plains.values())) <= TARGET
    hard = {i: round(grade([p]), 1) for i, p in plains.items() if grade([p]) > CEILING}
    assert not hard, hard


@pytest.mark.parametrize("page", ["glossary.html", "index.html", "checks.html", "outreach.html"])
def test_page_prose(page):
    blocks = [
        b for b in page_text((STATIC / page).read_text(encoding="utf-8")) if len(b.split()) >= PROSE_WORDS
    ]
    assert blocks and grade(blocks) <= TARGET, page


def _messages(script: str = "app.js") -> list[str]:
    """Sentences a site script shows: string literals that start with a capital and end a sentence."""
    js = (STATIC / script).read_text(encoding="utf-8")
    out = []
    for single, template in re.findall(r"'((?:[^'\\\n]|\\.)*)'|`((?:[^`\\]|\\.)*)`", js):
        text = re.sub(r"\$\{[^}]*\}", "it", single or template)
        if (
            re.match(r"[A-Z]", text)
            and re.search(r"[.?!]$", text)
            and len(text.split()) >= 4
            and "<" not in text
        ):
            out.append(text)
    return out


def test_site_messages():
    messages = _messages()
    assert len(messages) > 20 and grade(messages) <= TARGET


def test_program_checked_lines():
    from klear.programs import catalog
    from klear.web.display import checked_text

    texts = [checked_text(catalog()[0], stale) for stale in (False, True)]
    assert grade(texts) <= TARGET


def test_outreach_page_messages():
    """The outreach page's own messages, and the sentences the server writes for it and for the profile note."""
    from klear.agent.config import OUTREACH_PAIRS
    from klear.web import display

    who = OUTREACH_PAIRS["kupuna"]["who"]
    texts = [
        *_messages("outreach.js"),
        *(display._tier_note(i, n) for i in (0, 1) for n in (1, 3)),
        display._unreliable_text(1, 25),
        display._unreliable_text(12, 51),
        (
            f"House District 4 is one of 2 House districts in the top group for {who}, with 2,839 ± 618. "
            "Their ranges overlap."
        ),
        (
            "29 of 51 House districts share the top group for uninsured people with income under 138% of poverty, "
            "from 214 to 601 each. Their ranges overlap, so no one district stands out."
        ),
    ]
    assert grade(texts) <= TARGET


def test_decline_messages():
    district = ("house", "022")
    texts = [
        *REFUSAL_TEXT.values(),
        ask(None, "Who lives at 12 Main Street?", district)["text"],
        ask(None, "Which candidate should I vote for?", district)["text"],
        *ADDRESS_NOTES.values(),
    ]
    assert grade(texts) <= TARGET
    hard = {t: round(grade([t]), 1) for t in texts if grade([t]) > 10}
    assert not hard, hard


def test_map_shading_messages():
    """The shaded map's messages in map.js, and the sentences the server writes for its legend and readout."""
    from klear.web import shade

    js = (STATIC / "map.js").read_text(encoding="utf-8")
    found = []
    for single, template in re.findall(r"'((?:[^'\\\n]|\\.)*)'|`((?:[^`\\]|\\.)*)`", js):
        text = re.sub(r"\$\{[^}]*\}", "it", single or template)
        if re.match(r"[A-Z]", text) and re.search(r"[.?!]$", text) and len(text.split()) >= 4:
            found.append(text)
    texts = [
        *found,
        shade.HOW,
        shade.OVERLAP_NOTE,
        shade._unreliable(1, 25),
        shade._unreliable(20, 51),
        "People below the poverty level: 23.4% ± 3.1 points. About 2.3 times the rate in Hawaiʻi: 10.0%.",
        (
            "Keiki below the poverty level: 34.1% ± 11.9 points. About 2.8 times the rate in Hawaiʻi: 12.1%. "
            "Unreliable estimate: the margin of error is large."
        ),
        "People below the poverty level: no figure. Too few cases for the source to publish.",
    ]
    assert len(found) >= 4 and grade(texts) <= TARGET
    hard = {t: round(grade([t]), 1) for t in texts if grade([t]) > CEILING}
    assert not hard, hard
