"""What the How we check KLEAR page shows: the served store's validation.json, checked against the store's own build,
and the latest build attempt from validation-attempt.json."""

import json
from dataclasses import fields
from pathlib import Path

from klear.validation import CheckResult
from klear.validation.report import CAVEATS, check_view, eval_tables, skipped_view, verdict

FIELDS = {f.name for f in fields(CheckResult)}


def _view(c: dict) -> dict:
    # builds before the report text was stored in validation.json get it from the report code
    if "title" not in c:
        c = c | check_view(CheckResult(**{k: v for k, v in c.items() if k in FIELDS}))
    return {
        "name": c["name"],
        "title": c["title"],
        "gate": c["gate"],
        "result": c["result"],
        "summary": c["summary"],
        "about": c["about"],
        "notes": c.get("notes") or [],
        "tables": c.get("tables") or [],
    }


def _skipped(s: dict) -> dict:
    s = s if "title" in s else s | skipped_view(s)
    reason = str(s.get("reason", "")).rstrip(".")
    return {
        "name": s["name"],
        "title": s["title"],
        "gate": True,
        "result": "SKIPPED",
        "summary": f"Did not run: {reason}" if reason else "Did not run",
        "about": s["about"],
        "notes": [],
        "tables": [],
    }


def _attempt(path: Path | None, served: str | None) -> dict | None:
    """The latest build attempt, from validation-attempt.json: whether it is the build the site serves, its verdict,
    and when it is not, whether it replaced the store and why not. None when no attempt was recorded."""
    try:
        data = json.loads(Path(path).read_text(encoding="utf-8")) if path else None
    except (OSError, ValueError):
        return None
    if not isinstance(data, dict):
        return None
    store = data.get("store") if isinstance(data.get("store"), dict) else {}
    return {
        "served": served is not None and data.get("built_at") == served,
        "replaced": bool(store.get("replaced")),
        "reason": store.get("reason"),
        "verdict": data.get("verdict"),
    }


def checks_report(validation: Path, served: str | None = None, attempt: Path | None = None) -> dict:
    """Build facts, every check with its report text and tables, the eval results and the known limits, for the store
    the site serves. served is that store's built_at, from its build_info table, or None when it records none. A
    report written by another build ("stale") is not shown as the served data's checks: only its status is. The latest
    attempt, when it is not the served build, says whether it replaced the store and why not."""
    data = json.loads(Path(validation).read_text(encoding="utf-8"))
    if not isinstance(data, dict) or not isinstance(data.get("checks"), list):
        raise TypeError("validation.json has no checks")
    status = "unknown" if served is None else "current" if data.get("built_at") == served else "stale"
    last = _attempt(attempt, served)
    if status == "stale":
        return {"status": status, "attempt": last, "checks": [], "eval": {}, "caveats": []}
    rows = [_view(c) for c in data["checks"]]
    skipped = [_skipped(s) for s in data.get("skipped", [])]
    ev = data.get("eval") or {}
    if ev and "tables" not in ev:
        ev = ev | {"tables": eval_tables(ev)}
    results = [CheckResult(**{k: v for k, v in c.items() if k in FIELDS}) for c in data["checks"]]
    return {
        "status": status,
        "built_at": data.get("built_at"),
        "git_commit": data.get("git_commit"),
        "verdict": data.get("verdict") or verdict(results, data.get("skipped", [])),
        "store": data.get("store"),
        "attempt": last,
        "checks": rows + skipped,
        "eval": ev,
        "caveats": data.get("caveats", CAVEATS),
    }
