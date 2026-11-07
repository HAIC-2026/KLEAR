"""The public web path reads only local facts and makes no model calls."""

import json
import os
import queue
import subprocess
import sys
import threading
from functools import partial
from http.client import HTTPConnection
from http.server import ThreadingHTTPServer
from urllib.parse import urlsplit

import pytest

from klear import config
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
    con = HTTPConnection("127.0.0.1", port, timeout=10)
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


@pytest.mark.parametrize(
    ("has_key", "basic_only", "expected"),
    [(False, False, "basic lookup only"), (True, True, "basic lookup only"), (True, False, "assistant,")],
)
def test_startup_reports_mode_and_missing_store(tmp_path, monkeypatch, capsys, has_key, basic_only, expected):
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    monkeypatch.setattr(config, "ENV_FILE", tmp_path / "no-user-env")
    if has_key:
        config.PROJECT_ENV_FILE.write_text("ANTHROPIC_API_KEY=local-test-key\n", encoding="utf-8")

    def stop(server):
        raise KeyboardInterrupt

    monkeypatch.setattr(ThreadingHTTPServer, "serve_forever", stop)
    args = ["--port", "0", "--store", str(tmp_path / "missing.duckdb")]
    if basic_only:
        args.append("--basic-only")
    web.main(args)
    out = capsys.readouterr().out
    assert expected in out
    assert "Landing page preview only" in out
    assert "uv run python -m klear.etl build" in out
    assert "Ctrl+C" in out
    assert "local-test-key" not in out
    if not has_key:
        assert "Add ANTHROPIC_API_KEY to .env and restart" in out


def test_startup_opens_the_bound_port(small_con, tmp_path, monkeypatch, capsys):
    small_con.close()
    opened = []
    monkeypatch.setattr(web.webbrowser, "open", opened.append)

    def stop(server):
        raise KeyboardInterrupt

    monkeypatch.setattr(ThreadingHTTPServer, "serve_forever", stop)
    web.main(["--port", "0", "--store", str(tmp_path / "small.duckdb"), "--basic-only", "--open"])
    out = capsys.readouterr().out
    assert len(opened) == 1 and opened[0] in out
    assert opened[0] != "http://127.0.0.1:0"
    assert "preview only" not in out


def test_startup_explains_occupied_port(capsys):
    with web.Server(("127.0.0.1", 0), web.Handler) as occupied, pytest.raises(SystemExit) as exc:
        web.main(["--port", str(occupied.server_port), "--basic-only"])
    assert exc.value.code == 2
    err = capsys.readouterr().err
    assert "Could not start KLEAR" in err
    assert "--port 8001" in err


def test_server_can_restart_after_serving_a_request(tmp_path):
    with web.Server(("127.0.0.1", 0), partial(web.Handler, store=tmp_path / "missing.duckdb")) as server:
        port = server.server_port
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        try:
            assert request(port, "GET", "/")[0] == 200
        finally:
            server.shutdown()
            thread.join(timeout=5)
    with web.Server(("127.0.0.1", port), web.Handler) as restarted:
        assert restarted.server_port == port


def test_web_command_serves_real_store_and_assets(small_con, tmp_path):
    small_con.close()
    # Cover store paths containing spaces on both platforms.
    store_dir = tmp_path / "local data"
    store_dir.mkdir()
    store = store_dir / "small.duckdb"
    (tmp_path / "small.duckdb").replace(store)
    startup = queue.Queue()
    process = subprocess.Popen(
        [sys.executable, "-m", "klear.web", "--basic-only", "--port", "0", "--store", str(store)],
        cwd=config.ROOT,
        env=os.environ | {"PYTHONIOENCODING": "utf-8"},
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
        encoding="utf-8",
    )

    def read_startup():
        startup.put(process.stdout.readline())

    reader = threading.Thread(target=read_startup, daemon=True)
    reader.start()
    try:
        line = startup.get(timeout=30)
        assert line.startswith("KLEAR: http://127.0.0.1:"), line
        port = urlsplit(line.split()[1]).port
        for path in ["/", "/app.js", "/style.css", "/background1.jpg", "/fonts/google-sans-latin.woff2"]:
            assert request(port, "GET", path)[0] == 200
        assert request(port, "GET", "/.env")[0] == 404
        status, resolved = request(port, "GET", "/api/resolve?q=House%2022")
        assert status == 200 and json.loads(resolved)["matches"][0]["geo"] == "house:022"
        status, answer = chat(port, "How many kūpuna live here?")
        assert status == 200 and answer["mode"] == "basic" and answer["facts"]
    finally:
        process.terminate()
        try:
            process.wait(timeout=10)
        except subprocess.TimeoutExpired:
            process.kill()
            process.wait(timeout=10)
        process.stdout.close()
        reader.join(timeout=5)
