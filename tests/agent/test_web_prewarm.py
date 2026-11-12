"""Prepared answers to the starter questions: the prewarm command, the cache file and the chat endpoint."""

import io
import json
import re
import shutil
import threading
from contextlib import redirect_stdout
from functools import partial
from http.client import HTTPConnection
from http.server import ThreadingHTTPServer
from types import SimpleNamespace

import anthropic
import duckdb
import pytest

from klear.eval import spend
from klear.eval.golden import ROOT
from klear.web import __main__ as web
from klear.web import prewarm
from klear.web.assistant import Assistant, DailyBudget
from klear.web.cache import QUESTIONS, AnswerCache, cache_path, read_entries, write_entries
from tests.agent.fake_client import FakeClient, api_error, tool, turn
from tests.eval.conftest import _home  # noqa: F401
from tests.fixtures.small_store import build_small_store

BUILD = "2026-11-11 10:19:07 HST (2026-11-11T20:19:07Z UTC)"
DISTRICTS = ["house:022", "house:023", "senate:012"]
KUPUNA = "How many kūpuna live in this district?"
GEO = re.compile(r"\(((?:house|senate):\d{3})\)")


class AnyDistrict:
    """Model stand-in for any district named in the prompt: one get_facts call, then an answer citing its figures."""

    def __init__(self):
        self.messages, self.calls, self._lock = self, 0, threading.Lock()

    def count_tokens(self, **kw):
        return SimpleNamespace(input_tokens=100)

    def create(self, **kw):
        with self._lock:
            self.calls += 1
        messages = kw["messages"]
        geo = GEO.search(messages[0]["content"][0]["text"]).group(1)
        if len(messages) == 1:
            return turn(tool("get_facts", {"indicator_ids": ["pop_total", "pop_65_plus"], "geos": [geo]}))
        refs = [
            f["ref"]
            for m in messages[1:]
            if m["role"] == "user"
            for b in m["content"]
            if isinstance(b, dict) and b.get("type") == "tool_result" and not b.get("is_error")
            for f in json.loads(b["content"]).get("facts", [])
        ]
        found = [f"{{label:{r}}}: {{fact:{r}}} ({{vintage:{r}}})." for r in refs]
        answer = {
            "resolved": geo,
            "refusal": "none" if refs else "not_available",
            "blocks": [f"{{geo:{geo}}}.", *found] if refs else [f"No figure is stored for {{geo:{geo}}}."],
            "facts": refs,
            "programs": [],
            "caveats": [],
        }
        return turn(tool("submit_answer", answer, "tu_submit"))


def small_store(folder):
    path = folder / "klear.duckdb"
    folder.mkdir()
    con = build_small_store(path)
    con.execute("INSERT INTO build_info VALUES (?, 'x', 0, 'ok', '[]', '{}')", [BUILD])
    con.close()
    return path


@pytest.fixture(scope="module")
def prepared(tmp_path_factory):
    """One prewarm run on the small store, its output and the fake's call count, shared by the tests that copy it."""
    work = tmp_path_factory.mktemp("prepared")
    path, fake, out = small_store(work / "store"), AnyDistrict(), io.StringIO()
    with pytest.MonkeyPatch.context() as mp:
        for name in ("LEDGER", "LOCK", "UNCERTAIN", "MIRROR"):
            mp.setattr(spend, name, work / "ledger" / name.lower())
        with redirect_stdout(out):
            code = prewarm.main(["--store", str(path), "--yes", "--jobs", "3"], client=fake)
        total = spend.SpendLog().total()
    return SimpleNamespace(folder=path.parent, code=code, out=out.getvalue(), calls=fake.calls, spent=total)


@pytest.fixture
def store(tmp_path):
    """A small store with no prepared answers."""
    return small_store(tmp_path / "store")


@pytest.fixture
def ready(tmp_path, prepared):
    """A copy of the small store with every starter answer prepared."""
    shutil.copytree(prepared.folder, tmp_path / "ready")
    return tmp_path / "ready" / "klear.duckdb"


def rebuild(path, built_at):
    with duckdb.connect(str(path)) as con:
        con.execute("UPDATE build_info SET built_at = ?", [built_at])


def serve(path, assistant=None, prepared=None):
    handler = partial(web.Handler, store=path, assistant=assistant, prepared=prepared)
    server = ThreadingHTTPServer(("127.0.0.1", 0), handler)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    return server


def ask(server, question, district="house:022"):
    con = HTTPConnection("127.0.0.1", server.server_port)
    con.request("POST", "/api/chat", json.dumps({"question": question, "district": district}))
    response = con.getresponse()
    return response.status, json.loads(response.read())


def test_questions_are_the_starter_buttons():
    app = (ROOT / "klear/web/static/app.js").read_text(encoding="utf-8")
    listed = re.search(r"^const examples = (\[.*\]);$", app, re.MULTILINE).group(1)
    assert [tuple(x) for x in json.loads(listed.replace("'", '"'))] == list(QUESTIONS.items())


def test_prewarm_answers_every_starter_question_once(prepared, ready):
    assert prepared.code == 0 and "15 of 15 starter answers are ready" in prepared.out
    assert 0 < prepared.spent < 0.01
    rows = read_entries(cache_path(ready))
    assert sorted((r["question"], r["district"]) for r in rows) == sorted(
        (q, d) for q in QUESTIONS for d in DISTRICTS
    )
    assert {(r["build"], r["model"]) for r in rows} == {(BUILD, "claude-haiku-5-5")}
    sample = (ready.parent / prewarm.SAMPLE).read_text(encoding="utf-8")
    assert sample.count("\n## ") == 12 and all(f"## {i}. " in sample for i in range(1, 13))
    assert all(f"Question: {q}" in sample for q in QUESTIONS)
    # A second run finds every answer ready and asks nothing.
    fake = AnyDistrict()
    assert prewarm.main(["--store", str(ready), "--yes"], client=fake) == 0
    assert fake.calls == 0 and len(read_entries(cache_path(ready))) == 15


