"""The website assistant answers with the agent under a daily cap and falls back to the free lookup."""

import json
import threading
from functools import partial
from http.client import HTTPConnection
from http.server import ThreadingHTTPServer

import anthropic
import pytest

from klear.web import __main__ as web
from klear.web.assistant import BASIC, Assistant, BudgetExceeded, DailyBudget
from tests.agent.fake_client import FakeClient, api_error, tool
from tests.eval.conftest import _home  # noqa: F401

GOOD = {
    "resolved": "house:022",
    "refusal": "none",
    "blocks": ["{geo:house:022} has {fact:f1} {label:f1} ({vintage:f1})."],
    "facts": ["f1"],
    "programs": [],
    "caveats": [],
}
SCRIPT = [
    tool("get_facts", {"indicator_ids": ["pop_65_plus"], "geos": ["house:022"]}),
    tool("submit_answer", GOOD, "tu_submit"),
]
DISTRICT = ("house", "022")


@pytest.fixture
def budget(tmp_path):
    return DailyBudget(5.0, tmp_path / "web_spend.json", day=lambda: "2026-11-04")


def test_budget_holds_settles_and_caps(budget):
    rid = budget.reserve(3.0)
    assert budget.spent() == 3.0
    with pytest.raises(BudgetExceeded):
        budget.reserve(2.5)
    budget.settle(rid, 0.01)
    assert budget.spent() == pytest.approx(0.01)
    budget.settle(budget.reserve(1.0), None)
    assert budget.spent() == pytest.approx(1.01)


def test_budget_resets_each_day(tmp_path):
    day = ["2026-11-04"]
    b = DailyBudget(1.0, tmp_path / "web_spend.json", day=lambda: day[0])
    rid = b.reserve(1.0)
    day[0] = "2026-11-05"
    assert b.spent() == 0.0
    b.settle(rid, 1.0)
    assert b.spent() == 0.0
    b.reserve(1.0)


def test_assistant_answer_has_cards_and_settles_actual_cost(small_con, budget):
    fake = FakeClient(SCRIPT)
    result = Assistant(budget, client=fake).ask(small_con, "How many seniors live here?", DISTRICT)
    assert result["mode"] == "assistant" and result["refusal"] == "none"
    assert "House District 22" in result["text"] and "±100" in result["text"]
    card = result["facts"][0]
    assert card["geography"] == "House District 22" and card["year"] == "2020-2024"
    assert card["uncertainty"] == "ok" and card["value"].startswith("1,000")
    assert fake.calls == 2
    assert 0 < budget.spent() < 0.01


def test_cap_reached_uses_basic_lookup_without_calls(small_con, tmp_path):
    fake = FakeClient(SCRIPT)
    tiny = DailyBudget(0.0, tmp_path / "web_spend.json")
    result = Assistant(tiny, client=fake).ask(small_con, "How many kūpuna live here?", DISTRICT)
    assert result["mode"] == "basic" and result["lead"] == [BASIC]
    assert result["tiles"][0]["value"] == "1,000" and result["tiles"][0]["spread"] == "± 100"
    assert fake.calls == 0


def test_api_error_falls_back_and_keeps_full_hold(small_con, budget):
    fake = FakeClient(error=api_error(anthropic.InternalServerError, 500))
    result = Assistant(budget, client=fake).ask(small_con, "How many kūpuna live here?", DISTRICT)
    assert result["mode"] == "basic" and result["lead"] == [BASIC]
    assert budget.spent() > 0.01


def test_basic_refusal_has_no_fallback_note(small_con, tmp_path):
    tiny = DailyBudget(0.0, tmp_path / "web_spend.json")
    result = Assistant(tiny, client=FakeClient()).ask(small_con, "Which party should win here?", DISTRICT)
    assert result["refusal"] == "decline_partisan" and BASIC not in result["text"]


def test_chat_endpoint_uses_assistant(small_con, tmp_path, budget):
    small_con.close()
    assistant = Assistant(budget, client=FakeClient(SCRIPT))
    handler = partial(web.Handler, store=tmp_path / "small.duckdb", assistant=assistant)
    server = ThreadingHTTPServer(("127.0.0.1", 0), handler)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    try:
        con = HTTPConnection("127.0.0.1", server.server_port)
        body = json.dumps({"question": "How many seniors live here?", "district": "house:022"})
        con.request("POST", "/api/chat", body)
        response = con.getresponse()
        data = json.loads(response.read())
        assert response.status == 200 and data["mode"] == "assistant" and data["facts"]
    finally:
        server.shutdown()
        server.server_close()


def test_chat_profile_cites_the_same_figures_as_the_profile(real_con):
    from klear.agent.tools import Session
    from klear.web.assistant import basic

    answer = basic(real_con, "Give me a profile of this district.", DISTRICT)
    profile = json.loads(Session(real_con).run("district_profile", {"district": "house:022"}))
    assert {f["label"] for f in answer["facts"]} == {
        r["fact"]["label"] for s in profile["sections"] for r in s["rows"]
    }
    assert answer["lead"][0].startswith("This is the standard profile")
