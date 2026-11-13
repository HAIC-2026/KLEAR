# Eval report writer: a markdown report and a JSON twin with gates, breakdowns, failures and skipped questions.
import json
import secrets
from dataclasses import asdict
from datetime import UTC, datetime, timedelta
from pathlib import Path

from klear.eval.contract import answer_to_json
from klear.eval.golden import ROOT
from klear.eval.runner import Item, RunResult, exit_code, gates
from klear.eval.spend import TOKEN_FIELDS as TOKENS

NOTE = "Overall counts answered, non-pending questions only; pending and unanswered ones are listed under Skipped."


def _pct(p: int, t: int) -> str:
    return f"{100 * p / t:.1f}%" if t else "n/a"


def _table(head: list[str], rows: list[list[str]]) -> list[str]:
    lines = ["| " + " | ".join(head) + " |", "|" + "|".join("---" for _ in head) + "|"]
    return [*lines, *("| " + " | ".join(r) + " |" for r in rows), ""]


def _quote(text: str) -> list[str]:
    return ["> " + line if line else ">" for line in (text or "(empty)").splitlines()]


def _categories(items: list[Item]) -> dict[str, list[Item]]:
    out: dict[str, list[Item]] = {}
    for i in items:
        out.setdefault(i.golden.category, []).append(i)
    return out


def _checks(items: list[Item]) -> dict[str, list[bool]]:
    out: dict[str, list[bool]] = {}
    for i in items:
        for o in i.outcomes:
            if o.passed is not None:
                out.setdefault(o.check, []).append(bool(o.passed))
    return out


def _tokens(items: list[Item]) -> str:
    """Token totals of the answered questions, with the prompt cache writes and reads."""
    used = [i.usage for i in items if i.usage is not None]
    total = {k: sum(getattr(u, k) for u in used) for k in TOKENS}
    return (
        f"- Tokens: {total['input_tokens']:,} input, {total['cache_write_tokens']:,} cache write, "
        f"{total['cache_read_tokens']:,} cache read, {total['output_tokens']:,} output"
    )


def _markdown(result: RunResult, code: int) -> str:
    scored = result.scored
    manifest = result.manifest_hash or (
        "not recorded"
        if not result.manifest_checked
        else "manifest.json is missing, so the store build is not identified (exit 2)"
    )
    lines = [
        f"# KLEAR agent eval: {result.agent} ({result.mode})",
        "",
        f"- Run: {result.started}",
        f"- Agent: {result.agent}",
        f"- Model: {result.model or 'none'}",
        f"- Store: {result.store or 'unknown'}",
        f"- Store manifest hash (SHA-256 of data/build/manifest.json): {manifest}",
        f"- Questions: {len(result.items)} total, {len(scored)} answered, {len(result.incomplete)} skipped",
        f"- Cost: ${result.cost:.4f}"
        + (f" (estimated ${result.estimate:.2f})" if result.estimate is not None else "")
        + (f", worst-case reservation ${result.worst:.2f}" if result.worst is not None else ""),
        f"- Model time: {result.latency:.1f} s total, {result.latency / len(scored):.2f} s per answer"
        if scored
        else "- Model time: n/a",
        _tokens(scored),
        f"- Exit code: {code}",
    ]
    if result.recording:
        lines.append(
            f"- Recording: {result.recording} (rescore with --agent recorded --recording {result.recording})"
        )
    if result.stopped:
        lines.append(f"- Stopped: {result.stopped}")
    if result.ledger is not None:
        settled, held = result.ledger
        lines.append(
            f"- Ledger after run: ${settled:.2f} settled plus ${held:.2f} held = ${settled + held:.2f} "
            "counted against the cap"
        )
    if result.uncertain or result.prior_uncertain:
        why = "this run" if result.uncertain else f"earlier run: {result.prior_uncertain}"
        lines.append(f"- Budget uncertain ({why}); the next live run needs a typed continue")
    if result.error:
        lines.append(f"- Error: {result.error}")
    lines += ["", "## Gates", "", NOTE, ""]
    lines += _table(
        ["Gate", "Passed", "Total", "Rate", "Need", "Result"],
        [
            [g.label, str(g.passed), str(g.total), _pct(g.passed, g.total), f"{100 * g.need:.0f}%",
             ("n/a" if not g.total else "PASS" if g.ok else "FAIL")]
            for g in gates(result)
        ],
    )  # fmt: skip
    lines += ["## By category", ""]
    lines += _table(
        ["Category", "Answered", "Passed", "Rate", "Skipped"],
        [
            [c, str(len(s := [i for i in v if i.status == "scored"])), str(sum(i.passed for i in s)),
             _pct(sum(i.passed for i in s), len(s)), str(len(v) - len(s))]
            for c, v in sorted(_categories(result.items).items())
        ],
    )  # fmt: skip
    lines += ["## By check", ""]
    lines += _table(
        ["Check", "Passed", "Total", "Rate"],
        [[c, str(sum(v)), str(len(v)), _pct(sum(v), len(v))] for c, v in _checks(scored).items()],
    )
    failed = [i for i in scored if not i.passed]
    lines += ["## Failures", ""]
    if not failed:
        lines += ["None.", ""]
    for i in failed:
        lines += [f"### {i.golden.id} ({i.golden.category})", "", "Question:", *_quote(i.golden.question), ""]
        lines += ["Rendered answer:", *_quote(i.rendered), ""]
        lines += ["Failed checks:", ""]
        lines += [f"- {o.check}: {o.detail}" for o in i.outcomes if o.passed is False]
        lines.append("")
    lines += ["## Skipped", ""]
    if not result.incomplete:
        lines += ["None.", ""]
    for i in result.incomplete:
        lines.append(f"- {i.golden.id} ({i.golden.category}, {i.status}): {i.detail}")
    return "\n".join(lines).rstrip() + "\n"


