"""Answers website questions with the KLEAR agent under a daily spend cap, falling back to the free lookup."""

import json
import os
import uuid
from contextlib import contextmanager
from datetime import UTC, datetime, timedelta
from pathlib import Path

from klear.agent.config import DEFAULT_MODEL
from klear.agent.loop import KlearAgent
from klear.config import MissingSecretError
from klear.eval.contract import VERSION, Answer, fill_value, geo_display, render
from klear.eval.spend import load_prices, request_cost
from klear.filelock import exclusive
from klear.rules.registry import load_rules
from klear.web import display
from klear.web.answers import ask as lookup
from klear.web.answers import asks_profile

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
    """Spend for the current HST day in one locked JSON file; open holds count until settled or the day ends."""

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

    def settle(self, rid: str, cost: float | None) -> None:
        """Replace a hold with its actual cost, or with the full hold when the cost is unknown."""
        with self._state() as s:
            # A hold from a previous day is already gone, so its cost is not added to today.
            held = s["held"].pop(rid, None)
            if held is not None:
                s["spent"] += held if cost is None else cost

    def spent(self) -> float:
        with self._state() as s:
            return s["spent"] + sum(s["held"].values())


def _payload(con, answer, mode: str) -> dict:
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
    rules = {r.rule_id: r for r in load_rules()}
    programs = [
        {"name": rules[p].program, "url": rules[p].url, "year": rules[p].effective}
        for p in answer.programs
        if p in rules
    ]
    return {
        "text": render(answer, con),
        "refusal": answer.refusal,
        "facts": facts,
        "programs": programs,
        "mode": mode,
    } | display.build(con, answer)


class Assistant:
    """Shared model client and daily budget for every website question."""

    def __init__(self, budget: DailyBudget, model: str = DEFAULT_MODEL, client=None):
        self.budget, self.model, self._client = budget, model, client
        self.prices = load_prices()

    def client(self):
        if self._client is None:
            self._client = KlearAgent(None, self.model).client
        return self._client

    def ask(self, con, question: str, district: tuple[str, str]) -> dict:
        try:
            agent = KlearAgent(con, self.model, client=self.client())
            rid = self.budget.reserve(agent.max_call_cost(self.model, question, self.prices))
        except (BudgetExceeded, MissingSecretError):
            return self._basic(con, question, district)
        cost = None
        try:
            answer, usage = agent.answer("web", question, district)
            cost = request_cost(self.model, usage)
            return _payload(con, answer, "assistant")
        except Exception:  # noqa: BLE001 - any failure falls back to the free lookup
            return self._basic(con, question, district)
        finally:
            self.budget.settle(rid, cost)

    @staticmethod
    def _basic(con, question: str, district: tuple[str, str]) -> dict:
        return basic(con, question, district, BASIC)


def basic(con, question: str, district: tuple[str, str], note: str = "") -> dict:
    """Free keyword lookup, laid out like an assistant answer when it found figures."""
    result = lookup(con, question, district)
    refs = result.pop("fact_refs", [])
    if refs:
        answer = Answer(VERSION, district, result["refusal"], [], refs, [], [])
        result |= display.build(con, answer)
        profile = asks_profile(question)
        lead = "This is the standard profile for this district. The profile beside the chat compares each figure with the county and the state."
        result["lead"] = [
            note or (lead if profile else "These are the closest published figures for this question.")
        ]
    elif note and result["refusal"] in ("none", "not_available"):
        result["text"] = f"{note}\n\n{result['text']}"
    return result | {"mode": "basic"}
