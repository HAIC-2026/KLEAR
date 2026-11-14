"""Loads and checks the benefit program catalog in data/programs."""

import functools
import re
from dataclasses import dataclass
from datetime import date
from pathlib import Path
from urllib.parse import urlsplit

import yaml

from klear.config import ROOT
from klear.rules.registry import Rule, load_rules

PROGRAMS_DIR = ROOT / "data" / "programs"
REQUIRED = (
    "program_id",
    "name",
    "description",
    "serves",
    "agency",
    "url",
    "phone",
    "phone_source",
    "rules",
    "needs",
    "verified",
    "verification",
    "group",
)
OPTIONAL = ("phone_label", "level", "counties", "limit")
# The needs programs are grouped by on the website, with each group's heading.
GROUPS = {
    "kupuna": "Kūpuna",
    "food": "Food",
    "health": "Health coverage",
    "housing": "Housing",
    "taxes": "Taxes and income",
    "keiki": "Keiki",
}
LIMIT_FIELDS = ("rule", "value", "unit", "text")
LIMIT_UNITS = ("dollars", "percent")
COUNTIES = {
    "15001": "Hawaiʻi County",
    "15003": "Honolulu County",
    "15005": "Kalawao County",
    "15007": "Kauaʻi County",
    "15009": "Maui County",
}
PHONE = re.compile(r"^(?:\(\d{3}\) |1-\d{3}-)\d{3}-\d{4}$")
# A program last checked by a person longer ago than this sends readers to its official page.
STALE_AFTER_DAYS = 90
ELIGIBILITY_NOTE = (
    "This district-level view cannot establish any person's eligibility. "
    "Consult each program's official guidance."
)


@dataclass(frozen=True)
class Need:
    """A district figure that signals need: one rate, or a count and the total it is a share of."""

    indicators: tuple[str, ...]
    says: str


# Need figures a program can name, with the clause a reason sentence opens with.
NEEDS = {
    "pct_65_plus": Need(("pct_65_plus",), "Kūpuna are {value} of {place} residents"),
    "pct_65_plus_poverty": Need(
        ("pct_65_plus_poverty",), "In {place}, {value} of kūpuna live below the poverty level"
    ),
    "pct_hh_with_children": Need(
        ("pct_hh_with_children",), "In {place}, {value} of households include keiki under 18"
    ),
    "pct_children_poverty": Need(
        ("pct_children_poverty",), "In {place}, {value} of keiki live below the poverty level"
    ),
    "pct_people_poverty": Need(
        ("pct_people_poverty",), "In {place}, {value} of residents live below the poverty level"
    ),
    "pct_families_poverty": Need(
        ("pct_families_poverty",), "In {place}, {value} of families live below the poverty level"
    ),
    "pct_uninsured": Need(("pct_uninsured",), "In {place}, {value} of residents have no health insurance"),
    "pct_uninsured_under_19": Need(
        ("pct_uninsured_under_19",), "In {place}, {value} of children under 19 have no health insurance"
    ),
    "pct_rent_burden_35_plus": Need(
        ("pct_rent_burden_35_plus",),
        "In {place}, {value} of renter households pay 35% or more of income on rent",
    ),
    "pct_owner_cost_burden_35_plus": Need(
        ("pct_owner_cost_burden_35_plus",),
        "In {place}, {value} of owner households spend 35% or more of income on housing",
    ),
    "pct_severe_overcrowding": Need(
        ("pct_severe_overcrowding",), "In {place}, {value} of homes are severely overcrowded"
    ),
    "pct_hh_snap": Need(("pct_hh_snap",), "In {place}, {value} of households received SNAP in the past year"),
    "pct_hh_ssi": Need(("pct_hh_ssi",), "In {place}, {value} of households receive SSI"),
    "pct_disability": Need(("pct_disability",), "In {place}, {value} of residents have a disability"),
    "eitc_share": Need(
        ("irs_eitc_returns", "irs_returns"), "In {place}, an estimated {value} of tax returns claim the EITC"
    ),
}


