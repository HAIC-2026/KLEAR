"""The How we check KLEAR page reads the served store's validation.json, shows it only when it is that store's, and
says how the latest build attempt went."""

import json
import re
import threading
from dataclasses import asdict
from functools import partial
from http.client import HTTPConnection

import pytest

from klear.store.db import open_store
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
    assert rows[1] == ["Model", "not available", "claude-haiku-5-5"]
    assert ["Answered correctly", "not available", "89 of 100 (89%)"] in rows
    assert len(eval_tables(ev)) == 1


def test_eval_tables_name_each_kind_of_run_and_count_what_was_scored():
    practice = EVAL["practice"] | {"questions": 77, "answered": 75}
    rows = eval_tables({"practice": practice, "heldout": EVAL["heldout"]})[0]["rows"]
    assert rows[0] == [
        "Kind of run",
        "Recorded live answers, scored again against this build's data (no new model calls)",
        "Live run on questions kept outside this repository (published totals only)",
    ]
    assert rows[2] == ["Run", "Recorded 2026-11-01 18:35 HST, rescored with this build", "2026-11-03 HST"]
    assert rows[3] == ["Questions", "77 in the set: 75 answered, 2 with no recorded answer", "100"]


def _store_built(path, built_at):
    """An empty store that records only its build, as the checks endpoint reads it."""
    con = open_store(path)
    con.execute("DELETE FROM build_info")
    con.execute("INSERT INTO build_info VALUES (?, 'abc1234', 0, 'ok', '[]', '{}')", [built_at])
    con.close()


SERVED = validation()["built_at"]


def _attempt(built_at, replaced, reason=None):
    return validation() | {
        "built_at": built_at,
        "verdict": "Gated checks failed: source_fidelity." if not replaced else validation()["verdict"],
        "store": {"replaced": replaced, "reason": reason, "kept_previous": not replaced},
    }


def test_a_report_matching_the_served_store_is_its_checks(tmp_path):
    (tmp_path / "validation.json").write_text(json.dumps(validation()), encoding="utf-8")
    (tmp_path / "validation-attempt.json").write_text(json.dumps(validation()), encoding="utf-8")
    page = checks_report(tmp_path / "validation.json", SERVED, tmp_path / "validation-attempt.json")
    assert page["status"] == "current" and len(page["checks"]) == 4
    assert page["attempt"] == {
        "served": True,
        "replaced": True,
        "reason": None,
        "verdict": validation()["verdict"],
    }


def test_a_failed_rebuild_keeps_the_served_report_and_says_the_attempt_failed(tmp_path):
    (tmp_path / "validation.json").write_text(json.dumps(validation()), encoding="utf-8")
    failed = _attempt("2026-11-17 09:00:00 HST", False, "gated checks failed: source_fidelity")
    (tmp_path / "validation-attempt.json").write_text(json.dumps(failed), encoding="utf-8")
    page = checks_report(tmp_path / "validation.json", SERVED, tmp_path / "validation-attempt.json")
    assert page["status"] == "current" and len(page["checks"]) == 4
    assert page["attempt"] == {
        "served": False,
        "replaced": False,
        "reason": "gated checks failed: source_fidelity",
        "verdict": "Gated checks failed: source_fidelity.",
    }


def test_a_report_from_another_build_is_stale_and_shows_no_checks(tmp_path):
    # As an older build left it: the failed attempt's report beside the store it did not replace.
    other = _attempt("2026-11-17 09:00:00 HST", False, "gated checks failed: source_fidelity")
    (tmp_path / "validation.json").write_text(json.dumps(other), encoding="utf-8")
    page = checks_report(tmp_path / "validation.json", SERVED)
    assert page == {"status": "stale", "attempt": None, "checks": [], "eval": {}, "caveats": []}
    # A store that records no build cannot be matched, so its report is shown as unknown, never as current.
    assert checks_report(tmp_path / "validation.json", None)["status"] == "unknown"


def test_checks_endpoint_matches_the_report_to_the_served_store(server):
    port, build = server
    _store_built(build / "klear.duckdb", SERVED)
    (build / "validation.json").write_text(json.dumps(validation()), encoding="utf-8")
    body = json.loads(get(port, "/api/checks")[1])
    assert body["status"] == "current" and body["attempt"] is None
    (build / "validation.json").write_text(
        json.dumps(_attempt("2026-11-17 09:00:00 HST", True)), encoding="utf-8"
    )
    body = json.loads(get(port, "/api/checks")[1])
    assert body["status"] == "stale" and body["checks"] == []


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
    for path in ("/data", "/checks.js"):
        assert get(port, path)[0] == 200, path


def test_checks_section_assets_are_public_and_the_data_page_is_linked_from_the_footer():
    page = (web.STATIC / "data.html").read_text(encoding="utf-8")
    refs = re.findall(r'(?:src|href)="(/[^"#?]*)', page)
    assert {"/checks.js", "/data.js", "/style.css"} <= set(refs) <= set(web.PUBLIC)
    assert '<section class="data-section" id="checks"' in page
    footer = (web.STATIC / "index.html").read_text(encoding="utf-8").split("<footer>")[1]
    assert '<a href="/data">Data &amp; methods</a>' in footer
    script = (web.STATIC / "checks.js").read_text(encoding="utf-8")
    assert "/api/checks" in script and "innerHTML" not in script
    for text in (page, script):
        assert "—" not in text
