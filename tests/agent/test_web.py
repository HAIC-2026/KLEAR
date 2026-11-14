"""The public web path reads only local facts and makes no model calls."""

import csv
import io
import json
import os
import queue
import re
import socket
import subprocess
import sys
import threading
from collections import defaultdict, deque
from functools import partial
from html.parser import HTMLParser
from http.client import HTTPConnection
from http.server import ThreadingHTTPServer
from urllib.parse import urlsplit

import pytest

from klear import config
from klear.web import __main__ as web
from klear.web import display
from klear.web.assistant import Assistant
from tests.eval.conftest import _home  # noqa: F401


@pytest.fixture
def endpoint(small_con, tmp_path):
    small_con.close()
    server = web.Server(("127.0.0.1", 0), partial(web.Handler, store=tmp_path / "small.duckdb"))
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


def get(port, path):
    con = HTTPConnection("127.0.0.1", port)
    con.request("GET", path)
    response = con.getresponse()
    data = response.read()
    con.close()
    return response.status, data, response.headers


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


def test_public_files_and_cache_policy(endpoint):
    for path in web.PUBLIC:
        assert get(endpoint, path)[0] == 200, path
    _, body, headers = get(endpoint, "/fonts/public-sans.woff2")
    assert body[:4] == b"wOF2" and headers["Content-Type"] == "font/woff2"
    assert headers["Cache-Control"] == "public, max-age=604800"
    assert get(endpoint, "/")[2]["Cache-Control"] == "no-cache"
    for path in ["/api/resolve?q=House%2022", "/.env"]:
        assert get(endpoint, path)[2]["Cache-Control"] == "no-store"
    con = HTTPConnection("127.0.0.1", endpoint)
    con.request("POST", "/api/chat", "[]")
    response = con.getresponse()
    response.read()
    con.close()
    assert response.status == 400 and response.headers["Cache-Control"] == "no-store"


def test_logo_and_favicons_are_served_as_images(endpoint):
    types = {
        "/logo.svg": "image/svg+xml",
        "/favicon.svg": "image/svg+xml",
        "/favicon.ico": "image/x-icon",
        "/apple-touch-icon.png": "image/png",
    }
    for path, kind in types.items():
        status, body, headers = get(endpoint, path)
        assert status == 200 and headers["Content-Type"] == kind and body, path
    assert get(endpoint, "/favicon.ico")[1].startswith(bytes.fromhex("00000100"))
    assert get(endpoint, "/apple-touch-icon.png")[1].startswith(bytes.fromhex("89504e470d0a1a0a"))
    page = get(endpoint, "/")[1].decode("utf-8")
    for link in (
        'rel="icon" href="/favicon.ico?v=2"',
        'rel="icon" href="/favicon.svg?v=2"',
        'rel="apple-touch-icon"',
    ):
        assert link in page, link


def test_missing_public_file_is_not_cached(endpoint, monkeypatch, tmp_path):
    monkeypatch.setattr(web, "STATIC", tmp_path)
    status, _, headers = get(endpoint, "/fonts/public-sans.woff2")
    assert status == 404 and headers["Cache-Control"] == "no-store"


def test_malformed_requests_get_error_pages(endpoint):
    for line in [b"GET / XTTP/1.1", b"HELLO", b"BREW / HTTP/1.1"]:
        with socket.create_connection(("127.0.0.1", endpoint), timeout=10) as sock:
            sock.sendall(line + b"\r\n\r\n")
            reply = b"".join(iter(lambda s=sock: s.recv(65536), b""))
        assert re.search(rb"Error code: (400|501)", reply), line
    assert b"Cache-Control: no-store" in reply


def test_page_assets_are_public():
    css = (web.STATIC / "style.css").read_text(encoding="utf-8")
    html = (web.STATIC / "index.html").read_text(encoding="utf-8")
    refs = re.findall(r"url\(['\"]?(/[^)'\"]+)", css) + re.findall(r'(?:src|href)="(/[^"#?]*)', html)
    assert "/fonts/public-sans-ext.woff2" in refs
    assert set(refs) <= set(web.PUBLIC)


