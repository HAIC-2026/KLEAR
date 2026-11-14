# Eval agents: the agent protocol, a template stub that proves the scorers, a recording replayer and a script replayer.
import json
import re
import unicodedata
from dataclasses import asdict, dataclass
from pathlib import Path
from types import SimpleNamespace
from typing import Protocol

import yaml

from klear.eval.contract import Answer, FactRef, answer_from_json, answer_to_json, geo_display
from klear.store.db import GEO_TYPES

# Keyword table: question words to the indicators the stub cites.
KEYWORDS = (
    (re.compile(r"k[uū]puna|seniors?|\b65\b", re.IGNORECASE), ("pop_65_plus", "pct_65_plus")),
    (re.compile(r"keiki|children", re.IGNORECASE), ("pop_under_18",)),
)
COMPARE = re.compile(r"compar|versus|\bvs\b|\bthan\b|statewide|\bstate\b", re.IGNORECASE)
FIND = re.compile(r"\b(which|what|find|locate)\b.*\bdistrict|\bdistrict\b.*\b(in|for)\b", re.IGNORECASE)
LEGISLATIVE = re.compile(r"\b(house|senate)\s*(?:district\s*)?#?(\d{1,3})\b", re.IGNORECASE)
ZIP = re.compile(r"(?<!\d)(96\d{3})(?!\d)")
# Sentence templates per indicator: profile uses {g} and {a}, compare adds {b} and {s}.
PROFILE = {
    "pop_65_plus": "{g} has {a} kūpuna.",
    "pct_65_plus": "Kūpuna make up {a} of residents.",
    "pop_under_18": "{g} has {a} keiki.",
}
COMPARE_TPL = {
    "pop_65_plus": "{g} has {a} kūpuna, compared with {b} in {s}.",
    "pct_65_plus": "Kūpuna make up {a} of residents, compared with {b} in {s}.",
    "pop_under_18": "{g} has {a} keiki, compared with {b} in {s}.",
}
GOLDEN_ID = re.compile(r"[A-Za-z0-9_-]+")
CAVEAT = "These are survey estimates and carry sampling error."
# Input tokens a script turn counts as, below the agent's per-call input cap.
SCRIPT_TOKENS = 1


class MissingRecording(FileNotFoundError):
    pass


class RecordingError(MissingRecording):
    pass


@dataclass
class Usage:
    """Tokens of one question: uncached input, output, and input written to or read from the prompt cache."""

    input_tokens: int
    output_tokens: int
    seconds: float
    cache_write_tokens: int = 0
    cache_read_tokens: int = 0


class Agent(Protocol):
    name: str

    def answer(
        self, golden_id: str, question: str, context: tuple[str, str] | None, earlier: tuple = ()
    ) -> tuple[Answer, Usage]: ...


def _refusal(kind: str, text: str) -> Answer:
    return Answer("1", None, kind, [text], [], [], [])


def _geo(geo: tuple[str, str]) -> str:
    return f"{{geo:{geo[0]}:{geo[1]}}}"


def _plain(text: str) -> str:
    text = unicodedata.normalize("NFD", text.replace("ʻ", "").replace("'", "").replace("’", ""))
    return "".join(c for c in text if not unicodedata.combining(c)).casefold()


