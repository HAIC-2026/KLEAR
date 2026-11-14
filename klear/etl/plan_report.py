# Reads each district's official description and permanent-resident population from the 2021 Commission report.
import re
from dataclasses import dataclass, field
from pathlib import Path

import httpx
import pandas as pd
from pypdf import PdfReader

from klear.etl.fetch import fetch
from klear.etl.reapportionment import PAGE, UNITS
from klear.text import hawaiian

SOURCE_ID = "reapportionment_report_2021"
URL = (
    "https://elections.hawaii.gov/wp-content/uploads/"
    "2021-Reapportionment-Commission-Final-Report-952-pages-February-25-2022.pdf"
)
RAW_NAME = "reapportionment/2021-Reapportionment-Commission-Final-Report.pdf"
PAGE_URL = PAGE
NAME = "2021 Reapportionment Commission Final Report and Reapportionment Plan"
PUBLISHER = "Hawaiʻi Office of Elections, 2021 Reapportionment Commission"
VINTAGE = "Final report, February 25, 2022"
LICENSE = "Public record of the State of Hawaiʻi"
# PDF pages (from 1) of Tables A to E and 1 to 10, and of Appendix D, the district boundary descriptions. Only these
# are read: the 900 pages of minutes and maps take minutes to read.
TABLE_PAGES = range(21, 31)
DESCRIPTION_PAGES = range(875, 929)
SEATS = {"house": 51, "senate": 25}
KIND = {"Representative": "house", "Senatorial": "senate"}
TABLE_CHAMBER = {"House": "house", "Senate": "senate"}
BASE_TABLE = {"B": "house", "C": "senate"}

_HEADING = re.compile(r"^\s*(\d+)(st|nd|rd|th) (Representative|Senatorial) District\s*$", re.MULTILINE)
_SECTION_END = re.compile(r"^\s*CONGRESSIONAL DISTRICTS\s*$", re.MULTILINE)
_DESCRIBED = re.compile(r",?\s*more\s+particularly\s+described\s+as\s+follows")
_RUNNING_HEAD = re.compile(r"^\s*(2021 Reapportionment Plans|Metes and Bounds|Page \d+ of \d+)\s*$")
_FOOTER = re.compile(r"^\s*(D-\d{3})\s*$")
_ISLAND_TABLE = re.compile(
    r"^\s*Table (\d+) - (\w+) Basic Island Units? Targets and Deviations \((House|Senate) Districts\)"
)
_STATE_TABLE = re.compile(r"^\s*Table (\d+) - (House|Senate) Statewide Targets and Deviations")
_ROW = re.compile(r"^\s*(\d{1,2})\s+([\d,]+)\s+([\d,]+)\s+(-?\d+)\s+(-?\d+\.\d+)%")
_BASE_HEAD = re.compile(r"Table ([BC])\s*[–-]")
_BASE_ROW = re.compile(rf"^\s*({'|'.join(UNITS)})\s+([\d,]+)\s+(\d+)\s+([\d,]+)\s*$")
_STATE_TOTAL = re.compile(
    r"Table D\b.*?State of Hawaii\s+([\d,]+)\s+(\d+)\s+(\d+)\s+(\d+)\s+([\d,]+)", re.DOTALL
)


def ordinal(n: int) -> str:
    suffix = "th" if 10 <= n % 100 <= 20 else {1: "st", 2: "nd", 3: "rd"}.get(n % 10, "th")
    return f"{n}{suffix}"


def _int(s: str) -> int:
    return int(s.replace(",", ""))


@dataclass
class PlanReport:
    """What the report says: descriptions, the island and statewide population tables, and its totals."""

    descriptions: pd.DataFrame
    island_rows: pd.DataFrame
    state_rows: pd.DataFrame
    bases: pd.DataFrame
    state_total: dict | None
    problems: list[str] = field(default_factory=list)


def read_pages(path: Path, *ranges: range) -> list[dict[int, str]]:
    # the text of each range's pages that the file has, keyed by PDF page number
    reader = PdfReader(str(path))
    n = len(reader.pages)
    return [{p: reader.pages[p - 1].extract_text() or "" for p in pages if p <= n} for pages in ranges]


def _join(text: str) -> str:
    # undo line wraps: a hyphen at a line end joins the two words, any other break is a space
    text = re.sub(r"-[ \t]*\n\s*", "-", text)
    return re.sub(r"\s+", " ", text).strip()