class _PageText(HTMLParser):
    """Text and readable attributes of a page, including hidden elements, without scripts, styles or comments."""

    READABLE = frozenset({"alt", "title", "aria-label", "placeholder", "content"})

    def __init__(self):
        super().__init__()
        self.parts, self.skip = [], 0

    def handle_starttag(self, tag, attrs):
        self.skip += tag in ("script", "style")
        self.parts += [v for k, v in attrs if k in self.READABLE and v]

    def handle_endtag(self, tag):
        self.skip -= tag in ("script", "style")

    def handle_data(self, data):
        if not self.skip:
            self.parts.append(data)


@pytest.mark.parametrize("page", sorted(p.name for p in web.STATIC.glob("*.html")))
def test_static_pages_have_no_bracketed_placeholders(page):
    parser = _PageText()
    parser.feed((web.STATIC / page).read_text(encoding="utf-8"))
    assert not re.findall(r"\[[^\]]*\]", " ".join(parser.parts)), page


def test_profile_endpoint_lays_out_the_district_profile(endpoint):
    status, data, headers = get(endpoint, "/api/profile?district=house:022")
    profile = json.loads(data)
    assert status == 200 and headers["Cache-Control"] == "no-store"
    assert profile["district"] == "House District 22" and profile["where"].startswith("Honolulu County")
    assert profile["county"] == "Honolulu County"
    assert profile["overlaps"] == [{"name": "Senate District 12", "share": "100%"}]
    row = next(r for s in profile["sections"] for r in s["rows"] if r["id"] == "pop_65_plus")
    assert row["label"] == "Kūpuna (65 and over)" and row["value"] == "1,000" and row["spread"] == "± 100"
    people = next(s for s in profile["sections"] if s["title"] == "People")
    assert people["sources"] == [
        {"name": "American Community Survey 5-year", "url": "https://example.org", "year": "2020-2024"}
    ]
    assert row["note"] == 0 and "·" not in profile["where"]
    assert [a["lead"] + " " + a["rest"] for a in row["against"]] == [
        "20.0% of the total in Honolulu County: 5,000",
        "11.1% of the total in Hawaiʻi: 9,000",
    ]
    programs = profile["programs"]
    assert programs["title"] == "Programs to explore here" and "eligibility" in programs["note"]
    shown = programs["programs"] + programs["others"]
    assert len({p["name"] for p in shown}) == len(shown) >= 12
    assert all(p["agency"] and p["verified"] and p["phone"] and ".gov" in p["url"] for p in shown)
    for bad in ["house:999", "county:15003", ""]:
        assert get(endpoint, "/api/profile?district=" + bad)[0] == 400


def test_profile_gives_the_official_description_and_permanent_residents(endpoint):
    plan = json.loads(get(endpoint, "/api/profile?district=house:022")[1])["plan"]
    assert plan["description"] == {
        "text": "The 22nd Representative District comprises the portion of the island of Oʻahu including Manoa.",
        "cite": "report page D-017, PDF page 891",
    }
    assert (
        plan["population"]["value"] == "26,451"
        and plan["population"]["cite"] == "report page 15, PDF page 23"
    )
    assert "not the same as the total population" in plan["population"]["note"]
    assert plan["source"]["url"] == "https://example.org/reapportionment"
    # a count left out by the build is not shown, and a district without either has no block
    partial = json.loads(get(endpoint, "/api/profile?district=house:023")[1])["plan"]
    assert "description" in partial and "population" not in partial
    assert json.loads(get(endpoint, "/api/profile?district=senate:012")[1])["plan"] is None


def test_profile_from_a_store_without_the_plan_table(small_con):
    small_con.execute("DROP TABLE district_plan")
    assert display.plan(small_con, "house:022") is None


def test_profile_gives_the_printed_summary_its_columns_programs_and_sources(endpoint):
    profile = json.loads(get(endpoint, "/api/profile?district=house:022")[1])
    row = next(r for s in profile["sections"] for r in s["rows"] if r["id"] == "pop_65_plus")
    assert [(a["geo"], a["place"], a["value"], a["spread"]) for a in row["against"]] == [
        ("county:15003", "Honolulu County", "5,000", "± 50"),
        ("state:15", "Hawaiʻi", "9,000", ""),
    ]
    assert row["against"][1]["source"] == row["source"]
    # The district's matched programs, then the rest: one entry per program across its tax years, each with an
    # address to print, and no income limit tables.
    listed = profile["programs"]["programs"] + profile["programs"]["others"]
    names = [p["name"] for p in listed]
    assert names.count("Federal EITC") == 1 and "SNAP" in names and len(names) == len(set(names))
    assert not any("poverty guidelines" in n or "income limits" in n for n in names)
    assert all(p["url"].startswith("https://") and p["description"] and p["agency"] for p in listed)
    assert profile["shares_source"]["year"] == "2020 Census"
    assert profile["built"] == ""  # the small store records no build


