# Eval results for the validation report: the newest tracked recording rescored, and the published held-out run.
import re
from pathlib import Path

import duckdb
import yaml

from klear.eval.agents import RecordedAgent
from klear.eval.golden import CATEGORIES, ROOT, load_golden
from klear.eval.runner import RECORDINGS, gates, run

HELDOUT = ROOT / "eval" / "heldout_summary.yaml"
STAMP = re.compile(r"^(?P<model>.+)-(?P<stamp>\d{8}T\d{6})\d*$")
# Plain names for the eval gates and categories, as the public page shows them.
GATES = {
    "overall": "Answered correctly",
    "fidelity": "Numbers taken only from the data store",
    "district": "Found the right district",
    "refusal": "Declined personal and partisan requests",
}
CATEGORY_LABELS = {
    "profile": "District profiles",
    "compare": "Comparing a district with its county or the state",
    "find_district": "Finding the right district",
    "programs_gap": "Program use and who may qualify",
    "help_sites": "Health centers and housing sites",
    "modeled_need": "Modeled needs from CDC PLACES",
    "small_groups": "Small groups and unreliable estimates",
    "no_data": "Saying when the data is missing",
    "decline_individual": "Declining questions about a person or household",
    "decline_partisan": "Declining partisan questions",
    "messy_input": "Typos and shorthand",
    "rank": "Ranking districts by a measure",
}
LIMITS = [
    (
        "The answer checks test numbers, districts, sources, wording rules and refusals. They cannot judge "
        "whether an answer is clear or complete."
    ),
    (
        "Practice results replay answers the model gave once. A new run of the same model can answer "
        "differently."
    ),
]


def _gate(name: str, passed: int, total: int) -> dict:
    return {"name": name, "label": GATES.get(name, name), "passed": passed, "total": total}


def newest_recording(recordings: Path = RECORDINGS) -> str | None:
    # recording folders are named <model>-<HST timestamp>
    root = Path(recordings)
    names = [p.name for p in root.iterdir() if p.is_dir() and STAMP.match(p.name)] if root.is_dir() else []
    return max(names, key=lambda n: STAMP.match(n)["stamp"], default=None)


def _recorded_at(name: str) -> tuple[str, str]:
    m = STAMP.match(name)
    s = m["stamp"]
    return m["model"], f"{s[:4]}-{s[4:6]}-{s[6:8]} {s[9:11]}:{s[11:13]} HST"


def practice(store: Path, recordings: Path = RECORDINGS, golden: Path = ROOT / "eval" / "golden") -> dict:
    # rescore the newest recording against the store; replay mode makes no model calls
    name = newest_recording(recordings)
    if name is None:
        return {"error": f"no recorded run in {Path(recordings).name}"}
    model, recorded_at = _recorded_at(name)
    with duckdb.connect(str(store), read_only=True) as con:
        result = run(load_golden(golden), RecordedAgent(recordings, name), con, mode="replay")
    scored = result.scored
    cats = {}
    for item in result.items:
        c = cats.setdefault(item.golden.category, {"answered": 0, "passed": 0, "skipped": 0})
        if item.status == "scored":
            c["answered"] += 1
            c["passed"] += item.passed
        else:
            c["skipped"] += 1
    order = {c: n for n, c in enumerate(CATEGORIES)}
    return {
        "recording": name,
        "model": model,
        "recorded_at": recorded_at,
        "questions": len(result.items),
        "answered": len(scored),
        "passed": sum(i.passed for i in scored),
        "gates": [_gate(g.name, g.passed, g.total) | {"need": g.need, "ok": g.ok} for g in gates(result)],
        "categories": [
            {"category": k, "label": CATEGORY_LABELS.get(k, k)} | v
            for k, v in sorted(cats.items(), key=lambda kv: order.get(kv[0], len(order)))
        ],
        "failed": [i.golden.id for i in scored if not i.passed],
    }


def heldout(path: Path = HELDOUT) -> dict:
    # the published summary only; the held-out questions are not in this repository
    data = yaml.safe_load(Path(path).read_text(encoding="utf-8"))
    results = [_gate(k, int(v[0]), int(v[1])) for k, v in data["results"].items()]
    return {k: data[k] for k in ("model", "run", "reviewer", "questions", "source")} | {"results": results}


def eval_summary(store: Path | None, *, recordings: Path = RECORDINGS, heldout_path: Path = HELDOUT) -> dict:
    # never raises: a problem is reported in place of the numbers
    out: dict = {"limits": LIMITS}
    if store is None:
        out["practice"] = {"error": "this build did not write a data store to rescore against"}
    else:
        try:
            out["practice"] = practice(store, recordings)
        except Exception as e:  # noqa: BLE001 - the eval summary is reported, never gated
            out["practice"] = {"error": f"rescoring failed: {e}"}
    try:
        out["heldout"] = heldout(heldout_path)
    except (OSError, KeyError, TypeError, ValueError, yaml.YAMLError) as e:
        out["heldout"] = {"error": f"held-out summary unreadable: {e}"}
    return out
