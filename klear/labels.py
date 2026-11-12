"""Short labels and plain definitions of indicators, from data/catalog/indicators.yaml, for the website.

The store keeps each indicator's official label and universe, which the agent reads and the eval scores against
(klear/eval/contract.py). Readers see the short label, with its plain definition, the official label and the
universe one tap away.
"""

import functools
from dataclasses import dataclass
from pathlib import Path

import yaml

from klear.config import ROOT

CATALOG = ROOT / "data" / "catalog" / "indicators.yaml"
# ACS entries carry their reader text beside the official label; other sources' entries carry only reader text.
SECTIONS = ("indicators", "other_sources")
MAX_SHORT = 45
# Names that keep their capital when a short label falls mid-sentence. Words in capitals, like SNAP, keep theirs too.
PROPER = {"Native", "Filipino", "Asian", "White", "Hispanic"}


@dataclass(frozen=True)
class Plain:
    short: str
    plain: str


@functools.cache
def plain_labels(path: Path | None = None) -> dict[str, Plain]:
    """Reader text of every indicator in the catalog, by indicator id."""
    data = yaml.safe_load(Path(path or CATALOG).read_text(encoding="utf-8"))
    out: dict[str, Plain] = {}
    for section in SECTIONS:
        for e in data.get(section) or []:
            where = f"{section} entry {e.get('indicator_id')}"
            short, plain = str(e.get("short_label") or "").strip(), str(e.get("plain") or "").strip()
            if not short or not plain:
                raise ValueError(f"{where}: needs short_label and plain")
            if len(short) > MAX_SHORT:
                raise ValueError(f"{where}: short_label runs {len(short)} characters, over {MAX_SHORT}")
            if e["indicator_id"] in out:
                raise ValueError(f"{where}: duplicate indicator_id")
            out[e["indicator_id"]] = Plain(short, plain)
    return out


def short_label(indicator_id: str, official: str) -> str:
    """The label readers see, or the official one for an indicator the catalog gives no reader text."""
    found = plain_labels().get(indicator_id)
    return found.short if found else official


def in_sentence(label: str) -> str:
    """A short label as it reads mid-sentence: lower case, unless it starts with a name or a word in capitals."""
    first = label.split(" ", 1)[0]
    if first in PROPER or first[1:2].isupper():
        return label
    return label[:1].lower() + label[1:]


def meaning(indicator_id: str, official: str, universe: str | None) -> dict:
    """What a short label stands for: its plain definition, the official label and who the figure counts."""
    found = plain_labels().get(indicator_id)
    return {"plain": found.plain if found else "", "official": official, "universe": universe or ""}
