# Tests for the live freshness check, run against saved pages through a fake transport.
import io
import json
from dataclasses import replace
from datetime import date

import httpx
import openpyxl
import pytest

from klear.etl.fetch import USER_AGENT
from klear.programs import Program, catalog
from klear.rules import __main__ as cli
from klear.rules.freshness import (
    CHANGED,
    DUE,
    UNCHECKED,
    Finding,
    Pages,
    check_program,
    check_rule,
    failed,
    issue_report,
    phone_on_page,
    run,
)
from klear.rules.registry import FPG_API, Rule
from klear.validation.rules import HUD_COLUMNS, HUD_COUNTIES, HUD_URL

TODAY = date(2026, 11, 13)
SNAP_PAGE = "<html><body><h1>SNAP</h1><table><tr><td>4 $1,023 $1,655</td></tr></table></body></html>"
PHONE_PAGE = "<html><body><p>SNAP help line: 808-643-1643</p></body></html>"


def snap_rule(**over) -> Rule:
    r = Rule(
        rule_id="snap_test",
        program="SNAP",
        status="confirmed",
        effective="2026-10-01",
        source_url="https://example.org/snap",
        values={"max_hh4": {"value": 1655, "quote": "4 $1,023 $1,655"}},
        quote=None,
        verified_by="read from page",
        retrieved="2026-10-30",
        info_url="https://example.org/apply",
        review_by="2027-10-01",
    )
    return replace(r, **over)


def program(**over) -> Program:
    p = Program(
        program_id="snap",
        name="SNAP",
        description="Food money.",
        serves="People with low income.",
        agency="DHS",
        url="https://example.gov/snap",
        phone="(808) 643-1643",
        phone_source="https://example.gov/phones",
        rules=("snap_test",),
        needs=("pct_hh_snap",),
        verified=date(2026, 11, 9),
        verification="read from page",
    )
    return replace(p, **over)


def site(pages: dict, seen: list | None = None) -> Pages:
    """Pages served from a dict of url to body, or to a status code."""

    def handler(request: httpx.Request) -> httpx.Response:
        url = str(request.url)
        if seen is not None:
            seen.append((url, request.headers["User-Agent"]))
        body = pages.get(url, 404)
        if isinstance(body, int):
            return httpx.Response(body)
        if isinstance(body, Exception):
            raise body
        return httpx.Response(200, content=body if isinstance(body, bytes) else body.encode())

    return Pages(httpx.Client(transport=httpx.MockTransport(handler)), pause=0)


def good_site(**over) -> dict:
    return {
        "https://example.org/snap": SNAP_PAGE,
        "https://example.gov/snap": "<p>Apply for SNAP</p>",
        "https://example.gov/phones": PHONE_PAGE,
    } | over


def test_unchanged_pages_pass():
    found = run([snap_rule()], [program()], site(good_site()), TODAY)
    assert found == [] and not failed(found)


def test_changed_quote_fails_and_names_the_rule():
    page = SNAP_PAGE.replace("$1,655", "$1,702")
    found = run([snap_rule()], [program()], site(good_site(**{"https://example.org/snap": page})), TODAY)
    assert failed(found)
    (f,) = found
    assert (f.group, f.item, f.expected, f.url) == (
        CHANGED,
        "Rule snap_test",
        "4 $1,023 $1,655",
        "https://example.org/snap",
    )
    assert "max_hh4" in f.problem
    body = issue_report(found, "head")
    assert "Rule snap_test" in body and "## Changed on the official page" in body and "`" not in body


def test_blocked_page_needs_a_person_and_does_not_fail():
    pages = site(good_site(**{"https://example.org/snap": 403, "https://example.gov/snap": 403}))
    found = run([snap_rule(saved="snap.html")], [program()], pages, TODAY)
    assert [f.group for f in found] == [UNCHECKED, UNCHECKED] and not failed(found)
    assert all("blocks scripts (HTTP 403)" in f.problem for f in found)
    assert "Open each page in a browser" in issue_report(found, "head")


def test_unreachable_and_empty_answers_need_a_person():
    pages = site(
        good_site(
            **{
                "https://example.org/snap": httpx.ConnectTimeout("slow"),
                "https://example.gov/snap": 202,
                "https://example.gov/phones": 503,
            }
        )
    )
    found = run([snap_rule()], [program()], pages, TODAY)
    assert [f.group for f in found] == [UNCHECKED] * 3 and not failed(found)
    assert "did not answer within 30 seconds" in found[0].problem
    assert "HTTP 202" in found[1].problem and "HTTP 503" in found[2].problem


def test_missing_page_is_a_change():
    found = check_program(program(), site(good_site(**{"https://example.gov/snap": 404})), TODAY)
    assert [(f.group, f.problem) for f in found] == [(CHANGED, "the page is gone (HTTP 404)")]


def test_phone_must_still_appear():
    pages = site(good_site(**{"https://example.gov/phones": "<p>Call 808-643-1600</p>"}))
    (f,) = check_program(program(), pages, TODAY)
    assert f.group == CHANGED and f.expected == "(808) 643-1643" and f.url == "https://example.gov/phones"


@pytest.mark.parametrize(
    "text",
    [
        "(808) 643-1643",
        "808-643-1643",
        "808.643.1643",
        "1-808-643-1643",
        "808\u2011643\u20111643",
        "808 643 1643",
    ],
)
def test_phone_layouts(text):
    assert phone_on_page("(808) 643-1643", f"Call {text} today")


