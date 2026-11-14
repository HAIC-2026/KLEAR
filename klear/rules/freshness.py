# Rechecks every rule and program against its live official page and lists what needs a person.
import re
import tempfile
import time
from collections.abc import Callable
from dataclasses import dataclass, replace
from datetime import date, datetime
from pathlib import Path
from urllib.parse import urlsplit

import httpx

from klear.etl.fetch import USER_AGENT
from klear.etl.manual import HST
from klear.programs import STALE_AFTER_DAYS, Program, is_stale
from klear.rules.registry import FPG_API, Rule, document_text
from klear.validation.rules import HUD_URL, check_rules

TIMEOUT_S = 30
# Seconds between two requests to the same site.
PAUSE_S = 1.0
# Days before a rule's review_by date that the check starts asking for new figures.
WARN_DAYS = 30
GONE = {404, 410}
BLOCKED = {401, 403, 429}

CHANGED = "changed"
DUE = "due"
UNCHECKED = "unchecked"
TITLES = {
    CHANGED: "Changed on the official page",
    DUE: "Due for review",
    UNCHECKED: "Could not check, needs a person",
}


class PageError(Exception):
    """A page that could not be read, with the reason in plain words."""

    def __init__(self, url: str, reason: str):
        super().__init__(f"{reason}: {url}")
        self.url, self.reason = url, reason


class Gone(PageError):
    """The page no longer exists."""


class Blocked(PageError):
    """The site refused the script or could not be reached, so a person has to look."""


class Pages:
    """Live pages, each requested at most once per run, with a pause between requests to the same site and no
    retries."""

    def __init__(self, client: httpx.Client | None = None, pause: float = PAUSE_S, sleep=time.sleep):
        self.client = client or httpx.Client(follow_redirects=True, timeout=TIMEOUT_S)
        self.pause, self.sleep = pause, sleep
        self.got: dict[str, bytes | PageError] = {}
        self.last: dict[str, float] = {}

    def raw(self, url: str) -> bytes:
        if url not in self.got:
            self.got[url] = self._get(url)
        got = self.got[url]
        if isinstance(got, PageError):
            raise got
        return got

    def text(self, url: str) -> str:
        return document_text(self.raw(url))

    def _get(self, url: str) -> bytes | PageError:
        host = urlsplit(url).hostname or ""
        wait = self.last.get(host, float("-inf")) + self.pause - time.monotonic()
        if wait > 0:
            self.sleep(wait)
        try:
            resp = self.client.get(url, headers={"User-Agent": USER_AGENT})
        except httpx.TimeoutException:
            return Blocked(url, f"the page did not answer within {TIMEOUT_S} seconds")
        except httpx.HTTPError as e:
            return Blocked(url, f"the site could not be reached ({type(e).__name__})")
        finally:
            self.last[host] = time.monotonic()
        code = resp.status_code
        if code in GONE:
            return Gone(url, f"the page is gone (HTTP {code})")
        if code in BLOCKED:
            return Blocked(url, f"the site blocks scripts (HTTP {code})")
        if code >= 400:
            return Blocked(url, f"the site answered with an error (HTTP {code})")
        if code != 200 or not resp.content:
            return Blocked(url, f"the site sent no page to the script (HTTP {code})")
        return resp.content


@dataclass(frozen=True)
class Finding:
    """One item for a person: what it is, what is wrong, what was expected and the page to look at."""

    group: str
    item: str
    problem: str
    expected: str = ""
    url: str = ""


def say_date(d: date) -> str:
    return f"{d:%B} {d.day}, {d.year}"


def phone_on_page(phone: str, text: str) -> bool:
    """True when the page shows the number in any usual layout, like (808) 524-3370 or 808.524.3370."""
    digits = "".join(c for c in phone if c.isdigit())[-10:]
    sep = r"[\s.\-\u2010-\u2015\u00a0]*"
    area, exchange, line = digits[:3], digits[3:6], digits[6:]
    pattern = rf"(?<!\d)(?:1{sep})?\(?{area}\)?{sep}{exchange}{sep}{line}(?!\d)"
    return re.search(pattern, text) is not None


def due_rule(rule: Rule, today: date) -> list[Finding]:
    due = date.fromisoformat(rule.review_by)
    left = (due - today).days
    if left > WARN_DAYS:
        return []
    when = f"were due on {say_date(due)}" if left < 0 else f"are due by {say_date(due)}"
    return [Finding(DUE, f"Rule {rule.rule_id}", f"new figures {when}", url=rule.source_url)]


def _rule_detail(rule: Rule, d: dict, failure: PageError | None) -> Finding:
    item, url = f"Rule {rule.rule_id}", rule.fetch_url or rule.source_url
    check = d["check"]
    if check == "fetch":
        if isinstance(failure, Blocked):
            return Finding(UNCHECKED, item, failure.reason, url=failure.url)
        if isinstance(failure, Gone):
            return Finding(CHANGED, item, failure.reason, url=failure.url)
        return Finding(CHANGED, item, f"the page could not be read ({d['problem']})", url=url)
    if check == "quote":
        return Finding(
            CHANGED, item, f"the quote for {d['key']} is no longer on the page", d["quote"], d["url"]
        )
    if check == "fpg":
        return Finding(
            CHANGED,
            item,
            f"the guideline for a household of {d['size']} is now {d['api']}",
            str(d["yaml"]),
            f"{FPG_API}/{d['size']}",
        )
    if check == "hud":
        return Finding(
            CHANGED, item, f"{d['county']} {d['column']} is now {d['xlsx']}", str(d["yaml"]), HUD_URL
        )
    if check == "value_in_quote":
        return Finding(CHANGED, item, f"the value for {d['key']} is not in its quote", d["shown"], url)
    return Finding(CHANGED, item, f"the rule file does not agree with itself ({check})", url=url)


