"""Answers website questions with the KLEAR agent under a daily spend cap, falling back to the free lookup."""

import json
import os
import uuid
from contextlib import contextmanager
from datetime import UTC, datetime, timedelta
from pathlib import Path

from klear.agent.config import DEFAULT_MODEL
from klear.agent.earlier import INDICATORS_MAX
from klear.config import MissingSecretError
from klear.eval.contract import VERSION, Answer, fill_value, geo_display, render
from klear.eval.spend import TOKEN_FIELDS, load_prices, request_cost
from klear.filelock import exclusive
from klear.web import display
from klear.web.answers import ask as lookup
from klear.web.answers import asks_profile, place_of

BUDGET = Path.home() / ".config" / "klear" / "web_spend.json"
DAILY_CAP = 5.0
HST = timedelta(hours=-10)
BASIC = "The full assistant is not available right now, so this is a basic lookup."


class BudgetExceeded(RuntimeError):
    """The day's website spend cap would be crossed."""


def today() -> str:
    """Current date in HST."""
    return (datetime.now(UTC) + HST).date().isoformat()


class DailyBudget:
    """Spend and tokens for the current HST day in one locked JSON file; open holds count until settled or the day
    ends."""

    def __init__(self, cap: float = DAILY_CAP, path: Path = BUDGET, day=today):
        self.cap, self.path, self.day = cap, path, day

    @contextmanager
    def _state(self):
        self.path.parent.mkdir(parents=True, exist_ok=True)
        lock = self.path.with_name(self.path.name + ".lock")
        with open(lock, "ab") as fd, exclusive(fd.fileno()):
            state = json.loads(self.path.read_text(encoding="utf-8")) if self.path.exists() else {}
            if state.get("day") != self.day():
                state = {"day": self.day(), "spent": 0.0, "held": {}}
            yield state
            tmp = self.path.with_name(f"{self.path.name}.{os.getpid()}.tmp")
            tmp.write_text(json.dumps(state), encoding="utf-8")
            os.replace(tmp, self.path)

    def reserve(self, amount: float) -> str:
        with self._state() as s:
            if s["spent"] + sum(s["held"].values()) + amount > self.cap:
                raise BudgetExceeded(f"daily cap of ${self.cap:.2f} reached")
            rid = uuid.uuid4().hex
            s["held"][rid] = amount
            return rid

    def settle(self, rid: str, cost: float | None, usage=None) -> None:
        """Replace a hold with its actual cost, or with the full hold when the cost is unknown, and add the
        question's input, output, cache write and cache read tokens to the day's totals."""
        with self._state() as s:
            # A hold from a previous day is already gone, so its cost is not added to today.
            held = s["held"].pop(rid, None)
            if held is not None:
                s["spent"] += held if cost is None else cost
                if cost is not None and usage is not None:
                    tokens = s.setdefault("tokens", dict.fromkeys(TOKEN_FIELDS, 0))
                    for name in TOKEN_FIELDS:
                        tokens[name] = tokens.get(name, 0) + getattr(usage, name, 0)

    def tokens(self) -> dict[str, int]:
        """The day's settled input, output, cache write and cache read tokens."""
        with self._state() as s:
            return dict.fromkeys(TOKEN_FIELDS, 0) | s.get("tokens", {})

    def spent(self) -> float:
        with self._state() as s:
            return s["spent"] + sum(s["held"].values())


def _named(con, geo: tuple[str, str]) -> str | None:
    row = con.execute("SELECT name FROM geography WHERE geo_type = ? AND geo_id = ?", list(geo)).fetchone()
    return geo_display(*geo, row[0]) if row else None


def answered_district(con, answered: tuple[str, str] | None, selected: tuple[str, str] | None) -> dict:
    """The House or Senate district an answer is about when it is not the selected one, or when none is
    selected, as {"district": ...}."""
    if answered is None or answered == selected or answered[0] not in ("house", "senate"):
        return {}
    name = _named(con, answered)
    return {"district": {"geo": ":".join(answered), "name": name}} if name else {}


def subject(con, place: tuple[str, str] | None, refs) -> dict:
    """What an answer is about, which the page sends back with a follow-up question: its place and the indicator
    ids and places of its figures. Never its text."""
    ids = list(dict.fromkeys(r.indicator_id for r in refs))[:INDICATORS_MAX]
    name = _named(con, place) if place else None
    if not ids and not name:
        return {}
    places = list(dict.fromkeys(f"{r.geo_type}:{r.geo_id}" for r in refs))
    geo = ":".join(place) if name else None
    return {"subject": {"geo": geo, "name": name or "", "indicators": ids, "places": places}}


def _only_place(refs) -> tuple[str, str] | None:
    """The one place an answer's figures describe besides the state, or the state when they describe only it,
    for an answer about a county, which names no district."""
    places = {(r.geo_type, r.geo_id) for r in refs}
    others = places - {("state", "15")}
    return (
        next(iter(others)) if len(others) == 1 else ("state", "15") if places == {("state", "15")} else None
    )


def _choices(con, question: str, answer, selected) -> dict:
    """District choices for an assistant answer to a question that names a place spanning several districts,
    asked with no district chosen."""
    if selected is not None or answer.resolved is not None:
        return {}
    _, _, reply = place_of(con, question, None)
    return {"choices": reply["choices"]} if reply and "choices" in reply else {}


