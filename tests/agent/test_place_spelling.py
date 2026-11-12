# Places shown with ʻokina and kahakō are still found by the plain letters people type.
import json

import pandas as pd
import pytest

from klear.agent.tools import Session
from klear.etl.places_gazetteer import load_overrides, plain_name
from klear.store.db import add_places

# Census place id, the plain-letter Census name and searches that should find it
PLACES = {
    "1509260": ("Haiku-Pauwela", ["Haiku", "Pauwela", "haiku pauwela"]),
    "1516160": ("Honaunau-Napoopoo", ["Honaunau", "Napoopoo"]),
    "1521230": ("Kahaluu-Keauhou", ["Keauhou", "Kahaluu Keauhou"]),
    "1527350": ("Kamaili", ["Kamaili"]),
    "1528287": ("Kaneohe Base", ["Kaneohe Base", "Kane'ohe Base"]),
    "1547050": ("Mahinahina", ["mahinahina"]),
    "1549425": ("Manele", ["Manele"]),
    "1554100": ("Napili-Honokowai", ["Napili", "Honokowai"]),
    "1572957": ("Waiahole-Waikane", ["Waiahole", "Waikane"]),
    "1575510": ("Waihee-Waiehu", ["Waiehu", "Waihee-Waiehu"]),
}


@pytest.fixture
def spelled(small_con):
    names = load_overrides()
    place = pd.DataFrame(
        [(pid, names[pid], plain_name(names[pid]), 10) for pid in PLACES],
        columns=["place_id", "name", "plain", "pop2020"],
    )
    overlap = pd.DataFrame(
        [(pid, "county", "15003", 10, 1.0) for pid in PLACES],
        columns=["place_id", "geo_type", "geo_id", "pop2020", "share"],
    )
    add_places(small_con, place, overlap)
    return Session(small_con), names


@pytest.mark.parametrize("pid", PLACES)
def test_plain_letters_find_the_hawaiian_spelling(spelled, pid):
    session, names = spelled
    census, searches = PLACES[pid]
    assert names[pid] != census and plain_name(names[pid]) == plain_name(census)
    for text in searches:
        found = json.loads(session.run("resolve_place", {"text": text}))["matches"]
        assert names[pid] in [m["name"] for m in found], text
        assert names[pid] in [s["label"] for s in session.suggest(text)], text


def test_kaneohe_still_finds_kaneohe(spelled):
    session, _ = spelled
    found = json.loads(session.run("resolve_place", {"text": "Kaneohe"}))["matches"]
    assert [m["name"] for m in found] == ["Kāneʻohe"]
