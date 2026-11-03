"""The public web path reads only local facts and makes no model calls."""

import json
import threading
from functools import partial
from http.client import HTTPConnection
from http.server import ThreadingHTTPServer

import pytest

from klear.web import __main__ as web
from tests.eval.conftest import _home  # noqa: F401


@pytest.fixture
def endpoint(small_con, tmp_path):
    small_con.close()
    server = ThreadingHTTPServer(("127.0.0.1", 0), partial(web.Handler, store=tmp_path / "small.duckdb"))
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    yield server.server_port
    server.shutdown()
    server.server_close()
    thread.join()


def request(port, method, path, body=None, headers=None):
    con = HTTPConnection("127.0.0.1", port)
    con.request(method, path, body, headers or {})
    response = con.getresponse()
    data = response.read()
    con.close()
    return response.status, data


def chat(port, question, district="house:022"):
    status, data = request(
        port, "POST", "/api/chat", json.dumps({"question": question, "district": district})
    )
    return status, json.loads(data)


def test_lookup_and_private_files(endpoint):
    status, data = request(endpoint, "GET", "/api/resolve?q=House%2022")
    assert status == 200
    assert json.loads(data)["matches"][0]["geo"] == "house:022"
    for path in ["/../pyproject.toml", "/.env", "/data/build/klear.duckdb"]:
        assert request(endpoint, "GET", path)[0] == 404
    assert request(endpoint, "GET", "/background1.jpg")[0] == 200


def test_validation_and_cross_origin(endpoint):
    assert request(endpoint, "POST", "/api/chat", "[]")[0] == 400
    assert request(endpoint, "POST", "/api/chat", "{}", {"Origin": "https://other.example"})[0] == 403
    assert chat(endpoint, "Hello", "house:999")[0] == 400


def test_free_answer_has_figure_year_source_and_margin(endpoint, monkeypatch):
    monkeypatch.setenv("KLEAR_WEB_ENABLE_LIVE", "1")  # Old paid switch has no effect.
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    status, data = chat(endpoint, "How many kūpuna live here?")
    assert status == 200 and data["refusal"] == "none"
    assert "±100" in data["text"]
    fact = data["facts"][0]
    assert fact["year"] == "2020-2024"
    assert fact["source"] == "American Community Survey 5-year"
    assert fact["geography"] == "House District 22"
    assert fact["uncertainty"] == "ok"


def test_refusals_and_unknown_question(endpoint):
    assert chat(endpoint, "How can my party persuade voters?")[1]["refusal"] == "decline_partisan"
    assert chat(endpoint, "What is a resident's home address?")[1]["refusal"] == "decline_individual"
    assert chat(endpoint, "Explain quantum mechanics")[1]["refusal"] == "not_available"