def _payload(con, answer, mode: str, selected: tuple[str, str] | None = None) -> dict:
    units = dict(con.execute("SELECT indicator_id, unit FROM indicator").fetchall())
    labels = dict(con.execute("SELECT indicator_id, label FROM indicator").fetchall())
    month = display.build_month(con)
    facts = []
    for f in answer.facts:
        source = con.execute("SELECT name, url FROM source WHERE source_id = ?", [f.source_id]).fetchone()
        status = con.execute(
            "SELECT moe_status FROM fact WHERE indicator_id = ? AND geo_type = ? AND geo_id = ? AND source_id = ?",
            [f.indicator_id, f.geo_type, f.geo_id, f.source_id],
        ).fetchone()
        place = con.execute(
            "SELECT name FROM geography WHERE geo_type = ? AND geo_id = ?", [f.geo_type, f.geo_id]
        ).fetchone()
        facts.append(
            {
                "label": labels.get(f.indicator_id, f.indicator_id),
                "value": fill_value(f, units.get(f.indicator_id, "count")),
                "year": display.year_text(f.vintage, month),
                "source": source[0] if source else f.source_id,
                "url": source[1] if source else "",
                "geography": geo_display(f.geo_type, f.geo_id, place[0] if place else f.geo_id),
                "uncertainty": status[0] if status else "unavailable",
            }
        )
    programs = display.program_rows(answer.programs)
    return (
        {
            "text": render(answer, con),
            "refusal": answer.refusal,
            "facts": facts,
            "programs": programs,
            "mode": mode,
        }
        | display.build(con, answer)
        | display.elsewhere(answer.refusal, bool(answer.facts))
        | answered_district(con, answer.resolved, selected)
        | subject(con, answer.resolved or selected or _only_place(answer.facts), answer.facts)
    )


class Assistant:
    """Shared model client and daily budget for every website question."""

    def __init__(self, budget: DailyBudget, model: str = DEFAULT_MODEL, client=None):
        self.budget, self.model, self._client = budget, model, client
        self.prices = load_prices()

    @staticmethod
    def warm() -> None:
        """Load the agent and model client code, so the first question does not wait for the import."""
        import klear.agent.loop  # noqa: F401

    def client(self):
        if self._client is None:
            from klear.agent.loop import KlearAgent  # Import on first use to keep startup fast.

            self._client = KlearAgent(None, self.model).client
        return self._client

    def ask(self, con, question: str, district: tuple[str, str] | None, geocoder=None, earlier=()) -> dict:
        """district is None when none is chosen. geocoder finds the Census blocks of a street address, or is None
        when address lookup is off. earlier holds the reader's last questions in this tab."""
        from klear.agent.loop import KlearAgent

        try:
            agent = KlearAgent(con, self.model, client=self.client(), geocoder=geocoder)
            rid = self.budget.reserve(agent.max_call_cost(self.model, question, self.prices))
        except (BudgetExceeded, MissingSecretError):
            return self._basic(con, question, district, geocoder, earlier)
        cost = usage = None
        try:
            answer, usage = agent.answer("web", question, district, earlier)
            cost = request_cost(self.model, usage)
            return _payload(con, answer, "assistant", district) | _choices(con, question, answer, district)
        except Exception:  # noqa: BLE001 - any failure falls back to the free lookup
            return self._basic(con, question, district, geocoder, earlier)
        finally:
            self.budget.settle(rid, cost, usage)

    @staticmethod
    def _basic(con, question: str, district: tuple[str, str] | None, geocoder=None, earlier=()) -> dict:
        return basic(con, question, district, BASIC, geocoder, earlier)


def basic(
    con, question: str, district: tuple[str, str] | None, note: str = "", geocoder=None, earlier=()
) -> dict:
    """Free keyword lookup, laid out like an assistant answer when it found figures."""
    result = lookup(con, question, district, geocoder, earlier)
    refs = result.pop("fact_refs", [])
    written, caveats, notes = result.pop("lead", []), result.pop("caveats", []), result.pop("notes", [])
    where, answered = result.pop("where", None), result.pop("district", district)
    if refs:
        answer = Answer(VERSION, answered, result["refusal"], [], refs, [], [])
        result |= display.build(con, answer)
        profile = asks_profile(question)
        lead = "This is the standard profile for this district. The district profile on this page compares each figure with the county and the state."
        if written:
            result["lead"] = ([note] if note else []) + written
        elif notes and result["refusal"] == "not_available":
            result["lead"] = [note] if note else []
        else:
            result["lead"] = [
                note or (lead if profile else "These are the closest published figures for this question.")
            ]
        if where:
            result["lead"].insert(1 if note else 0, where)
        result["lead"] += notes
        result["caveats"] = caveats
    elif note and result["refusal"] in ("none", "not_available"):
        result["text"] = f"{note}\n\n{result['text']}"
    return (
        result
        | {"mode": "basic"}
        | display.elsewhere(result["refusal"], bool(refs))
        | answered_district(con, answered, district)
        | subject(con, answered, refs)
    )
