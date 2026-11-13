# Eval runner: scores an agent on golden questions in replay or guarded live mode and applies the gates.
import hashlib
import re
from contextlib import suppress
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from pathlib import Path

from klear.eval.agents import Agent, MissingRecording, Usage, save_recording
from klear.eval.contract import Answer, ContractError, render
from klear.eval.golden import ROOT, Golden, pending_reason
from klear.eval.scorers import Outcome, score
from klear.eval.spend import (
    EPS,
    PRICES,
    LedgerError,
    SpendCapError,
    SpendLog,
    estimate_cost,
    request_cost,
    usage_problem,
)

DECLINE = ("decline_individual", "decline_partisan")
PASS_RATE = 0.95
RECORDINGS = ROOT / "eval" / "recordings"


@dataclass
class Item:
    golden: Golden
    status: str  # scored, pending or unanswered
    detail: str = ""
    outcomes: list[Outcome] = field(default_factory=list)
    answer: Answer | None = None
    rendered: str = ""
    usage: Usage | None = None

    @property
    def passed(self) -> bool:
        return all(o.passed for o in self.outcomes if o.passed is not None)


@dataclass
class RunResult:
    items: list[Item]
    agent: str
    mode: str
    model: str | None
    started: str
    store: str | None = None
    manifest_hash: str | None = None
    manifest_checked: bool = False
    cost: float = 0.0
    estimate: float | None = None
    worst: float | None = None
    stopped: str = ""
    error: str = ""
    uncertain: bool = False
    prior_uncertain: str = ""
    ledger: tuple[float, float] | None = None
    latency: float = 0.0
    recording: str = ""

    @property
    def scored(self) -> list[Item]:
        return [i for i in self.items if i.status == "scored"]

    @property
    def incomplete(self) -> list[Item]:
        return [i for i in self.items if i.status != "scored"]

    @property
    def manifest_missing(self) -> bool:
        return self.manifest_checked and self.manifest_hash is None


@dataclass
class Gate:
    name: str
    label: str
    passed: int
    total: int
    need: float

    @property
    def ok(self) -> bool:
        return self.total == 0 or self.passed / self.total >= self.need


def _check_ok(items: list[Item], check: str, categories: tuple[str, ...] | None = None) -> tuple[int, int]:
    rows = [
        o
        for i in items
        if categories is None or i.golden.category in categories
        for o in i.outcomes
        if o.check == check and o.passed is not None
    ]
    return sum(bool(o.passed) for o in rows), len(rows)


def gates(result: RunResult) -> list[Gate]:
    items = result.scored
    out = []
    for name, label, check, cats, need in (
        ("fidelity", "Numeric fidelity", "fidelity", None, 1.0),
        ("district", "District resolution (questions with an expected district)", "district", None, 1.0),
        ("refusal", "Refusal correctness (decline_individual, decline_partisan)", "refusal", DECLINE, 1.0),
    ):
        p, t = _check_ok(items, check, cats)
        out.append(Gate(name, label, p, t, need))
    out.append(Gate("overall", "Overall pass rate", sum(i.passed for i in items), len(items), PASS_RATE))
    return out


def exit_code(result: RunResult) -> int:
    if result.stopped or result.incomplete or result.manifest_missing or not result.items:
        return 2
    return 0 if all(g.ok for g in gates(result)) else 1


def _manifest_hash(path: Path | None) -> str | None:
    if path is None or not Path(path).is_file():
        return None
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def _render(answer: Answer, con) -> str:
    try:
        return render(answer, con)
    except (ContractError, KeyError) as e:
        return f"[render failed: {e}]\n\n" + "\n\n".join([*answer.blocks, *answer.caveats])


def _select(goldens: list[Golden], only: str | None) -> list[Golden]:
    return [g for g in goldens if only is None or only in (g.id, g.category, *g.tags)]


def _hst_now(fmt: str = "%Y-%m-%d %H:%M:%S HST") -> str:
    return (datetime.now(UTC) - timedelta(hours=10)).strftime(fmt)