def test_prewarm_asks_before_spending(store, monkeypatch):
    fake = AnyDistrict()
    monkeypatch.setattr("builtins.input", lambda prompt: "n")
    assert prewarm.main(["--store", str(store)], client=fake) == 2
    assert fake.calls == 0 and not cache_path(store).exists()
    assert spend.SpendLog().total() == 0


def test_failed_questions_are_left_to_answer_live(store, capsys):
    failing = FakeClient(error=api_error(anthropic.InternalServerError, 500))
    assert prewarm.main(["--store", str(store), "--yes", "--jobs", "1"], client=failing) == 1
    assert read_entries(cache_path(store)) == []
    out = capsys.readouterr().out
    assert "15 failed" in out and "0 of 15 starter answers are ready" in out
    # A failed call costs its full reservation.
    assert spend.SpendLog().total() == pytest.approx(15 * 0.027)


def test_prewarm_drops_answers_from_an_older_build(ready):
    rebuild(ready, "2026-11-12 08:00:00 HST (2026-11-12T18:00:00Z UTC)")
    fake = AnyDistrict()
    assert prewarm.main(["--store", str(ready), "--yes"], client=fake) == 0
    rows = read_entries(cache_path(ready))
    assert len(rows) == 15 and {r["build"][:10] for r in rows} == {"2026-11-12"}
    assert fake.calls >= 15


def test_chat_serves_a_prepared_answer_without_a_model_call(ready, tmp_path, monkeypatch):
    live = FakeClient()
    assistant = Assistant(DailyBudget(5.0, tmp_path / "web.json", day=lambda: "2026-11-11"), client=live)
    # Every answer slot is taken, which a prepared answer does not need.
    monkeypatch.setattr(web, "BUSY", threading.BoundedSemaphore(1))
    web.BUSY.acquire()
    server = serve(ready, assistant, AnswerCache(ready, "claude-haiku-5-5"))
    try:
        status, data = ask(server, f"  {KUPUNA} ", "house:22")
        assert status == 200 and data["mode"] == "assistant" and live.calls == 0
        assert data["prepared"] == "Prepared ahead of time from the data update of November 11, 2026."
        assert data["tiles"] or data["charts"]
        # A typed question is never looked up, so it waits for the model.
        assert ask(server, "How many kūpuna live here?")[0] == 429
        web.BUSY.release()
        status, data = ask(server, "How many kūpuna live here?")
        assert status == 200 and "prepared" not in data and live.calls == 1
    finally:
        server.shutdown()
        server.server_close()


def test_prepared_answers_reset_when_the_store_is_rebuilt(ready):
    server = serve(ready, prepared=AnswerCache(ready, "claude-haiku-5-5"))
    try:
        assert "prepared" in ask(server, KUPUNA)[1]
        rebuild(ready, "2026-11-12 08:00:00 HST (2026-11-12T18:00:00Z UTC)")
        status, data = ask(server, KUPUNA)
        assert status == 200 and data["mode"] == "basic" and "prepared" not in data
    finally:
        server.shutdown()
        server.server_close()


def test_warm_lays_out_every_answer_so_lookups_need_no_store(ready, monkeypatch):
    cache = AnswerCache(ready, "claude-haiku-5-5")
    assert cache.warm() == 15 and cache.warm() == 0

    def closed(*args, **kwargs):
        raise duckdb.IOException("store is busy")

    monkeypatch.setattr(duckdb, "connect", closed)
    assert all(cache.get(q, d)["prepared"] for q in QUESTIONS for d in DISTRICTS)


def test_cache_keys_by_model_and_skips_bad_entries(ready):
    path = cache_path(ready)
    rows = read_entries(path)
    typed = rows[0] | {"question": "What is the median rent?"}
    broken = rows[1] | {"answer": {"contract": "0"}}
    write_entries(path, [*rows[2:], typed, broken])
    assert len(read_entries(path)) == len(rows) - 2
    assert AnswerCache(ready, "claude-sonnet-5-5").get(KUPUNA, "house:022") is None
    haiku = AnswerCache(ready, "claude-haiku-5-5")
    assert haiku.get(KUPUNA, "house:022") is not None
    assert haiku.get("What is the median rent?", "house:022") is None
    assert haiku.get(KUPUNA, "county:15003") is None and haiku.get(KUPUNA, "house:099") is None
    path.write_text("{not json", encoding="utf-8")
    assert read_entries(path) == [] and haiku.get(KUPUNA, "house:022") is None


def test_startup_summary(store, ready):
    cache = AnswerCache(store, "claude-haiku-5-5")
    assert cache.summary(live=False) == ""
    assert "klear.web.prewarm" in cache.summary(live=True)
    cache = AnswerCache(ready, "claude-haiku-5-5")
    assert cache.summary(live=True).startswith("Starter questions: 15 prepared answers")
    rebuild(ready, "2026-11-12 08:00:00 HST (2026-11-12T18:00:00Z UTC)")
    assert "another data build" in cache.summary(live=True)
