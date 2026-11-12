# Places shown with ʻokina and kahakō are found however people type them: with the ʻokina, with any character
# people use for it (' ` ‘ ’ ʼ and the rest), or with none, and with kahakō, other accents or plain letters.
import json
import threading
import unicodedata
from functools import partial
from http.client import HTTPConnection
from urllib.parse import quote

import pandas as pd
import pytest

from klear.agent.tools import Session
from klear.etl.places_gazetteer import load_overrides, plain_name
from klear.store.db import add_places
from klear.text import OKINA, OKINA_LOOKALIKES
from klear.web import __main__ as web

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
# More places from the checked-in spellings: a leading ʻokina, one in the middle, and one per word
MORE = ("1500550", "1507450", "1574450")
# Every way to type the ʻokina: itself, each lookalike, and nothing
MARKS = [OKINA, *OKINA_LOOKALIKES, ""]
# Names to type, each with an ʻokina: Census places, then an island from the small store
TYPED = ["Kāneʻohe", "ʻAiea", "ʻEwa Beach", "Waiʻanae", "Hōnaunau-Nāpoʻopoʻo", "Haʻikū-Paʻuwela", "Oʻahu"]


def _mark(m: str) -> str:
    return f"U+{ord(m):04X}" if m else "none"


def kahako_styles(text: str) -> list[str]:
    """text with kahakō as written, as a vowel and a combining macron, as an umlaut, and as a plain vowel."""
    combining = unicodedata.normalize("NFD", text)
    plain = "".join(c for c in combining if c != "̄")
    umlaut = unicodedata.normalize("NFC", combining.replace("̄", "̈"))
    return [text, combining, umlaut, plain]


@pytest.fixture
def store(small_con):
    names = load_overrides()
    ids = [*PLACES, *MORE]
    place = pd.DataFrame(
        [(pid, names[pid], plain_name(names[pid]), 10) for pid in ids],
        columns=["place_id", "name", "plain", "pop2020"],
    )
    overlap = pd.DataFrame(
        [(pid, "county", "15003", 10, 1.0) for pid in ids],
        columns=["place_id", "geo_type", "geo_id", "pop2020", "share"],
    )
    add_places(small_con, place, overlap)
    return small_con


@pytest.fixture
def spelled(store):
    return Session(store), load_overrides()


def found(session, text) -> list[str]:
    return [m["name"] for m in json.loads(session.run("resolve_place", {"text": text}))["matches"]]


def labels(session, text) -> list[str]:
    return [s["label"] for s in session.suggest(text)]


@pytest.mark.parametrize("pid", PLACES)
def test_plain_letters_find_the_hawaiian_spelling(spelled, pid):
    session, names = spelled
    census, searches = PLACES[pid]
    assert names[pid] != census and plain_name(names[pid]) == plain_name(census)
    for text in searches:
        assert names[pid] in found(session, text), text
        assert names[pid] in labels(session, text), text


def test_kaneohe_still_finds_kaneohe(spelled):
    session, _ = spelled
    assert found(session, "Kaneohe") == ["Kāneʻohe"]


@pytest.mark.parametrize("mark", MARKS, ids=_mark)
@pytest.mark.parametrize("name", TYPED)
def test_any_okina_and_kahako_finds_the_place(spelled, name, mark):
    session, _ = spelled
    for text in kahako_styles(name.replace(OKINA, mark)):
        assert found(session, text)[0] == name, text
        assert found(session, text.upper())[0] == name, text.upper()
        assert labels(session, text)[0] == name, text


@pytest.mark.parametrize("mark", MARKS, ids=_mark)
def test_any_okina_with_the_state_name(spelled, mark):
    session, _ = spelled
    assert found(session, f"Kāne{mark}ohe, Hawai{mark}i")[0] == "Kāneʻohe"
    assert found(session, f"Wai{mark}anae Hawai{mark}i")[0] == "Waiʻanae"


def test_full_width_letters_find_the_place(spelled):
    session, _ = spelled
    assert found(session, "ＫＡＮＥＯＨＥ")[0] == "Kāneʻohe"
    assert labels(session, "Ｗａｉ＇ａｎａｅ")[0] == "Waiʻanae"


@pytest.fixture
def endpoint(store, tmp_path):
    store.close()
    server = web.Server(("127.0.0.1", 0), partial(web.Handler, store=tmp_path / "small.duckdb"))
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    yield server.server_port
    server.shutdown()
    server.server_close()
    thread.join()


def get_json(port, path):
    con = HTTPConnection("127.0.0.1", port, timeout=10)
    con.request("GET", path)
    response = con.getresponse()
    data = json.loads(response.read())
    con.close()
    assert response.status == 200, data
    return data


@pytest.mark.parametrize("mark", MARKS, ids=_mark)
def test_site_search_reads_any_okina(endpoint, mark):
    for name in ("Kāneʻohe", "ʻAiea", "Haʻikū-Paʻuwela"):
        q = quote(name.replace(OKINA, mark))
        assert get_json(endpoint, f"/api/resolve?q={q}")["matches"][0]["name"] == name
        assert get_json(endpoint, f"/api/suggest?q={q}")["suggestions"][0]["label"] == name


@pytest.mark.parametrize("mark", ["'", "`", "‘", "’", "ʼ", ""])
def test_built_store_reads_any_okina(session, mark):
    # In the built store, when there is one: Census places, a neighborhood board area, a county division and an
    # island, then a neighborhood name that leads to its board area
    for name in ("Kāneʻohe", "Lānaʻi City", "Haʻikū-Paʻuwela", "Waiʻalae-Kāhala", "Kōloa-Poʻipū", "Molokaʻi"):
        text = name.replace(OKINA, mark)
        assert name in found(session, text), text
        assert name in labels(session, text), text
    text = "ʻĀina Haina".replace(OKINA, mark)
    assert found(session, text) == ["Kuliʻouʻou-Kalani Iki"]
    assert "ʻĀina Haina" in labels(session, text)
