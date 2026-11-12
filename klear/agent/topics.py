# Topics KLEAR holds no district figures for, from data/catalog/topics.yaml, and the questions that ask about them.
import re
from dataclasses import dataclass
from functools import cache
from pathlib import Path

import yaml

from klear import config
from klear.etl.places_gazetteer import plain_name

PATH = config.ROOT / "data" / "catalog" / "topics.yaml"
FIELDS = ("topic", "refusal", "match", "source", "where")
# What each refusal tells the reader, as tools and answers say it.
MEANING = {
    "not_available": "inside what KLEAR covers, but KLEAR has no district data for it yet",
    "out_of_scope": "outside what KLEAR covers, which is people, income, housing, health and benefit programs",
}


@dataclass(frozen=True)
class Topic:
    topic: str
    refusal: str
    match: tuple[str, ...]
    source: str
    where: str

    def card(self) -> dict:
        """The topic as tools report it: the refusal to use, what it means and who publishes the figures."""
        return {
            "topic": self.topic,
            "refusal": self.refusal,
            "meaning": MEANING[self.refusal],
            "source": self.source,
            "where": self.where,
        }

    def text(self) -> str:
        """A plain answer for the topic that says which refusal applies and where to look."""
        if self.refusal == "not_available":
            said = f"Figures on {self.topic} are not in KLEAR's district data yet, although KLEAR covers the subject."
        else:
            said = f"Figures on {self.topic} are {MEANING['out_of_scope']}."
        return f"{said} For these figures, see {self.where}."


@cache
def load_topics(path: Path = PATH) -> tuple[Topic, ...]:
    entries = (yaml.safe_load(Path(path).read_text(encoding="utf-8")) or {}).get("topics") or []
    out = []
    for e in entries:
        if not isinstance(e, dict) or set(e) != set(FIELDS):
            raise ValueError(f"topic entry {e!r}: fields must be {FIELDS}")
        if e["refusal"] not in MEANING:
            raise ValueError(f"topic {e['topic']!r}: refusal must be one of {sorted(MEANING)}")
        if (
            not isinstance(e["match"], list)
            or not e["match"]
            or not all(isinstance(m, str) for m in e["match"])
        ):
            raise ValueError(f"topic {e['topic']!r}: match must be a non-empty list of strings")
        out.append(
            Topic(e["topic"], e["refusal"], tuple(plain_name(m) for m in e["match"]), e["source"], e["where"])
        )
    return tuple(out)


def topics_in(text: str, topics: tuple[Topic, ...] | None = None) -> list[Topic]:
    """Catalog topics the text asks about: each has a match entry whose every stem starts a word of the text."""
    words = re.findall(r"[a-z0-9]+", plain_name(text))
    return [
        t
        for t in (load_topics() if topics is None else topics)
        if any(all(any(w.startswith(stem) for w in words) for stem in m.split()) for m in t.match)
    ]


def asked_topic(text: str, topics: tuple[Topic, ...] | None = None) -> Topic | None:
    """The topic a question asks about, or None when it names none or topics that need different refusals."""
    found = topics_in(text, topics)
    return found[0] if found and len({t.refusal for t in found}) == 1 else None