def test_phone_is_not_found_inside_a_longer_number():
    assert not phone_on_page("(808) 643-1643", "Fax 808-643-16430")
    assert phone_on_page("1-800-316-8005", "toll free 1 (800) 316-8005")


def test_review_by_warns_thirty_days_ahead():
    assert check_rule(snap_rule(review_by="2026-12-14"), site(good_site()), TODAY) == []
    (soon,) = check_rule(snap_rule(review_by="2026-12-13"), site(good_site()), TODAY)
    assert soon.group == DUE and soon.problem == "new figures are due by December 13, 2026"
    (late,) = check_rule(snap_rule(review_by="2026-11-01"), site(good_site()), TODAY)
    assert late.problem == "new figures were due on November 1, 2026" and failed([late])


def test_program_checked_by_a_person_over_ninety_days_ago_is_due():
    assert check_program(program(verified=date(2026, 8, 15)), site(good_site()), TODAY) == []
    (f,) = check_program(program(verified=date(2026, 8, 14)), site(good_site()), TODAY)
    assert f.group == DUE and "August 14, 2026" in f.problem


def test_each_page_is_requested_once_with_the_project_user_agent():
    seen = []
    rules = [snap_rule(), snap_rule(rule_id="snap_other")]
    run(rules, [program(), program(program_id="snap2")], site(good_site(), seen), TODAY)
    urls = [u for u, _ in seen]
    assert sorted(urls) == sorted(set(urls)) and len(urls) == 3
    assert {a for _, a in seen} == {USER_AGENT} and "KLEAR" in USER_AGENT


def test_pause_between_requests_to_the_same_site():
    waits = []
    pages = Pages(
        httpx.Client(transport=httpx.MockTransport(lambda r: httpx.Response(200, content=b"ok"))),
        pause=5,
        sleep=waits.append,
    )
    pages.raw("https://a.example/1")
    pages.raw("https://b.example/1")
    pages.raw("https://a.example/2")
    assert len(waits) == 1 and 0 < waits[0] <= 5


def test_poverty_guidelines_are_compared_with_the_api():
    values = {1: 18360, 2: 24860}
    rule = snap_rule(rule_id="fpg_2026_hi", source_url=FPG_API, values=values)
    api = {f"{FPG_API}/{n}": json.dumps({"data": {"income": values.get(n, 0)}}) for n in range(1, 9)}
    api[f"{FPG_API}/2"] = json.dumps({"data": {"income": 25000}})
    found = check_rule(rule, site(api), TODAY)
    changed = [f for f in found if f.url == f"{FPG_API}/2"]
    assert (
        changed[0].problem == "the guideline for a household of 2 is now 25000"
        and changed[0].expected == "24860"
    )


def _hud_book() -> bytes:
    wb = openpyxl.Workbook()
    ws = wb.active
    ws.append(["stusps", "county_name", *HUD_COLUMNS])
    for c in HUD_COUNTIES:
        ws.append(["HI", f"{c} County", 150000] + [1] * (len(HUD_COLUMNS) - 1))
    buf = io.BytesIO()
    wb.save(buf)
    return buf.getvalue()


def test_hud_workbook_is_fetched_live_and_a_web_page_needs_a_person():
    cols = {"median2026": 150000} | {c: 1 for c in HUD_COLUMNS[1:]}
    rule = snap_rule(
        rule_id="hud_il_fy2026_hi", source_url=HUD_URL, values={c: dict(cols) for c in HUD_COUNTIES}
    )
    assert check_rule(rule, site({HUD_URL: _hud_book()}), TODAY) == []
    (f,) = check_rule(rule, site({HUD_URL: "<html>checking your browser</html>"}), TODAY)
    assert f.group == UNCHECKED and "web page instead of the workbook" in f.problem


def test_needs_manual_rule_is_listed_for_a_person():
    (f,) = check_rule(snap_rule(status="needs_manual", note="no page"), site({}), TODAY)
    assert f.group == UNCHECKED and "no page" in f.problem


def test_command_prints_the_list_writes_the_issue_and_exits_on_change(tmp_path, monkeypatch, capsys):
    found = [
        Finding(CHANGED, "Rule snap_test", "the quote is gone", "4 $1,655", "https://example.org/snap"),
        Finding(UNCHECKED, "Program Med-QUEST (med_quest)", "the site blocks scripts (HTTP 403)"),
    ]
    monkeypatch.setattr(cli, "run", lambda *a: found)
    monkeypatch.setattr(cli, "today_hst", lambda: TODAY)
    report = tmp_path / "report.md"
    assert cli.main(["check", "--report", str(report)]) == 1
    out = capsys.readouterr().out
    assert (
        out.startswith(f"Checked 15 rules and {len(catalog())} programs")
        and "1 changed, 0 due for review, 1 could" in out
    )
    assert '- Rule snap_test: the quote is gone. Expected "4 $1,655". Page: https://example.org/snap' in out
    assert "Program Med-QUEST" in report.read_text(encoding="utf-8")
    monkeypatch.setattr(cli, "run", lambda *a: found[1:])
    assert cli.main(["check"]) == 0


def test_the_check_never_reads_a_saved_copy(monkeypatch):
    def no_manual(*a, **k):
        raise AssertionError("read a saved copy")

    monkeypatch.setattr("klear.validation.rules.manual_file", no_manual)
    pages = site({"https://example.org/snap": 403})
    (f,) = check_rule(snap_rule(saved="snap.html"), pages, TODAY)
    assert f.group == UNCHECKED
