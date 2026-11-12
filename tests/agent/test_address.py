"""Street addresses in chat: the agent tool, the basic lookup and the chat endpoint, with a fake geocoder."""

import json
import threading
from functools import partial
from http.client import HTTPConnection

import pytest

from klear.agent.loop import KlearAgent
from klear.agent.prompt import SYSTEM
from klear.agent.tools import ADDRESS_FOUND, Session
from klear.eval.contract import render
from klear.web import __main__ as web
from klear.web.assistant import Assistant, DailyBudget, basic
from klear.web.geocode import NOTES, GeocoderError, address_in
from tests.agent.fake_client import FakeClient, tool
from tests.eval.conftest import _home  # noqa: F401

# Kāneʻohe is mostly House 22 in the small store, but this block of it is in House 23.
BLOCK = "150030001001001"
MATCHED = "1 TEST ST, KANEOHE, HI, 96744"
SELECTED = ("house", "022")


def geocoder(calls: list, reply=((BLOCK, MATCHED),), error: bool = False):
    def lookup(text):
        calls.append(text)
        if error:
            raise GeocoderError("Census geocoder failed: ConnectError")
        return list(reply)

    return lookup


@pytest.mark.parametrize(
    ("question", "address"),
    [
        (
            "Which House and Senate district is 310 W Kaʻahumanu Ave, Kahului in?",
            "310 W Kaʻahumanu Ave, Kahului",
        ),
        (
            "What district is 415 S Beretania St, Honolulu, HI 96813?",
            "415 S Beretania St, Honolulu, HI 96813",
        ),
        ("How many kūpuna live near 94-275 Mokuola St in Waipahu?", "94-275 Mokuola St"),
        ("Is House District 22 near 1 Test St?", "1 Test St"),
        ("46-056 Kamehameha Hwy, Kāneʻohe", "46-056 Kamehameha Hwy, Kāneʻohe"),
        ("What is the poverty rate in House 22?", None),
        ("How many people in Senate District 12 live in 2 or more units?", None),
        ("What about ZIP code 96744?", None),
    ],
)
def test_address_in_finds_the_address_a_question_names(question, address):
    assert address_in(question) == address


def test_resolve_place_finds_the_address_district_not_the_town_majority(small_con):
    calls = []
    session = Session(small_con, geocoder(calls))
    out = json.loads(session.run("resolve_place", {"text": "1 Test St, Kāneʻohe"}))
    [m] = out["matches"]
    assert m["kind"] == "address" and [o["geo"] for o in m["overlaps"]] == [
        "house:023",
        "senate:012",
        "county:15003",
    ]
    assert out["note"] == ADDRESS_FOUND and "refusal none" in out["note"]
    assert {("house", "023"), ("senate", "012")} <= session.resolved and "Kāneʻohe" in session.places
    assert calls == ["1 Test St, Kāneʻohe"]


@pytest.mark.parametrize(
    ("lookup", "outcome"),
    [(None, "off"), (geocoder([], error=True), "failed"), (geocoder([], reply=()), "none")],
)
def test_resolve_place_never_guesses_a_district_for_an_unmatched_address(small_con, lookup, outcome):
    session = Session(small_con, lookup)
    out = json.loads(session.run("resolve_place", {"text": "1 Test St, Kāneʻohe"}))
    assert out["matches"] == [] and out["address"] == outcome and out["note"] == NOTES[outcome]
    assert "refusal none" in out["hint"] and "please give a place, ZIP code or district" in out["hint"]
    assert not session.resolved


def test_prompt_treats_a_street_address_as_a_place():
    assert "A street address alone is a place, so refusal none" in SYSTEM
    assert "a household or a street address" not in SYSTEM


def test_agent_answers_for_the_address_district_with_refusal_none(small_con):
    calls = []
    submit = {
        "resolved": "house:023",
        "refusal": "none",
        "blocks": ["That address in Kāneʻohe is in {geo:house:023} and {geo:senate:012}."],
        "facts": [],
        "programs": [],
        "caveats": [],
    }
    fake = FakeClient(
        [tool("resolve_place", {"text": "1 Test St, Kāneʻohe"}), tool("submit_answer", submit, "tu_submit")]
    )
    agent = KlearAgent(small_con, client=fake, geocoder=geocoder(calls))
    answer, _ = agent.answer("web", "Which House and Senate district is 1 Test St, Kāneʻohe in?", SELECTED)
    assert answer.refusal == "none" and answer.resolved == ("house", "023")
    assert "House District 23" in render(answer, small_con) and calls == ["1 Test St, Kāneʻohe"]
    sent = json.dumps(fake.requests[1]["messages"], ensure_ascii=False, default=str)
    assert "house:023" in sent and ADDRESS_FOUND in sent


