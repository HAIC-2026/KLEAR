"""Answers to the site's starter questions, prepared ahead for one store build and model."""

import json
import os
import threading
from datetime import datetime
from pathlib import Path

import duckdb

from klear.eval.contract import ContractError, answer_from_json, geo_key
from klear.web.assistant import _payload

# The questions the starter buttons send, with their short labels, in the order of examples in app.js.
QUESTIONS = {
    "Give me a socioeconomic profile of this district.": "Profile",
    "How many kūpuna live in this district?": "Kūpuna",
    "What does the data say about housing needs in this district?": "Housing",
    "How does poverty here compare with the state?": "Poverty vs. state",
    "What support programs could residents of this district explore?": "Programs",
}
NAME = "answer_cache.json"
FIELDS = ("question", "district", "build", "model", "answered_at", "answer")
AGAIN = "uv run python -m klear.web.prewarm"


def cache_path(store: Path) -> Path:
    """The prepared answers file kept next to the store."""
    return store.with_name(NAME)


def build_id(con) -> str | None:
    """The build time the store records, which names its build, or None for a store without one."""
    row = con.execute("SELECT built_at FROM build_info").fetchone()
    return row[0] if row else None


def build_date(build: str) -> str:
    """Reader date of a build, like 'November 11, 2026'."""
    day = datetime.strptime(build[:10], "%Y-%m-%d")
    return f"{day:%B} {day.day}, {day.year}"


def read_entries(path: Path) -> list[dict]:
    """Every well-formed entry in the file; a missing or unreadable file has none."""
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return []
    rows = data.get("answers") if isinstance(data, dict) else None
    out = []
    for row in rows if isinstance(rows, list) else []:
        if not isinstance(row, dict) or set(row) != set(FIELDS):
            continue
        if row["question"] not in QUESTIONS or not all(isinstance(row[k], str) for k in FIELDS[:5]):
            continue
        try:
            answer_from_json(row["answer"])
        except ContractError:
            continue
        out.append(row)
    return out


def write_entries(path: Path, rows: list[dict]) -> None:
    """Replace the file in one step, so a reader never sees half of it."""
    tmp = path.with_name(f"{path.name}.{os.getpid()}.tmp")
    tmp.write_text(json.dumps({"answers": rows}, ensure_ascii=False), encoding="utf-8")
    os.replace(tmp, path)


def _stamp(path: Path) -> tuple | None:
    try:
        st = path.stat()
    except OSError:
        return None
    return st.st_ino, st.st_mtime_ns, st.st_size


def _district(text: str) -> str | None:
    """A House or Senate district as the file keys it, like 'house:022', or None."""
    key = geo_key(text) if isinstance(text, str) else None
    if key is None or key[0] not in ("house", "senate") or not key[1].isdigit():
        return None
    return f"{key[0]}:{int(key[1]):03}"


class AnswerCache:
    """Prepared answers for one store and model, matched to the store's build on every lookup and served from memory."""

    def __init__(self, store: Path, model: str):
        self.store, self.path, self.model = store, cache_path(store), model
        self._lock = threading.Lock()
        self._stamps: tuple | None = None
        self._build: str | None = None
        self._rows: dict[tuple[str, str], dict] = {}
        self._pages: dict[tuple[str, str], dict] = {}
        self._others = 0

    def _refresh(self) -> None:
        stamps = (_stamp(self.store), _stamp(self.path))
        with self._lock:
            if stamps == self._stamps:
                return
        rows = [r for r in read_entries(self.path) if r["model"] == self.model] if all(stamps) else []
        build = None
        try:
            if rows:
                with duckdb.connect(str(self.store), read_only=True) as con:
                    build = build_id(con)
        except duckdb.Error:
            stamps, rows = None, []  # try again on the next lookup
        mine = {(r["question"], r["district"]): r for r in rows if build and r["build"] == build}
        with self._lock:
            self._stamps, self._build, self._rows, self._pages = stamps, build, mine, {}
            self._others = len(rows) - len(mine)

    def _lay_out(self, con, key: tuple[str, str], row: dict, build: str) -> dict | None:
        try:
            if build_id(con) != build:
                return None
            note = f"Prepared ahead of time from the data update of {build_date(build)}."
            page = _payload(con, answer_from_json(row["answer"]), "assistant") | {"prepared": note}
        except Exception:  # noqa: BLE001 - a prepared answer that no longer lays out is answered live
            return None
        with self._lock:
            if self._build == build:
                self._pages[key] = page
        return page

    def get(self, question: str, district: str) -> dict | None:
        """The prepared answer laid out for the page, or None to answer the question live."""
        key = (question, _district(district))
        if question not in QUESTIONS or key[1] is None:
            return None
        self._refresh()
        with self._lock:
            row, page, build = self._rows.get(key), self._pages.get(key), self._build
        if row is None or page is not None:
            return page
        try:
            with duckdb.connect(str(self.store), read_only=True) as con:
                return self._lay_out(con, key, row, build)
        except duckdb.Error:
            return None

    def warm(self) -> int:
        """Lay out every prepared answer ahead, so the first visitor to ask each one gets it from memory too."""
        self._refresh()
        with self._lock:
            todo = [(k, r) for k, r in self._rows.items() if k not in self._pages]
            build = self._build
        if not todo:
            return 0
        try:
            with duckdb.connect(str(self.store), read_only=True) as con:
                return sum(self._lay_out(con, key, row, build) is not None for key, row in todo)
        except duckdb.Error:
            return 0

    def summary(self, live: bool) -> str:
        """Startup line saying how many starter answers are prepared for the store's build."""
        self._refresh()
        with self._lock:
            ready, others = len(self._rows), self._others
        if ready:
            return f"Starter questions: {ready} prepared answers for this data build are served at once."
        if others:
            return f"Prepared answers in {self.path} are from another data build and unused. Refresh: {AGAIN}"
        return f"Starter questions are answered live. To answer them at once, run: {AGAIN}" if live else ""