class StubAgent:
    name = "stub"

    def max_call_cost(self, model, question, prices) -> float:
        return 0.0

    def __init__(self, con):
        self.con = con

    def _exists(self, geo: tuple[str, str]) -> bool:
        return (
            self.con.execute(
                "SELECT 1 FROM geography WHERE geo_type = ? AND geo_id = ?", list(geo)
            ).fetchone()
            is not None
        )

    def _lookup(self, question: str) -> tuple[str, str] | None:
        if m := LEGISLATIVE.search(question):
            geo = (m.group(1).lower(), f"{int(m.group(2)):03d}")
            return geo if self._exists(geo) else None
        if m := ZIP.search(question):
            geo = ("zcta", m.group(1))
            return geo if self._exists(geo) else None
        plain = _plain(question)
        counties = self.con.execute("SELECT geo_id, name FROM geography WHERE geo_type = 'county'").fetchall()
        hits = []
        for geo_id, name in counties:
            place = _plain(geo_display("county", geo_id, name)).removesuffix(" county")
            if re.search(rf"\b{re.escape(place)}\b", plain):
                hits.append((len(place), ("county", geo_id)))
        return max(hits)[1] if hits else None

    def _fact(self, ref: str, indicator: str, geo: tuple[str, str]) -> FactRef | None:
        row = self.con.execute(
            "SELECT f.source_id, f.value, f.value_low, f.value_high, f.moe, s.vintage "
            "FROM fact f JOIN source s USING (source_id) "
            "WHERE f.indicator_id = ? AND f.geo_type = ? AND f.geo_id = ? AND f.value IS NOT NULL "
            "ORDER BY f.source_id LIMIT 1",
            [indicator, *geo],
        ).fetchone()
        if row is None:
            return None
        return FactRef(ref, indicator, geo[0], geo[1], row[0], *row[1:5], row[5])

    def _found(self, geo: tuple[str, str]) -> Answer:
        marker = _geo(geo)
        return Answer("1", geo, "none", [f"{marker} is the district you asked about."], [], [], [])

    def _profile(self, indicators: tuple[str, ...], geo: tuple[str, str], compare: bool) -> Answer | None:
        state = self.con.execute("SELECT geo_id FROM geography WHERE geo_type = 'state'").fetchone()
        facts, sentences = [], []
        for n, indicator in enumerate(indicators):
            own = self._fact(f"f{2 * n + 1}", indicator, geo)
            other = self._fact(f"f{2 * n + 2}", indicator, ("state", state[0])) if compare and state else None
            if own is None or (compare and other is None):
                continue
            facts += [own, other] if compare else [own]
            tpl = (COMPARE_TPL if compare else PROFILE)[indicator]
            sentences.append(
                tpl.format(
                    g=_geo(geo),
                    a=f"{{fact:{own.ref}}}",
                    b=f"{{fact:{other.ref}}}" if other else "",
                    s=_geo(("state", state[0])) if state else "",
                )
            )
        if not facts:
            return None
        sentences.append(f"These are {{vintage:{facts[0].ref}}} estimates.")
        return Answer("1", geo, "none", [" ".join(sentences)], facts, [], [CAVEAT])

    def _answer(self, question: str, context: tuple[str, str] | None) -> Answer:
        none = _refusal("not_available", "I do not have verified data to answer that question.")
        indicators = next((ids for rx, ids in KEYWORDS if rx.search(question)), None)
        found = self._lookup(question)
        if indicators is None:
            return self._found(found) if found and FIND.search(question) else none
        geo = found or context
        if geo is None or not self._exists(geo) or geo[0] not in GEO_TYPES:
            return none
        return self._profile(indicators, geo, bool(COMPARE.search(question))) or none

    def answer(
        self, golden_id: str, question: str, context: tuple[str, str] | None, earlier: tuple = ()
    ) -> tuple[Answer, Usage]:
        return self._answer(question, context), Usage(0, 0, 0.0)


def _path(dir: Path, name: str, golden_id: str) -> Path:
    if not GOLDEN_ID.fullmatch(golden_id):
        raise RecordingError(f"invalid golden id {golden_id!r}: use letters, digits, hyphen, underscore")
    return Path(dir) / name / f"{golden_id}.json"


class RecordedAgent:
    def __init__(self, dir: Path, name: str):
        if not GOLDEN_ID.fullmatch(name):
            raise RecordingError(f"invalid recording name {name!r}: use letters, digits, hyphen, underscore")
        self.dir, self.name = Path(dir), name

    def max_call_cost(self, model, question, prices) -> float:
        return 0.0

    def answer(
        self, golden_id: str, question: str, context: tuple[str, str] | None, earlier: tuple = ()
    ) -> tuple[Answer, Usage]:
        path = _path(self.dir, self.name, golden_id)
        if not path.is_file():
            raise MissingRecording(f"no recording for {golden_id} at {path}")
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
            raw, usage = data["answer"], Usage(**data["usage"])
        except (ValueError, KeyError, TypeError) as e:
            raise RecordingError(f"malformed recording {path}: {e}") from e
        return answer_from_json(raw), usage