def test_assistant_passes_the_geocoder_to_the_agent(small_con, tmp_path):
    calls = []
    submit = {"resolved": "house:023", "refusal": "none", "blocks": ["It is in {geo:house:023}."]}
    submit |= {"facts": [], "programs": [], "caveats": []}
    fake = FakeClient(
        [tool("resolve_place", {"text": "1 Test St"}), tool("submit_answer", submit, "tu_submit")]
    )
    result = Assistant(DailyBudget(5.0, tmp_path / "spend.json"), client=fake).ask(
        small_con, "Which district is 1 Test St in?", SELECTED, geocoder=geocoder(calls)
    )
    assert result["mode"] == "assistant" and result["refusal"] == "none" and calls == ["1 Test St"]


def test_basic_names_the_address_districts(small_con):
    calls = []
    result = basic(
        small_con,
        "Which House and Senate district is 1 Test St, Kāneʻohe in?",
        SELECTED,
        geocoder=geocoder(calls),
    )
    assert result["refusal"] == "none" and result["mode"] == "basic"
    assert "House District 23 and Senate District 12, in Honolulu County" in result["text"]
    assert MATCHED in result["text"] and "House District 22" not in result["text"]
    assert calls == ["1 Test St, Kāneʻohe"]


def test_basic_answers_the_question_for_the_address_district(small_con):
    result = basic(
        small_con, "How many kūpuna live near 1 Test St, Kāneʻohe?", SELECTED, geocoder=geocoder([])
    )
    assert result["refusal"] == "none"
    assert MATCHED in result["lead"][0] and "House District 23" in result["lead"][0]
    assert {f["geography"] for f in result["facts"]} == {"House District 23"}


@pytest.mark.parametrize(
    ("lookup", "outcome"),
    [(None, "off"), (geocoder([], error=True), "failed"), (geocoder([], reply=()), "none")],
)
def test_basic_says_why_an_address_was_not_placed(small_con, lookup, outcome):
    result = basic(small_con, "Which House district is 1 Test St, Kāneʻohe in?", SELECTED, geocoder=lookup)
    assert result["refusal"] == "none" and result["text"].startswith(NOTES[outcome])
    assert "House District" not in result["text"] and "place, ZIP code or district" in result["text"]


@pytest.mark.parametrize(
    "question",
    ["Who lives at 1 Test St, Kāneʻohe?", "How much does the household at 1 Test St, Kāneʻohe earn?"],
)
def test_questions_about_the_people_at_an_address_are_still_declined(small_con, question):
    calls = []
    result = basic(small_con, question, SELECTED, geocoder=geocoder(calls))
    assert result["refusal"] == "decline_individual" and calls == []


@pytest.fixture
def chat(small_con, tmp_path):
    small_con.close()
    servers = []

    def ask(lookup, question):
        handler = partial(web.Handler, store=tmp_path / "small.duckdb", geocoder=lookup)
        srv = web.Server(("127.0.0.1", 0), handler)
        threading.Thread(target=srv.serve_forever, daemon=True).start()
        servers.append(srv)
        con = HTTPConnection("127.0.0.1", srv.server_port, timeout=30)
        con.request("POST", "/api/chat", json.dumps({"question": question, "district": "house:022"}))
        response = con.getresponse()
        data = json.loads(response.read())
        con.close()
        return response.status, data

    yield ask
    for srv in servers:
        srv.shutdown()
        srv.server_close()


def test_chat_endpoint_looks_up_an_address_only_when_lookup_is_on(chat):
    calls = []
    status, data = chat(geocoder(calls), "Which House and Senate district is 1 Test St, Kāneʻohe in?")
    assert status == 200 and data["refusal"] == "none" and "House District 23" in data["text"]
    assert calls == ["1 Test St, Kāneʻohe"]
    status, data = chat(None, "Which House and Senate district is 1 Test St, Kāneʻohe in?")
    assert status == 200 and data["refusal"] == "none" and data["text"].startswith(NOTES["off"])