def test_profile_downloads_as_csv_with_raw_numbers_and_sources(endpoint):
    status, body, headers = get(endpoint, "/api/profile.csv?district=house:022")
    assert status == 200 and headers["Content-Type"] == "text/csv; charset=utf-8"
    assert headers["Content-Disposition"] == 'attachment; filename="klear-house-district-22.csv"'
    assert headers["Cache-Control"] == "no-store"
    text = body.decode("utf-8")
    assert text.startswith("﻿")
    rows = list(csv.reader(io.StringIO(text[1:])))
    acs = ["American Community Survey 5-year", "2020-2024", "https://example.org"]
    assert rows[0] == list(display.CSV_COLUMNS)
    kupuna = [r for r in rows if r[1] == "Population 65 years and over"]
    assert [r[2:5] for r in kupuna] == [
        ["House District 22", "1000", "100"],
        ["Honolulu County", "5000", "50"],
        ["Hawaiʻi", "9000", ""],
    ]
    assert kupuna[0][7] == "count" and kupuna[0][8:11] == acs
    for bad in ["house:999", ""]:
        assert get(endpoint, "/api/profile.csv?district=" + bad)[0] == 400


def test_district_addresses_serve_the_page(endpoint):
    # Any district address serves the page, which checks the number itself; nothing else under /district/ does.
    for path in ["/district/house-22", "/district/senate-1", "/district/house-07", "/district/house-99"]:
        status, body, headers = get(endpoint, path)
        assert status == 200 and b'id="print-sheet"' in body, path
        assert headers["Cache-Control"] == "no-cache" and headers["Content-Type"] == "text/html", path
    for path in ["/district/county-3", "/district/house-022", "/district/house-22/", "/district/house-22/x"]:
        assert get(endpoint, path)[0] == 404, path


def test_about_serves_store_facts_and_sources(endpoint):
    status, data, headers = get(endpoint, "/api/about")
    body = json.loads(data)
    assert status == 200 and headers["Cache-Control"] == "no-store"
    assert body["facts"][0] == "2 House and 1 Senate districts"
    assert "example" not in body and [s["publisher"] for s in body["sources"]] == [
        "Census",
        "HUD",
        "LRB",
        "LRB",
        "Office of Elections",
    ]


def test_about_without_a_store_is_unavailable(tmp_path):
    with web.Server(("127.0.0.1", 0), partial(web.Handler, store=tmp_path / "missing.duckdb")) as server:
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        try:
            status, data = request(server.server_port, "GET", "/api/about")
            assert status == 503 and "No data store has been built yet" in json.loads(data)["error"]
            assert request(server.server_port, "GET", "/")[0] == 200
        finally:
            server.shutdown()
            thread.join(timeout=5)


def test_validation_and_cross_origin(endpoint):
    assert request(endpoint, "POST", "/api/chat", "[]")[0] == 400
    status, data = request(endpoint, "POST", "/api/chat", "{}", {"Origin": "https://other.example"})
    assert status == 403 and json.loads(data)["error"].endswith("Ask it on the KLEAR site instead.")
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


def test_free_answer_adds_the_main_county_when_asked(endpoint, monkeypatch):
    # Keep these questions out of the rate limit the other tests share.
    monkeypatch.setattr(web, "_RECENT", defaultdict(deque))
    status, data = chat(endpoint, "Compare kūpuna in House District 22 with Honolulu County and the state.")
    assert status == 200 and data["mode"] == "basic" and data["refusal"] == "none"
    kupuna = [f["geography"] for f in data["facts"] if f["label"] == "Kūpuna (65 and over)"]
    assert kupuna == ["House District 22", "Honolulu County", "Hawaiʻi"]
    # Counts stay one tile led by the district, which names its share of the state's, with the others under it.
    (tile,) = data["tiles"]
    assert not data["charts"] and tile["geography"] == "House District 22"
    assert [p["geography"] for p in tile["places"]] == ["Honolulu County", "Hawaiʻi"]
    assert data["tiles"][0]["share"] == "11.1% of the Hawaiʻi total"
    assert "District, county and state figures" in data["text"]
    county_only = chat(endpoint, "How many kūpuna live in House District 22 and in its county?")[1]
    assert [f["geography"] for f in county_only["facts"]] == ["House District 22", "Honolulu County"]
    assert "District and county figures" in county_only["text"]
    alone = chat(endpoint, "How many kūpuna live here?")[1]
    assert {f["geography"] for f in alone["facts"]} == {"House District 22"}