def parse_descriptions(pages: dict[int, str]) -> tuple[pd.DataFrame, list[str]]:
    """Each district's opening sentence in Appendix D, up to where the metes and bounds begin, with the report
    page (its footer, such as D-001) and the PDF page where it starts."""
    text, starts, problems = "", [], []
    for pdf_page in sorted(pages):
        lines = pages[pdf_page].splitlines()
        footer = next((m.group(1) for line in lines if (m := _FOOTER.match(line))), None)
        kept = [line for line in lines if not _RUNNING_HEAD.match(line) and not _FOOTER.match(line)]
        starts.append((len(text), pdf_page, footer))
        text += "\n".join(kept) + "\n"
    end = _SECTION_END.search(text)
    text = text[: end.start()] if end else text
    heads = list(_HEADING.finditer(text))
    rows = []
    for i, m in enumerate(heads):
        n, suffix, kind = int(m.group(1)), m.group(2), m.group(3)
        body = text[m.end() : heads[i + 1].start() if i + 1 < len(heads) else len(text)]
        stop = _DESCRIBED.search(body)
        sentence = _join(body[: stop.start()] if stop else body).rstrip(" .;,")
        opening = f"The {ordinal(n)} {kind} District "
        pdf_page, footer = next((p, f) for at, p, f in reversed(starts) if at <= m.start())
        if f"{n}{suffix}" != ordinal(n) or not sentence.startswith(opening):
            problems.append(
                f"{kind} District {n} on PDF page {pdf_page}: heading or opening words not as expected"
            )
            continue
        rows.append(
            {
                "chamber": KIND[kind],
                "geo_id": f"{n:03d}",
                "description": hawaiian(sentence + "."),
                "page": footer,
                "pdf_page": pdf_page,
            }
        )
    df = pd.DataFrame(rows, columns=["chamber", "geo_id", "description", "page", "pdf_page"])
    return df, problems


def _printed_page(text: str) -> str | None:
    # the report's own page number is the first line that is only a number, under the running head
    for line in text.splitlines()[:4]:
        if re.fullmatch(r"\s*\d+\s*", line):
            return line.strip()
    return None


def parse_tables(pages: dict[int, str]) -> tuple[pd.DataFrame, pd.DataFrame, pd.DataFrame, dict | None]:
    """The district rows of Tables 1 to 8 (by basic island unit) and 9 and 10 (statewide), the unit populations
    and seats of Tables B and C, and the state row of Table D."""
    island, state, bases = [], [], []
    table = base = None
    for pdf_page in sorted(pages):
        text = pages[pdf_page]
        printed = _printed_page(text)
        for line in text.splitlines():
            if m := _ISLAND_TABLE.match(line):
                table = ("island", int(m.group(1)), m.group(2), TABLE_CHAMBER[m.group(3)])
                continue
            if m := _STATE_TABLE.match(line):
                table = ("state", int(m.group(1)), None, TABLE_CHAMBER[m.group(2)])
                continue
            if m := _BASE_HEAD.search(line):
                base, table = BASE_TABLE[m.group(1)], None
                continue
            if line.strip().startswith("Table "):
                table = base = None
                continue
            if base and (m := _BASE_ROW.match(line)):
                bases.append(
                    {
                        "chamber": base,
                        "unit": m.group(1),
                        "base": _int(m.group(2)),
                        "seats": int(m.group(3)),
                        "target": _int(m.group(4)),
                    }
                )
            elif table and (m := _ROW.match(line)):
                kind, number, unit, chamber = table
                row = {
                    "chamber": chamber,
                    "geo_id": f"{int(m.group(1)):03d}",
                    "table": number,
                    "target": _int(m.group(2)),
                    "permanent": _int(m.group(3)),
                    "deviation": int(m.group(4)),
                    "page": printed,
                    "pdf_page": pdf_page,
                }
                (island if kind == "island" else state).append(row | ({"unit": unit} if unit else {}))
    joined = "\n".join(pages[p] for p in sorted(pages))
    total = None
    if m := _STATE_TOTAL.search(joined):
        total = {"base": _int(m.group(1)), "senate": int(m.group(2)), "house": int(m.group(3))}
    cols = ["chamber", "geo_id", "table", "target", "permanent", "deviation", "page", "pdf_page"]
    return (
        pd.DataFrame(island, columns=["unit", *cols]),
        pd.DataFrame(state, columns=cols),
        pd.DataFrame(bases, columns=["chamber", "unit", "base", "seats", "target"]),
        total,
    )


def parse_report(path: Path) -> PlanReport:
    text, tables = read_pages(path, DESCRIPTION_PAGES, TABLE_PAGES)
    descriptions, problems = parse_descriptions(text)
    island, state, bases, total = parse_tables(tables)
    return PlanReport(descriptions, island, state, bases, total, problems)


def _is_pdf(path: Path) -> None:
    with path.open("rb") as f:
        if f.read(5) != b"%PDF-":
            raise ValueError(f"{URL} did not return a PDF")


def load_report(client: httpx.Client | None = None) -> PlanReport:
    return parse_report(fetch(URL, RAW_NAME, client=client, validate=_is_pdf))
