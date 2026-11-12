# Loads the YAML rule files and fetches visible page text for quote checks.
import html
import io
import re
from dataclasses import dataclass, field
from html.parser import HTMLParser
from pathlib import Path

import yaml
from pypdf import PdfReader

from klear.config import ROOT
from klear.etl.fetch import fetch

STATUSES = ("confirmed", "needs_manual")
FPG_API = "https://aspe.hhs.gov/topics/poverty-economic-mobility/poverty-guidelines/api/2026/hi"
# rules checked against structured data instead of page quotes
STRUCTURED = ("fpg_2026_hi", "hud_il_fy2026_hi")
REQUIRED_RULE_IDS = (
    "fpg_2026_hi",
    "hud_il_fy2026_hi",
    "snap_fy2027",
    "wic_2026_27",
    "eitc_federal_ty2025",
    "eitc_federal_ty2026",
    "ssi_federal_2026",
    "preschool_open_doors",
    "h_heap_fy2026",
    "medquest_2026",
    "hi_state_credits",
    "aca_ptc_2026",
    "ssi_hi_supplement",
    "kupuna_care",
    "hpha_limits",
)
REQUIRED = ("rule_id", "program", "status", "effective", "url", "values", "verified_by", "retrieved")
OPTIONAL = ("quote", "note", "fetch_url", "effective_source", "saved")


@dataclass
class Rule:
    rule_id: str
    program: str
    status: str
    effective: str
    url: str
    values: dict
    quote: str | None
    verified_by: str
    retrieved: str
    note: str = field(default="")
    fetch_url: str | None = None
    effective_source: dict | None = None
    # A copy of the page saved by hand in data/manual/, read when the live page blocks scripts.
    saved: str | None = None


def _check_values(name: str, rule_id: str, values: dict) -> None:
    if not values:
        raise ValueError(f"{name}: confirmed rule {rule_id} has no values")
    for key, e in values.items():
        if not isinstance(e, dict) or "value" not in e:
            raise ValueError(f"{name}: value {key!r} needs a value and a quote")
        q = e.get("quote")
        if not isinstance(q, str) or not q.strip():
            raise ValueError(f"{name}: value {key!r} needs a non-empty quote")


def load_rules(path: Path = ROOT / "data" / "rules", *, required=REQUIRED_RULE_IDS) -> list[Rule]:
    path = Path(path)
    if not path.is_dir():
        raise ValueError(f"rules directory {path} does not exist")
    rules: list[Rule] = []
    for f in sorted(path.glob("*.yaml")):
        d = yaml.safe_load(f.read_text(encoding="utf-8")) or {}
        for k in REQUIRED:
            if k not in d:
                raise ValueError(f"{f.name}: missing field {k}")
        if d["status"] not in STATUSES:
            raise ValueError(f"{f.name}: unknown status {d['status']!r}")
        extra = set(d) - set(REQUIRED) - set(OPTIONAL)
        if extra:
            raise ValueError(f"{f.name}: unknown fields {sorted(extra)}")
        if "quote" in d and d["quote"] is not None and not str(d["quote"]).strip():
            raise ValueError(f"{f.name}: quote is empty")
        if d["status"] == "confirmed":
            if d["rule_id"] in STRUCTURED:
                if not d["values"]:
                    raise ValueError(f"{f.name}: confirmed rule {d['rule_id']} has no values")
            else:
                _check_values(f.name, d["rule_id"], d["values"])
        es = d.get("effective_source")
        if es is not None and not all(str(es.get(k, "")).strip() for k in ("url", "quote", "shown")):
            raise ValueError(f"{f.name}: effective_source needs url, quote and shown")
        if any(r.rule_id == d["rule_id"] for r in rules):
            raise ValueError(f"{f.name}: duplicate rule_id {d['rule_id']}")
        rules.append(
            Rule(
                **{k: (str(d[k]) if k in ("effective", "retrieved") else d[k]) for k in REQUIRED},
                quote=d.get("quote"),
                note=d.get("note") or "",
                fetch_url=d.get("fetch_url"),
                effective_source=es,
                saved=d.get("saved"),
            )
        )
    missing = sorted(set(required) - {r.rule_id for r in rules})
    if missing:
        raise ValueError(f"missing required rules {missing}")
    return rules


class _Text(HTMLParser):
    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.parts: list[str] = []
        self.skip = 0

    def handle_starttag(self, tag, attrs):
        if tag in ("script", "style", "noscript"):
            self.skip += 1

    def handle_endtag(self, tag):
        if tag in ("script", "style", "noscript") and self.skip:
            self.skip -= 1

    def handle_data(self, data):
        if not self.skip:
            self.parts.append(data)


def visible_text(raw: str) -> str:
    p = _Text()
    p.feed(raw)
    return re.sub(r"\s+", " ", html.unescape(" ".join(p.parts))).strip()


def document_text(raw: bytes) -> str:
    """Visible text of an HTML page or a PDF, with whitespace collapsed."""
    if raw.startswith(b"%PDF"):
        text = " ".join(page.extract_text() or "" for page in PdfReader(io.BytesIO(raw)).pages)
        # Undo ligatures and drop the dot leaders of form tables, so quotes read as the printed words.
        text = (
            text.replace("\ufb01 ", "fi")
            .replace("\ufb01", "fi")
            .replace("\ufb02 ", "fl")
            .replace("\ufb02", "fl")
        )
        return re.sub(r"\s+", " ", re.sub(r"\.{4,}", " ", text)).strip()
    return visible_text(raw.decode("utf-8", errors="replace"))


def page_text(url: str) -> str:
    # fetch through the cached downloader and return visible text
    name = "rules/" + re.sub(r"[^A-Za-z0-9]+", "_", url.split("://", 1)[-1]).strip("_")[:120]
    return document_text(fetch(url, name).read_bytes())