def _failed(g: Golden, why: str, answer: Answer | None = None, usage: Usage | None = None) -> Item:
    return Item(g, "scored", "", [Outcome("contract", False, why)], answer, f"[{why}]", usage)


def run(
    goldens: list[Golden],
    agent: Agent,
    con,
    *,
    mode: str,
    confirm=None,
    model: str | None = None,
    only: str | None = None,
    spend: SpendLog | None = None,
    prices: Path = PRICES,
    manifest_path: Path | None = None,
    store: str | None = None,
    recordings: Path | None = None,
) -> RunResult:
    if mode not in ("replay", "live"):
        raise ValueError(f"mode must be 'replay' or 'live', got {mode!r}")
    if mode == "live":
        spend = spend or SpendLog()
    goldens = _select(goldens, only)
    keep = None
    result = RunResult(
        [],
        agent.name,
        mode,
        model,
        _hst_now(),
        store,
        _manifest_hash(manifest_path),
        manifest_path is not None,
    )
    todo = []
    for g in goldens:
        reason = pending_reason(g, con)
        if reason:
            result.items.append(Item(g, "pending", reason))
        else:
            todo.append(g)
    if mode == "live" and not _precheck(result, todo, agent, model, spend, prices, confirm):
        result.items += [Item(g, "unanswered", result.stopped) for g in todo]
        return _finish(result, goldens, Usage(0, 0, 0.0), spend if mode == "live" else None)
    used = Usage(0, 0, 0.0)
    if mode == "live":
        name = re.sub(r"[^A-Za-z0-9_-]", "-", str(model))
        result.recording = f"{name}-{_hst_now('%Y%m%dT%H%M%S%f')}"
        keep = (Path(recordings or RECORDINGS), result.recording)
    for g in todo:
        if result.stopped:
            result.items.append(Item(g, "unanswered", result.stopped))
            continue
        try:
            item, spent_usage = _ask(g, agent, con, mode, model, spend, prices, result, keep)
        except KeyboardInterrupt:
            result.stopped = "interrupted"
            item, spent_usage = Item(g, "unanswered", "interrupted"), None
        except (LedgerError, SpendCapError) as e:
            result.stopped = result.error = f"spend ledger: {e}"
            item, spent_usage = Item(g, "unanswered", result.stopped), None
        result.items.append(item)
        if spent_usage is not None:
            used = Usage(
                used.input_tokens + spent_usage.input_tokens,
                used.output_tokens + spent_usage.output_tokens,
                used.seconds + spent_usage.seconds,
                used.cache_write_tokens + spent_usage.cache_write_tokens,
                used.cache_read_tokens + spent_usage.cache_read_tokens,
            )
    return _finish(result, goldens, used, spend if mode == "live" else None)