@dataclass(frozen=True)
class Limit:
    """One headline income limit: a value in one of the program's rule files and the sentence it fills."""

    rule: str
    value: str
    unit: str
    text: str


@dataclass(frozen=True)
class Program:
    """One catalog entry: what the program does, who runs it, how to reach it and what signals need."""

    program_id: str
    name: str
    description: str
    serves: str
    agency: str
    url: str
    phone: str
    phone_source: str
    rules: tuple[str, ...]
    needs: tuple[str, ...]
    verified: date
    verification: str
    group: str
    phone_label: str = ""
    level: str = ""
    counties: tuple[str, ...] = ()
    limit: Limit | None = None

    @property
    def level_label(self) -> str:
        if self.level == "county":
            return ", ".join(COUNTIES[c] for c in self.counties) or "County"
        return self.level.title() or "Government"


def _https(value, gov: bool) -> bool:
    if not isinstance(value, str):
        return False
    parts = urlsplit(value)
    return parts.scheme == "https" and bool(parts.hostname) and (not gov or parts.hostname.endswith(".gov"))


def _ids(name: str, field: str, value, *, empty: bool = False) -> tuple[str, ...]:
    if (
        not isinstance(value, list)
        or (not value and not empty)
        or not all(isinstance(x, str) and x for x in value)
    ):
        raise ValueError(f"{name}: {field} must be a non-empty list of ids")
    if len(set(value)) != len(value):
        raise ValueError(f"{name}: {field} lists an id twice")
    return tuple(value)


def _limit(name: str, d, rules: tuple[str, ...], values: dict[str, dict] | None) -> Limit:
    """A program's headline limit, checked against its rules and, when given, the values those rules hold."""
    if not isinstance(d, dict) or sorted(d) != sorted(LIMIT_FIELDS):
        raise ValueError(f"{name}: limit needs exactly {list(LIMIT_FIELDS)}")
    if not all(isinstance(d[k], str) and d[k].strip() for k in LIMIT_FIELDS):
        raise ValueError(f"{name}: limit fields must be non-empty text")
    if d["rule"] not in rules:
        raise ValueError(f"{name}: limit rule {d['rule']} is not one of the program's rules")
    if d["unit"] not in LIMIT_UNITS:
        raise ValueError(f"{name}: limit unit must be one of {list(LIMIT_UNITS)}")
    if d["text"].count("{value}") != 1:
        raise ValueError(f"{name}: limit text must hold {{value}} once")
    if values is not None and d["value"] not in values.get(d["rule"], {}):
        raise ValueError(f"{name}: rule {d['rule']} has no value {d['value']!r}")
    return Limit(**d)


def _program(f: Path, d: dict, rule_ids: set[str], values: dict[str, dict] | None = None) -> Program:
    name = f.name
    missing = [k for k in REQUIRED if k not in d]
    if missing:
        raise ValueError(f"{name}: missing fields {missing}")
    if extra := sorted(set(d) - set(REQUIRED) - set(OPTIONAL)):
        raise ValueError(f"{name}: unknown fields {extra}")
    text = [k for k in (*REQUIRED, *OPTIONAL) if k not in ("rules", "needs", "counties", "limit") and k in d]
    if bad := [k for k in text if not isinstance(d[k], str) or not d[k].strip()]:
        raise ValueError(f"{name}: {bad} must be non-empty text")
    if d["program_id"] != f.stem:
        raise ValueError(f"{name}: program_id {d['program_id']!r} must match the file name")
    if not _https(d["url"], gov=True):
        raise ValueError(f"{name}: url must be an https page on a .gov site")
    if not _https(d["phone_source"], gov=False):
        raise ValueError(f"{name}: phone_source must be an https page")
    if not PHONE.match(d["phone"]):
        raise ValueError(f"{name}: phone {d['phone']!r} must look like (808) 555-0100 or 1-800-555-0100")
    rules = _ids(name, "rules", d["rules"], empty=True)
    if "level" in d and d["level"] not in ("federal", "state", "county"):
        raise ValueError(f"{name}: invalid level")
    counties = _ids(name, "counties", d.get("counties", []), empty=True)
    if any(c not in COUNTIES for c in counties):
        raise ValueError(f"{name}: unknown county code")
    if unknown := [r for r in rules if r not in rule_ids]:
        raise ValueError(f"{name}: unknown rule ids {unknown}")
    needs = _ids(name, "needs", d["needs"])
    if unknown := [n for n in needs if n not in NEEDS]:
        raise ValueError(f"{name}: unknown needs {unknown}, expected some of {sorted(NEEDS)}")
    try:
        verified = date.fromisoformat(d["verified"])
    except ValueError:
        raise ValueError(f"{name}: verified must be a date like 2026-11-09") from None
    if verified > date.today():
        raise ValueError(f"{name}: verified date {verified} is in the future")
    if d["group"] not in GROUPS:
        raise ValueError(f"{name}: group must be one of {list(GROUPS)}")
    limit = _limit(name, d["limit"], rules, values) if "limit" in d else None
    return Program(
        **(d | {"rules": rules, "needs": needs, "verified": verified, "counties": counties, "limit": limit})
    )


