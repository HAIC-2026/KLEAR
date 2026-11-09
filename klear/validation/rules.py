# Validation 10: every value of a confirmed rule is anchored to a quote on its official page and structured rules match their source (gated).
import json
import re
from collections.abc import Callable
from datetime import date

import openpyxl

from klear.etl.manual import ManualInputMissing, manual_file
from klear.rules.registry import FPG_API, Rule
from klear.validation import CheckResult

HUD_FILE = "Section8-FY26.xlsx"
HUD_URL = "https://www.huduser.gov/portal/datasets/il/il26/Section8-FY26.xlsx"
HUD_COUNTIES = ("Honolulu", "Hawaii", "Maui", "Kauai")
HUD_COLUMNS = (
    ("median2026",)
    + tuple(f"eli_{i}" for i in range(1, 9))
    + tuple(f"l50_{i}" for i in range(1, 9))
    + tuple(f"l80_{i}" for i in range(1, 9))
)


def _norm(s: str) -> str:
    return re.sub(r"\s+", " ", s).strip()


def shown(value, override: str | None = None) -> str:
    # the value as a page prints it, for example 1655 -> 1,655
    if override is not None:
        return str(override)
    if isinstance(value, int) and not isinstance(value, bool):
        return f"{value:,}"
    return str(value)


def in_quote(quote: str, text: str) -> bool:
    # numbers must match whole tokens so 1,655 is not found inside 11,655
    if re.fullmatch(r"[\d,.]+", text):
        return re.search(rf"(?<![\d,.]){re.escape(text)}(?![\d]|[,.]\d)", quote) is not None
    return text in quote


MONTHS = ("jan", "feb", "mar", "apr", "may", "jun", "jul", "aug", "sep", "oct", "nov", "dec")


def shown_matches(effective: str, shown: str) -> bool:
    # the printed date must agree with the ISO effective date on every part it states
    eff = date.fromisoformat(effective)
    year = re.search(r"\b(20\d\d)\b", shown)
    if not year or int(year.group(1)) != eff.year:
        return False
    month = re.search(r"\b(" + "|".join(MONTHS) + r")[a-z]*\b", shown.lower())
    if month and MONTHS.index(month.group(1)) + 1 != eff.month:
        return False
    day = re.search(r"\b(\d{1,2})\b", shown[: year.start()])
    return not (day and int(day.group(1)) != eff.day)


NUMBER = re.compile(r"\d[\d,]*(?:\.\d+)?")


def shown_is_value(value, shown: str) -> bool:
    # a shown override must state the stored value: the same number, or the same ISO date
    if isinstance(value, date):
        value = value.isoformat()
    if isinstance(value, str) and re.fullmatch(r"\d{4}-\d{2}-\d{2}", value):
        return shown_matches(value, shown)
    if isinstance(value, int | float) and not isinstance(value, bool):
        nums = NUMBER.findall(shown)
        return len(nums) == 1 and float(nums[0].replace(",", "")) == float(value)
    return str(value) in shown


def _check_fpg(rule: Rule, fetch_text, details: list[dict]) -> int:
    n = 0
    for size in range(1, 9):
        got = int(json.loads(fetch_text(f"{FPG_API}/{size}"))["data"]["income"])
        n += 1
        want = rule.values.get(size, rule.values.get(str(size)))
        if got != want:
            details.append({"rule_id": rule.rule_id, "check": "fpg", "size": size, "yaml": want, "api": got})
    return n


def _check_hud(rule: Rule, details: list[dict]) -> int:
    # A read-only workbook keeps its file open until closed, which blocks deleting it on Windows.
    wb = openpyxl.load_workbook(manual_file(HUD_FILE, HUD_URL), read_only=True, data_only=True)
    try:
        rows = wb.active.iter_rows(values_only=True)
        header = [str(h).strip().lower() if h is not None else "" for h in next(rows)]
        ci = {h: i for i, h in enumerate(header)}
        table = [r for r in rows if str(r[ci["state_alpha"]]).strip() == "HI"]
    finally:
        wb.close()
    n = 0
    for county in HUD_COUNTIES:
        row = next((r for r in table if str(r[ci["county_name"]]).startswith(county)), None)
        cols = rule.values.get(county) or {}
        for col in HUD_COLUMNS:
            want = cols.get(col)
            got = row[ci[col]] if row is not None and col in ci else None
            n += 1
            if want is None or got != want:
                details.append(
                    {"rule_id": rule.rule_id, "check": "hud", "county": county, "column": col, "yaml": want, "xlsx": got}
                )  # fmt: skip
    return n


def _check_anchor(
    rule: Rule, key: str, quote: str, text: str, want: str, pages: dict, url: str, details
) -> int:
    page = pages.get(url)
    if page is None:
        page = pages[url] = _norm(text)
    if _norm(quote) not in page:
        details.append({"rule_id": rule.rule_id, "check": "quote", "key": key, "url": url, "quote": quote})
    elif not in_quote(_norm(quote), want):
        details.append({"rule_id": rule.rule_id, "check": "value_in_quote", "key": key, "quote": quote, "shown": want})  # fmt: skip
    return 1


def check_rules(rules: list[Rule], *, fetch_text: Callable[[str], str]) -> CheckResult:
    details: list[dict] = []
    notes: list[str] = []
    missing: ManualInputMissing | None = None
    n = 0
    pages: dict[str, str] = {}
    for r in rules:
        if r.status == "needs_manual":
            notes.append(f"{r.rule_id}: needs a manual check, not compared ({r.note or 'no reason given'})")
            continue
        try:
            es = r.effective_source
            if es:
                shown_es = str(es.get("shown", ""))
                if not shown_matches(r.effective, shown_es):
                    details.append(
                        {"rule_id": r.rule_id, "check": "effective_date", "effective": r.effective, "shown": shown_es}
                    )  # fmt: skip
                else:
                    eurl = es["url"]
                    n += _check_anchor(r, "effective", es["quote"], fetch_text(eurl), shown_es, pages, eurl, details)  # fmt: skip
            if r.rule_id == "fpg_2026_hi":
                n += _check_fpg(r, fetch_text, details)
            elif r.rule_id == "hud_il_fy2026_hi":
                n += _check_hud(r, details)
            else:
                url = r.fetch_url or r.url
                text = fetch_text(url)
                for key, e in r.values.items():
                    if e.get("shown") is not None and not shown_is_value(e["value"], str(e["shown"])):
                        details.append(
                            {"rule_id": r.rule_id, "check": "shown_value", "key": key, "value": e["value"], "shown": e["shown"]}
                        )  # fmt: skip
                        n += 1
                        continue
                    want = shown(e["value"], e.get("shown"))
                    n += _check_anchor(r, key, e["quote"], text, want, pages, url, details)
        except ManualInputMissing as e:
            missing = e
            notes.append(f"{r.rule_id}: skipped, {e}")
        except Exception as e:  # noqa: BLE001 - any fetch or parse error fails that rule
            problem = f"{type(e).__name__}: {e}"
            details.append({"rule_id": r.rule_id, "check": "fetch", "problem": problem})
    if details:
        return CheckResult("rules", False, True, n, len(details), details, notes)
    if missing is not None:
        raise missing
    return CheckResult("rules", True, True, n, 0, details, notes)