def _json(result: RunResult, code: int) -> dict:
    return {
        "run": result.started,
        "agent": result.agent,
        "mode": result.mode,
        "model": result.model,
        "store": result.store,
        "manifest_hash": result.manifest_hash,
        "manifest_missing": result.manifest_missing,
        "cost": result.cost,
        "estimate": result.estimate,
        "worst": result.worst,
        "latency": result.latency,
        "stopped": result.stopped,
        "error": result.error,
        "budget_uncertain": result.uncertain,
        "prior_budget_uncertain": result.prior_uncertain,
        "ledger_settled": result.ledger[0] if result.ledger else None,
        "ledger_held": result.ledger[1] if result.ledger else None,
        "exit_code": code,
        "recording": result.recording or None,
        "gates": [
            {"name": g.name, "passed": g.passed, "total": g.total, "need": g.need, "ok": g.ok}
            for g in gates(result)
        ],
        "items": [
            {
                "id": i.golden.id,
                "category": i.golden.category,
                "status": i.status,
                "detail": i.detail,
                "passed": i.passed if i.status == "scored" else None,
                "outcomes": [{"check": o.check, "passed": o.passed, "detail": o.detail} for o in i.outcomes],
                "rendered": i.rendered,
                "answer": answer_to_json(i.answer) if i.answer is not None else None,
                "usage": asdict(i.usage) if i.usage is not None else None,
            }
            for i in result.items
        ],
    }


def write_report(result: RunResult, out_dir: Path = ROOT / "eval" / "reports") -> tuple[Path, Path]:
    out_dir = Path(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    code = exit_code(result)
    while True:
        now = datetime.now(UTC) - timedelta(hours=10)
        stem = f"{now:%Y%m%dT%H%M%S%f}-{secrets.token_hex(3)}-{result.agent}-{result.mode}"
        md, js = out_dir / f"{stem}.md", out_dir / f"{stem}.json"
        try:
            with open(md, "x", encoding="utf-8") as f:
                f.write(_markdown(result, code))
            with open(js, "x", encoding="utf-8") as f:
                f.write(json.dumps(_json(result, code), ensure_ascii=False, indent=2) + "\n")
        except FileExistsError:
            continue
        return md, js