def load_programs(path: Path = PROGRAMS_DIR, rule_ids=None) -> list[Program]:
    """Every program in the catalog, checked, in file name order. Given only rule ids, a limit's value key is not
    checked against the rule file."""
    path = Path(path)
    if not path.is_dir():
        raise ValueError(f"programs directory {path} does not exist")
    if rule_ids is None:
        values = {r.rule_id: r.values or {} for r in load_rules()}
        known = set(values)
    else:
        values, known = None, set(rule_ids)
    out: list[Program] = []
    owner: dict[str, str] = {}
    for f in sorted(path.glob("*.yaml")):
        d = yaml.safe_load(f.read_text(encoding="utf-8"))
        if not isinstance(d, dict):
            raise TypeError(f"{f.name}: expected a mapping of fields")
        p = _program(f, d, known, values)
        for r in p.rules:
            if r in owner:
                raise ValueError(f"{f.name}: rule {r} already belongs to {owner[r]}")
            owner[r] = p.program_id
        out.append(p)
    if not out:
        raise ValueError(f"no programs in {path}")
    return out


@functools.cache
def catalog() -> tuple[Program, ...]:
    """The checked catalog, read once per process."""
    return tuple(load_programs())


@functools.cache
def _rule_book() -> dict[str, Rule]:
    return {r.rule_id: r for r in load_rules()}


def long_date(d: date) -> str:
    """A date as November 9, 2026."""
    return f"{d:%B} {d.day}, {d.year}"


def headline_limit(p: Program, rules: dict[str, Rule] | None = None) -> dict | None:
    """The program's one headline limit in words, with its rule and the date it took effect, or None when the program
    names none or its rule is not confirmed."""
    if p.limit is None:
        return None
    rule = (rules if rules is not None else _rule_book()).get(p.limit.rule)
    entry = (rule.values or {}).get(p.limit.value) if rule and rule.status == "confirmed" else None
    value = entry.get("value") if isinstance(entry, dict) else None
    if not isinstance(value, (int, float)) or isinstance(value, bool):
        return None
    shown = f"${value:,.0f}" if p.limit.unit == "dollars" else f"{value:g}%"
    return {
        "text": p.limit.text.format(value=shown),
        "rule": rule.rule_id,
        "effective": long_date(date.fromisoformat(rule.effective)),
    }


def by_rule() -> dict[str, Program]:
    """Each rule id mapped to the program whose thresholds it holds."""
    return {r: p for p in catalog() for r in p.rules}


def is_stale(p: Program, today: date | None = None) -> bool:
    """True when a person last checked the program's details more than STALE_AFTER_DAYS ago."""
    return ((today or date.today()) - p.verified).days > STALE_AFTER_DAYS
