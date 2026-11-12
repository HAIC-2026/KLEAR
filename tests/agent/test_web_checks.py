"""The How we check KLEAR page reads the latest build's validation.json and nothing else."""

import json
import re
import threading
from dataclasses import asdict
from functools import partial
from http.client import HTTPConnection

import pytest

from klear.validation import CheckResult
from klear.validation.report import CAVEATS, check_view, eval_tables, render_validation, skipped_view, verdict
from klear.web import __main__ as web
from klear.web.checks import checks_report

ACCURACY = {
    "kind": "summary", "chamber": "house", "indicator": "total_pop", "n_districts": 51, "median_ape": 0.8,
    "p90_ape": 2.1, "share_within_moe": 1.0, "conserved": True,
}  # fmt: skip
CHECKS = [
    CheckResult("boundaries_house", True, True, 51, 0, notes=["union area difference 0.0000%"]),
    CheckResult("apportionment", True, True, 1, 0, details=[ACCURACY]),
    CheckResult("irs_county", False, False, 4, 1, notes=["Kauai differs by 2%"]),
]
SKIPPED = [{"name": "chas", "reason": "needs a manual download.", "manual": True}]
EVAL = {
    "practice": {
        "recording": "claude-haiku-5-5-20261101T183501390320",
        "model": "claude-haiku-5-5",
        "recorded_at": "2026-11-01 18:35 HST",
        "questions": 2,
        "gates": [{"name": "overall", "label": "Answered correctly", "passed": 1, "total": 2}],
        "categories": [{"category": "profile", "label": "District profiles", "answered": 2, "passed": 1}],
        "failed": ["profile-002"],
    },
    "heldout": {
        "model": "claude-haiku-5-5",
        "run": "2026-11-03 HST",
        "reviewer": "off (the default)",
        "questions": 100,
        "source": "Published totals.",
        "results": [{"name": "overall", "label": "Answered correctly", "passed": 89, "total": 100}],
    },
    "limits": ["Checks cannot judge clarity."],
}


def validation(new_format=True) -> dict:
    if not new_format:
        return {
            "built_at": "2026-11-11 10:38:33 HST",
            "checks": [asdict(r) for r in CHECKS],
            "skipped": SKIPPED,
        }
    return {
        "built_at": "2026-11-11 10:38:33 HST (2026-11-11T20:38:33Z UTC)",
        "git_commit": "abc1234",
        "verdict": verdict(CHECKS, SKIPPED),
        "store": {"replaced": True, "reason": None, "kept_previous": False},
        "checks": [asdict(r) | check_view(r) for r in CHECKS],
        "skipped": [s | skipped_view(s) for s in SKIPPED],
        "caveats": CAVEATS,
        "eval": EVAL | {"tables": eval_tables(EVAL)},
    }


def test_report_carries_the_same_words_as_validation_md(tmp_path):
    path = tmp_path / "validation.json"
    path.write_text(json.dumps(validation()), encoding="utf-8")
    page = checks_report(path)
    text = render_validation(CHECKS, SKIPPED, {"eval": page["eval"]})
    assert page["git_commit"] == "abc1234" and page["store"]["replaced"]
    assert [c["name"] for c in page["checks"]] == ["boundaries_house", "apportionment", "irs_county", "chas"]
    for c in page["checks"]:
        assert f"## {c['title']}" in text
        assert c["result"] in ("PASS", "FAIL", "SKIPPED")
    assert page["checks"][2]["gate"] is False and page["checks"][3]["result"] == "SKIPPED"
    assert page["checks"][3]["summary"] == "Did not run: needs a manual download"
    rows = page["checks"][1]["tables"][0]["rows"]
    assert rows == [["House", "Total population", "51", "0.80%", "2.10%", "100%", "yes"]]
    assert "| House | Total population | 51 | 0.80% | 2.10% | 100% | yes |" in text
    assert "details" not in page["checks"][0] and page["caveats"] == CAVEATS
    assert [t["title"] for t in page["eval"]["tables"]] == [
        "Practice and held-out results",
        "Practice set by category",
    ]
    assert ["Answered correctly", "1 of 2 (50%)", "89 of 100 (89%)"] in page["eval"]["tables"][0]["rows"]
    assert "| Answered correctly | 1 of 2 (50%) | 89 of 100 (89%) |" in text


def test_report_from_an_older_build_gets_its_text_from_the_report_code(tmp_path):
    path = tmp_path / "validation.json"
    path.write_text(json.dumps(validation(new_format=False)), encoding="utf-8")
    page = checks_report(path)
    assert page["checks"][0]["title"] == "House district boundaries"
    assert page["checks"][0]["summary"] == "51 districts, 0 problems"
    assert page["verdict"].startswith("Build incomplete") and page["eval"] == {}


def test_eval_tables_say_what_is_missing():
    ev = {"practice": {"error": "no recorded run"}, "heldout": EVAL["heldout"]}
    rows = eval_tables(ev)[0]["rows"]
    assert rows[0] == ["Model", "not available", "claude-haiku-5-5"]
    assert ["Answered correctly", "not available", "89 of 100 (89%)"] in rows
    assert len(eval_tables(ev)) == 1


@pytest.fixture
def server(tmp_path):
    srv = web.Server(("127.0.0.1", 0), partial(web.Handler, store=tmp_path / "klear.duckdb"))
    thread = threading.Thread(target=srv.serve_forever, daemon=True)
    thread.start()
    yield srv.server_port, tmp_path
    srv.shutdown()
    srv.server_close()
    thread.join()


def get(port, path):
    con = HTTPConnection("127.0.0.1", port, timeout=10)
    con.request("GET", path)
    response = con.getresponse()
    data = response.read()
    con.close()
    return response.status, data, response.headers


def test_checks_endpoint_serves_the_build_next_to_the_store(server):
    port, build = server
    status, data, _ = get(port, "/api/checks")
    assert status == 503 and "No checked build yet" in json.loads(data)["error"]
    (build / "validation.json").write_text(json.dumps(validation()), encoding="utf-8")
    status, data, headers = get(port, "/api/checks")
    body = json.loads(data)
    assert status == 200 and headers["Cache-Control"] == "no-store"
    assert body["built_at"].startswith("2026-11-11") and len(body["checks"]) == 4
    for path in ("/checks.html", "/checks.js", "/checks.css"):
        assert get(port, path)[0] == 200, path


def test_checks_page_assets_are_public_and_linked_from_the_footer():
    page = (web.STATIC / "checks.html").read_text(encoding="utf-8")
    refs = re.findall(r'(?:src|href)="(/[^"#?]*)', page)
    assert {"/checks.js", "/checks.css", "/style.css"} <= set(refs) <= set(web.PUBLIC)
    footer = (web.STATIC / "index.html").read_text(encoding="utf-8").split("<footer>")[1]
    assert '<a href="/checks.html">How we check KLEAR</a>' in footer
    script = (web.STATIC / "checks.js").read_text(encoding="utf-8")
    assert "/api/checks" in script and "innerHTML" not in script
    for text in (page, script):
        assert "—" not in text
