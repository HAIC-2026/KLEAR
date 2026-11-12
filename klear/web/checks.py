"""What the How we check KLEAR page shows, read from the latest build's validation.json."""

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


def checks_report(validation: Path) -> dict:
    """Build facts, every check with its report text and tables, the eval results and the known limits."""
    data = json.loads(Path(validation).read_text(encoding="utf-8"))
    if not isinstance(data, dict) or not isinstance(data.get("checks"), list):
        raise TypeError("validation.json has no checks")
    rows = [_view(c) for c in data["checks"]]
    skipped = [_skipped(s) for s in data.get("skipped", [])]
    ev = data.get("eval") or {}
    if ev and "tables" not in ev:
        ev = ev | {"tables": eval_tables(ev)}
    results = [CheckResult(**{k: v for k, v in c.items() if k in FIELDS}) for c in data["checks"]]
    return {
        "built_at": data.get("built_at"),
        "git_commit": data.get("git_commit"),
        "verdict": data.get("verdict") or verdict(results, data.get("skipped", [])),
        "store": data.get("store"),
        "checks": rows + skipped,
        "eval": ev,
        "caveats": data.get("caveats", CAVEATS),
    }
