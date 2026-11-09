# Golden question loader: reads and validates the YAML files under eval/golden.
from dataclasses import dataclass, field, fields
from pathlib import Path

import yaml

from klear.eval.agents import GOLDEN_ID
from klear.eval.contract import REFUSALS
from klear.store.db import GEO_TYPES

ROOT = Path(__file__).resolve().parents[2]
CATEGORIES = (
    "profile",
    "compare",
    "find_district",
    "programs_gap",
    "help_sites",
    "modeled_need",
    "small_groups",
    "no_data",
    "decline_individual",
    "decline_partisan",
    "messy_input",
    "rank",
)
REQUIRED = ("id", "category", "question", "ideal")
DIRECTIONS = ("higher", "lower", "within_margin")
RULES = ROOT / "data" / "rules"
LIST_FIELDS = ("tags", "must_say", "must_not_say", "programs", "programs_forbidden")


class GoldenError(ValueError):
    pass


@dataclass
class Golden:
    id: str
    category: str
    question: str
    ideal: str
    context: tuple[str, str] | None = None
    tags: list[str] = field(default_factory=list)
    district: tuple[str, str] | None = None
    facts: list[tuple[str, str, str]] = field(default_factory=list)
    must_say: list[str] = field(default_factory=list)
    must_not_say: list[str] = field(default_factory=list)
    programs: list[str] = field(default_factory=list)
    programs_forbidden: list[str] = field(default_factory=list)
    refusal: str = "none"
    allowed_geos: list[tuple[str, str]] = field(default_factory=list)
    direction: str | None = None


def _strings(name: str, v, n: int | None) -> tuple[str, ...]:
    ok = isinstance(v, list | tuple) and (n is None or len(v) == n) and all(isinstance(x, str) for x in v)
    if not ok:
        want = f"{n} strings" if n else "strings"
        raise ValueError(f'{name} must be a list of {want}, got {v!r} (quote ids like "022")')
    return tuple(v)


def _geo_type(name: str, geo_type: str) -> None:
    if geo_type not in GEO_TYPES:
        raise ValueError(f"{name} geo_type {geo_type!r} not in {sorted(GEO_TYPES)}")


def rule_ids(rules: Path = RULES) -> set[str]:
    return {yaml.safe_load(p.read_text(encoding="utf-8"))["rule_id"] for p in Path(rules).glob("*.yaml")}


def _parse(path: Path, rules: set[str]) -> Golden:
    data = yaml.safe_load(path.read_text(encoding="utf-8"))
    if not isinstance(data, dict):
        raise TypeError("file must hold a mapping")
    names = {f.name for f in fields(Golden)}
    if extra := sorted(set(data) - names):
        raise ValueError(f"unknown field {extra[0]}")
    if missing := [k for k in REQUIRED if k not in data]:
        raise ValueError(f"missing field {missing[0]}")
    for k in ("id", "question", "ideal"):
        if not isinstance(data[k], str) or not data[k].strip():
            raise ValueError(f"{k} must be a non-empty string")
    if not GOLDEN_ID.fullmatch(data["id"]):
        raise ValueError(f"id {data['id']!r} may use letters, digits, hyphen and underscore only")
    if path.stem != data["id"]:
        raise ValueError(f"file name {path.name} does not match id {data['id']}")
    if data["category"] not in CATEGORIES:
        raise ValueError(f"unknown category {data['category']}")
    if path.parent.name != data["category"]:
        raise ValueError(f"category {data['category']} does not match folder {path.parent.name}")
    refusal = data.get("refusal", "none")
    if refusal not in REFUSALS:
        raise ValueError(f"unknown refusal {refusal}")
    kw: dict = {k: data[k] for k in REQUIRED} | {"refusal": refusal}
    for k in ("context", "district"):
        if data.get(k) is not None:
            kw[k] = _strings(k, data[k], 2)
            _geo_type(k, kw[k][0])
    for k in LIST_FIELDS:
        if k in data:
            kw[k] = list(_strings(k, data[k], None))
    if unknown := [p for p in kw.get("programs", []) + kw.get("programs_forbidden", []) if p not in rules]:
        raise ValueError(f"program {unknown[0]} is not a rule_id in data/rules")
    if "allowed_geos" in data:
        if not isinstance(data["allowed_geos"], list):
            raise ValueError("allowed_geos must be a list")
        kw["allowed_geos"] = [_strings("allowed_geos entry", x, 2) for x in data["allowed_geos"]]
        for x in kw["allowed_geos"]:
            _geo_type("allowed_geos", x[0])
    if data.get("direction") is not None:
        if data["direction"] not in DIRECTIONS:
            raise ValueError(f"direction {data['direction']!r} not in {DIRECTIONS}")
        kw["direction"] = data["direction"]
    if "facts" in data:
        facts = data["facts"]
        if not isinstance(facts, list):
            raise ValueError("facts must be a list")
        kw["facts"] = [_strings("fact", f, 3) for f in facts]
        for f in kw["facts"]:
            _geo_type("fact", f[1])
    return Golden(**kw)


def load_golden(root: Path = ROOT / "eval" / "golden", rules: Path = RULES) -> list[Golden]:
    for path in sorted([*root.rglob("*.yaml"), *root.rglob("*.yml")]):
        rel = path.relative_to(root)
        if path.suffix != ".yaml" or len(rel.parts) != 2:
            raise GoldenError(f"{rel}: golden files must be <category>/<id>.yaml")
    out, seen, known = [], {}, rule_ids(rules)
    for path in sorted(root.glob("*/*.yaml")):
        try:
            g = _parse(path, known)
        except (ValueError, TypeError, yaml.YAMLError) as e:
            raise GoldenError(f"{path.relative_to(root)}: {e}") from e
        if g.id in seen:
            raise GoldenError(f"{path.relative_to(root)}: duplicate id {g.id} (also in {seen[g.id]})")
        seen[g.id] = path.relative_to(root)
        out.append(g)
    return out


def pending_reason(g: Golden, con) -> str | None:
    indicators = {r[0] for r in con.execute("SELECT indicator_id FROM indicator").fetchall()}
    geos = {tuple(r) for r in con.execute("SELECT geo_type, geo_id FROM geography").fetchall()}
    for geo in (g.district, g.context):
        if geo is not None and geo not in geos:
            return f"geography {geo[0]} {geo[1]} not in store"
    have = {
        tuple(r)
        for r in con.execute(
            "SELECT DISTINCT indicator_id, geo_type, geo_id FROM fact WHERE value IS NOT NULL"
        ).fetchall()
    }
    for indicator_id, geo_type, geo_id in g.facts:
        if indicator_id not in indicators:
            return f"indicator {indicator_id} not in store"
        if (geo_type, geo_id) not in geos:
            return f"geography {geo_type} {geo_id} not in store"
        if (indicator_id, geo_type, geo_id) not in have:
            return f"no fact value for {indicator_id} at {geo_type} {geo_id}"
    return None
