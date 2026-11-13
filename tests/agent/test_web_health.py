"""The health and readiness checks read only the process, the store and the prepared answers, never the model."""

import json
from http.client import HTTPConnection

import pytest

from klear.web.assistant import Assistant, DailyBudget
from klear.web.cache import AnswerCache
from tests.agent.fake_client import FakeClient
from tests.agent.test_web_prewarm import prepared, ready, serve, small_store  # noqa: F401
from tests.eval.conftest import _home  # noqa: F401

KEY = "sk-ant-test-key-never-shown"


def get(server, path):
    con = HTTPConnection("127.0.0.1", server.server_port)
    con.request("GET", path)
    response = con.getresponse()
    raw = response.read().decode()
    return response.status, json.loads(raw), raw, response.getheader("Cache-Control")


@pytest.fixture
def live(tmp_path, monkeypatch):
    monkeypatch.setenv("ANTHROPIC_API_KEY", KEY)
    fake = FakeClient()
    budget = DailyBudget(5.0, tmp_path / "web.json", day=lambda: "2026-11-11")
    return Assistant(budget, client=fake), fake


@pytest.mark.parametrize("path", ["/healthz", "/api/health"])
def test_health_says_the_process_is_up_even_without_a_store(tmp_path, live, path):
    assistant, fake = live
    server = serve(tmp_path / "missing" / "klear.duckdb", assistant)
    try:
        status, data, _, cache = get(server, path)
        assert (status, data, cache) == (200, {"status": "ok"}, "no-store")
        assert fake.calls == 0 and fake.counts == []
    finally:
        server.shutdown()
        server.server_close()


@pytest.mark.parametrize("path", ["/readyz", "/api/ready"])
def test_ready_reports_store_build_prepared_answers_and_assistant(ready, live, path):  # noqa: F811
    assistant, fake = live
    server = serve(ready, assistant, AnswerCache(ready, "claude-haiku-5-5"))
    try:
        status, data, raw, cache = get(server, path + "?q=How+many+kupuna")
        assert status == 200 and cache == "no-store"
        assert data == {
            "ready": True,
            "store": "ok",
            "built": "2026-11-11",
            "prepared_answers": 15,
            "assistant": "on",
        }
        assert fake.calls == 0 and fake.counts == []
        for secret in (KEY, str(ready.parent), "kupuna", "duckdb"):
            assert secret not in raw
    finally:
        server.shutdown()
        server.server_close()


def test_ready_without_prepared_answers_or_assistant(tmp_path):
    store = small_store(tmp_path / "store")
    server = serve(store, None, AnswerCache(store, "claude-haiku-5-5"))
    try:
        status, data, _, _ = get(server, "/readyz")
        assert status == 200 and data["prepared_answers"] == 0 and data["assistant"] == "off"
    finally:
        server.shutdown()
        server.server_close()


@pytest.mark.parametrize(
    ("make", "state"),
    [(lambda p: None, "missing"), (lambda p: p.write_bytes(b"not a store"), "unreadable")],
)
def test_not_ready_without_a_usable_store(tmp_path, make, state):
    store = tmp_path / "klear.duckdb"
    make(store)
    server = serve(store, None, AnswerCache(store, "claude-haiku-5-5"))
    try:
        status, data, raw, cache = get(server, "/readyz")
        assert status == 503 and cache == "no-store"
        assert data == {
            "ready": False,
            "store": state,
            "built": None,
            "prepared_answers": 0,
            "assistant": "off",
        }
        assert str(tmp_path) not in raw
    finally:
        server.shutdown()
        server.server_close()