def _ask(g, agent, con, mode, model, spend, prices, result, keep=None) -> tuple[Item, Usage | None]:
    """Run one question; settle its cost in live mode. Spend errors propagate to run."""
    reserved = None
    if mode == "live":
        worst = agent.max_call_cost(model, g.question, prices)
        try:
            reserved = (spend.reserve(worst), worst)
        except SpendCapError as e:
            result.stopped = f"spend cap: {e}"
            return Item(g, "unanswered", result.stopped), None
    try:
        # Only follow-up questions pass earlier questions, so agents that predate them still run.
        answer, usage = agent.answer(
            g.id, g.question, g.context, **({"earlier": g.earlier} if g.earlier else {})
        )
    except ContractError as e:
        if reserved is not None:
            _settle_worst(result, spend, reserved, model, f"malformed answer: {e}", stop=False)
        return _failed(g, f"malformed answer: {e}"), None
    except MissingRecording as e:
        if reserved is not None:
            _settle_worst(result, spend, reserved, model, f"call failed: {e!r}")
        return Item(g, "unanswered", str(e)), None
    except BaseException as e:
        why = "interrupted" if not isinstance(e, Exception) else f"call failed: {e!r}"
        if reserved is not None:
            _settle_worst(result, spend, reserved, model, why)
        else:
            result.stopped = why
        if not isinstance(e, Exception | KeyboardInterrupt):
            raise
        return Item(g, "unanswered", why), None
    if reserved is not None:
        rid, worst = reserved
        try:
            cost = request_cost(model, usage, prices)
        except (TypeError, ValueError) as e:
            _settle_worst(result, spend, reserved, model, f"invalid usage: {e}")
            spend.mark_uncertain(f"invalid usage from {agent.name} on {g.id}: {e}")
            result.uncertain = True
            return Item(g, "unanswered", result.stopped), None
        spend.settle(rid, model, usage, cost)
        result.cost += cost
        if cost > worst + EPS:
            result.stopped = f"call cost ${cost:.4f} exceeded its ${worst:.4f} reservation"
    elif (problem := usage_problem(usage)) is not None:
        result.stopped = f"invalid usage: {problem}"
        return Item(g, "unanswered", result.stopped), None
    if keep is not None:
        try:
            save_recording(*keep, g.id, answer, usage, getattr(agent, "trace", None))
        except OSError as e:
            result.error = f"recording not saved for {g.id}: {e}"
    try:
        item = Item(g, "scored", "", score(g, answer, con), answer, _render(answer, con), usage)
    except Exception as e:  # noqa: BLE001 - any scorer crash fails only this question
        item = _failed(g, f"scoring error: {e!r}", answer, usage)
    return item, usage


def _finish(
    result: RunResult, goldens: list[Golden], used: Usage, spend: SpendLog | None = None
) -> RunResult:
    if spend is not None:
        try:
            result.ledger = spend.snapshot()
        except LedgerError:
            result.ledger = None
    result.latency = used.seconds
    order = {g.id: n for n, g in enumerate(goldens)}
    result.items.sort(key=lambda i: order[i.golden.id])
    return result


def _settle_worst(
    result: RunResult, spend: SpendLog, reserved: tuple[str, float], model, why: str, stop: bool = True
) -> None:
    rid, worst = reserved
    settled = False
    try:
        spend.settle(rid, model, Usage(0, 0, 0.0), worst)
        settled = True
    finally:
        if not settled:
            result.uncertain = True
            with suppress(Exception):
                spend.mark_uncertain(f"settling ${worst:.4f} for a failed call failed ({why})")
    result.cost += worst
    if stop:
        result.stopped = why


def _precheck(result, todo, agent, model, spend, prices, confirm) -> bool:
    """Check every live precondition, then ask; True when the run may start."""
    if not hasattr(agent, "max_call_cost"):
        raise ValueError(f"agent {agent.name} does not declare max_call_cost, so it cannot run live")
    if (preflight := getattr(agent, "preflight", None)) is not None:
        preflight()
    result.estimate = estimate_cost(model, len(todo), prices)
    result.worst = sum(agent.max_call_cost(model, g.question, prices) for g in todo)
    if result.manifest_missing:
        result.stopped = "manifest.json is missing, refusing to spend on an incomplete run"
    elif not todo:
        result.stopped = "no answerable questions"
    if result.stopped:
        return False
    spend.check(result.estimate)
    settled, held = spend.snapshot()
    result.prior_uncertain = spend.uncertain()
    print(f"Live run: {len(todo)} questions, model {model}")
    print(f"Expected cost: about ${result.estimate:.2f} (estimate from typical token use per question)")
    print(f"Worst-case total reservation: ${result.worst:.2f} (each question reserves its own maximum)")
    print(f"Ledger: ${settled:.2f} settled plus ${held:.2f} held = ${settled + held:.2f} of ${spend.cap:.2f}")
    if result.prior_uncertain:
        print(f"WARNING: budget uncertain after an earlier run: {result.prior_uncertain}")
        want, prompt = "continue", 'Type "continue" to proceed: '
    else:
        want, prompt = "y", "Proceed and spend this? [y/N] "
    try:
        answer = (confirm or input)(prompt)
    except EOFError:
        answer = ""
    if answer.strip().lower() != want:
        result.stopped = "declined at the confirmation prompt"
        return False
    return True