def save_recording(
    dir: Path, name: str, golden_id: str, answer: Answer, usage: Usage, trace: list | None = None
) -> Path:
    path = _path(Path(dir), name, golden_id)
    path.parent.mkdir(parents=True, exist_ok=True)
    body = {"answer": answer_to_json(answer), "usage": asdict(usage)}
    if trace is not None:
        body["trace"] = trace
    path.write_text(json.dumps(body, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return path


class ScriptClient:
    """Stand-in for the Anthropic client that returns scripted assistant turns with zero usage."""

    def __init__(self, turns: list[list[dict]], path: Path):
        self.turns, self.path = list(turns), path
        self.messages = self

    def count_tokens(self, **kw) -> SimpleNamespace:
        return SimpleNamespace(input_tokens=SCRIPT_TOKENS)

    def create(self, **kw) -> SimpleNamespace:
        if not self.turns:
            raise RecordingError(f"script {self.path} ran out of turns before the loop finished")
        n = len(self.turns)
        blocks = [
            SimpleNamespace(type="tool_use", id=f"toolu_{n}_{i}", name=b["name"], input=b["input"])
            if b["type"] == "tool_use"
            else SimpleNamespace(type="text", text=b["text"])
            for i, b in enumerate(self.turns.pop(0))
        ]
        stop = "tool_use" if any(b.type == "tool_use" for b in blocks) else "end_turn"
        usage = SimpleNamespace(
            input_tokens=0, output_tokens=0, cache_creation_input_tokens=0, cache_read_input_tokens=0
        )
        return SimpleNamespace(content=blocks, stop_reason=stop, usage=usage)


def _block_problem(b) -> str | None:
    if not isinstance(b, dict):
        return "block must be a mapping"
    if b.get("type") == "text":
        return None if isinstance(b.get("text"), str) else "text block needs a text string"
    if b.get("type") == "tool_use":
        if isinstance(b.get("name"), str) and isinstance(b.get("input"), dict):
            return None
        return "tool_use block needs a name string and an input mapping"
    return f"block type must be text or tool_use, got {b.get('type')!r}"


def load_script(path: Path) -> list[list[dict]]:
    try:
        turns = yaml.safe_load(path.read_text(encoding="utf-8"))
    except yaml.YAMLError as e:
        raise RecordingError(f"malformed script {path}: {e}") from e
    if not isinstance(turns, list) or not turns:
        raise RecordingError(f"malformed script {path}: expected a non-empty list of turns")
    for n, turn in enumerate(turns, 1):
        if not isinstance(turn, list) or not turn:
            raise RecordingError(f"malformed script {path}: turn {n} must be a non-empty list of blocks")
        for b in turn:
            if (problem := _block_problem(b)) is not None:
                raise RecordingError(f"malformed script {path}: turn {n}: {problem}")
    return turns


class ScriptedAgent:
    name = "scripted"

    def __init__(self, con, scripts_dir: Path):
        self.con, self.dir = con, Path(scripts_dir)

    def max_call_cost(self, model, question, prices) -> float:
        return 0.0

    def answer(
        self, golden_id: str, question: str, context: tuple[str, str] | None, earlier: tuple = ()
    ) -> tuple[Answer, Usage]:
        from klear.agent.loop import KlearAgent

        if not GOLDEN_ID.fullmatch(golden_id):
            raise RecordingError(f"invalid golden id {golden_id!r}: use letters, digits, hyphen, underscore")
        path = self.dir / f"{golden_id}.yaml"
        if not path.is_file():
            raise MissingRecording(f"no script for {golden_id} at {path}")
        client = ScriptClient(load_script(path), path)
        out = KlearAgent(self.con, client=client).answer(golden_id, question, context, earlier)
        if client.turns:
            raise RecordingError(f"script {path} has {len(client.turns)} unused turns after the answer")
        return out