def check_rule(rule: Rule, pages: Pages, today: date) -> list[Finding]:
    """Due dates and the rule's values against its live pages, never a saved copy."""
    out = due_rule(rule, today)
    item = f"Rule {rule.rule_id}"
    if rule.status == "needs_manual":
        return [
            *out,
            Finding(UNCHECKED, item, f"marked for a manual check ({rule.note or 'no reason given'})"),
        ]
    failures: list[PageError] = []

    def fetch_text(url: str) -> str:
        try:
            return pages.text(url)
        except PageError as e:
            failures.append(e)
            raise

    with tempfile.TemporaryDirectory() as tmp:
        hud = None
        if rule.rule_id == "hud_il_fy2026_hi":
            try:
                raw = pages.raw(HUD_URL)
                if not raw.startswith(b"PK"):
                    raise Blocked(HUD_URL, "the site sent a web page instead of the workbook")
                hud = Path(tmp) / "hud.xlsx"
                hud.write_bytes(raw)
            except PageError as e:
                group = UNCHECKED if isinstance(e, Blocked) else CHANGED
                return [*out, Finding(group, item, e.reason, url=e.url)]
        res = check_rules([replace(rule, saved=None)], fetch_text=fetch_text, hud_file=hud)
    failure = failures[-1] if failures else None
    return out + [_rule_detail(rule, d, failure) for d in res.details]


def _reach(item: str, url: str, read: Callable[[str], object]) -> tuple[object, list[Finding]]:
    try:
        return read(url), []
    except Blocked as e:
        return None, [Finding(UNCHECKED, item, e.reason, url=url)]
    except Gone as e:
        return None, [Finding(CHANGED, item, e.reason, url=url)]


def check_program(p: Program, pages: Pages, today: date) -> list[Finding]:
    """The program page still answers, the phone still appears on its source and a person checked it lately."""
    item = f"Program {p.name} ({p.program_id})"
    out = []
    if is_stale(p, today):
        out.append(
            Finding(
                DUE,
                item,
                f"a person last checked these details on {say_date(p.verified)}, "
                f"more than {STALE_AFTER_DAYS} days ago",
                url=p.url,
            )
        )
    out += _reach(item, p.url, pages.raw)[1]
    text, problems = _reach(item, p.phone_source, pages.text)
    out += problems
    if text is not None and not phone_on_page(p.phone, text):
        out.append(
            Finding(CHANGED, item, "the phone number is no longer on its page", p.phone, p.phone_source)
        )
    return out


def run(rules: list[Rule], programs: list[Program], pages: Pages, today: date) -> list[Finding]:
    found = [f for r in rules for f in check_rule(r, pages, today)]
    return found + [f for p in programs for f in check_program(p, pages, today)]


def failed(found: list[Finding]) -> bool:
    """A change or a due date fails the run; pages that could not be checked only ask for a person."""
    return any(f.group in (CHANGED, DUE) for f in found)


def _line(f: Finding) -> str:
    out = f"{f.item}: {f.problem}."
    if f.expected:
        out += f' Expected "{f.expected}".'
    if f.url:
        out += f" Page: {f.url}"
    return out


def summary(found: list[Finding], n_rules: int, n_programs: int, today: date) -> str:
    counts = {g: sum(f.group == g for f in found) for g in TITLES}
    return (
        f"Checked {n_rules} rules and {n_programs} programs against their official pages on {say_date(today)} "
        f"(HST): {counts[CHANGED]} changed, {counts[DUE]} due for review, "
        f"{counts[UNCHECKED]} could not be checked."
    )


def text_report(found: list[Finding], head: str) -> str:
    lines = [head]
    for group, title in TITLES.items():
        items = [f for f in found if f.group == group]
        if items:
            lines += ["", f"{title}:", *(f"- {_line(f)}" for f in items)]
    return "\n".join(lines) + "\n"


def issue_report(found: list[Finding], head: str) -> str:
    """The body of the issue a failed run opens, in Markdown."""
    lines = [head, ""]
    if failed(found):
        lines.append(
            "Update each item below by following docs/KEEPING-CURRENT.md, then run the check again by hand."
        )
    for group, title in TITLES.items():
        items = [f for f in found if f.group == group]
        if not items:
            continue
        lines += ["", f"## {title}", ""]
        if group == UNCHECKED:
            lines += [
                "These sites refuse scripts or did not answer. Open each page in a browser and compare it.",
                "",
            ]
        lines += [f"- {_line(f)}" for f in items]
    return "\n".join(lines) + "\n"


def today_hst() -> date:
    return datetime.now(HST).date()