def test_free_program_answer_lists_each_program_once_with_its_page(endpoint):
    status, data = chat(endpoint, "What support programs could residents of this district explore?")
    assert status == 200 and data["refusal"] == "none" and "eligibility" in data["text"]
    names = [p["name"] for p in data["programs"]]
    assert len(names) == len(set(names)) >= 12 and names.count("Federal EITC") == 1
    assert all(p["url"].startswith("https://") and "match" in p for p in data["programs"])
    assert not any("federalregister" in p["url"] or p["url"].endswith(".txt") for p in data["programs"])


def test_chat_takes_no_district_and_the_last_questions_and_keeps_nothing(endpoint, monkeypatch):
    monkeypatch.setattr(web, "_RECENT", defaultdict(deque))
    status, data = chat(endpoint, "How many kūpuna live in Kāneʻohe?", None)
    assert status == 200 and data["choices"][0]["name"] == "Kāneʻohe"
    status, data = chat(endpoint, "How many kūpuna live in Waikīkī?", None)
    assert status == 200 and data["district"]["geo"] == "house:022"
    subject = data["subject"]
    earlier = [
        {
            "question": "How many kūpuna live in Waikīkī?",
            "district": subject["geo"],
            "indicators": subject["indicators"],
        }
    ]
    body = {"question": "And how does that compare with the county?", "district": None, "earlier": earlier}
    status, raw = request(endpoint, "POST", "/api/chat", json.dumps(body))
    assert status == 200 and "Honolulu County" in {f["geography"] for f in json.loads(raw)["facts"]}
    # Answer text is never taken back, and the handler keeps no conversation between requests.
    body["earlier"] = [{"question": "q", "answer": "made-up figures"}]
    assert request(endpoint, "POST", "/api/chat", json.dumps(body))[0] == 400
    assert request(endpoint, "POST", "/api/chat", json.dumps({"question": "Hi", "district": 22}))[0] == 400
    assert chat(endpoint, "And how does that compare with the county?", None)[1]["facts"] == []


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
    warmed = threading.Event()
    monkeypatch.setattr(Assistant, "warm", staticmethod(warmed.set))
    args = ["--port", "0", "--store", str(tmp_path / "missing.duckdb")]
    if basic_only:
        args.append("--basic-only")
    web.main(args)
    out = capsys.readouterr().out
    assert expected in out
    # With the assistant on, its code loads at startup instead of on the first question.
    assert warmed.wait(5) if has_key and not basic_only else not warmed.is_set()
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


def test_server_start_does_not_look_up_host_names(monkeypatch):
    def lookup(*args):
        raise AssertionError("reverse DNS lookups can stall startup")

    monkeypatch.setattr(socket, "getfqdn", lookup)
    server = web.Server(("127.0.0.1", 0), web.Handler)
    try:
        assert server.server_name == "127.0.0.1" and server.server_port > 0
    finally:
        server.server_close()


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
        try:
            line = startup.get(timeout=30)
        except queue.Empty:
            process.kill()
            pytest.fail(
                "klear.web printed nothing within 30 s. Its output: " + repr(process.communicate()[0])
            )
        assert line.startswith("KLEAR: http://127.0.0.1:"), line
        port = urlsplit(line.split()[1]).port
        for path in ["/", "/app.js", "/map.js", "/style.css", "/background1.jpg", "/fonts/public-sans.woff2"]:
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


def test_profile_survives_a_program_catalog_that_cannot_be_read(endpoint, monkeypatch):
    def broken():
        raise ValueError("program catalog cannot be read")

    monkeypatch.setattr("klear.agent.tools.catalog", broken)
    status, body = get(endpoint, "/api/profile?district=house:022")[:2]
    profile = json.loads(body)
    assert status == 200 and profile["programs"] is None and profile["sections"]


def test_web_server_starts_without_loading_the_model_sdk():
    code = "import sys, klear.web.__main__; print('anthropic' in sys.modules)"
    result = subprocess.run(
        [sys.executable, "-c", code], cwd=config.ROOT, capture_output=True, text=True, check=True
    )
    assert result.stdout.strip() == "False"
