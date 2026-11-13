"""Loads and checks the benefit program catalog in data/programs."""

import functools
import re
from dataclasses import dataclass
from datetime import date
from pathlib import Path
from urllib.parse import urlsplit

import yaml

from klear.config import ROOT
from klear.rules.registry import load_rules

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
)
OPTIONAL = ("phone_label", "level", "counties")
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
    phone_label: str = ""
    level: str = ""
    counties: tuple[str, ...] = ()

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


def _program(f: Path, d: dict, rule_ids: set[str]) -> Program:
    name = f.name
    missing = [k for k in REQUIRED if k not in d]
    if missing:
        raise ValueError(f"{name}: missing fields {missing}")
    if extra := sorted(set(d) - set(REQUIRED) - set(OPTIONAL)):
        raise ValueError(f"{name}: unknown fields {extra}")
    text = [k for k in (*REQUIRED, *OPTIONAL) if k not in ("rules", "needs", "counties") and k in d]
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
    return Program(**(d | {"rules": rules, "needs": needs, "verified": verified, "counties": counties}))


def load_programs(path: Path = PROGRAMS_DIR, rule_ids=None) -> list[Program]:
    """Every program in the catalog, checked, in file name order."""
    path = Path(path)
    if not path.is_dir():
        raise ValueError(f"programs directory {path} does not exist")
    known = set(rule_ids) if rule_ids is not None else {r.rule_id for r in load_rules()}
    out: list[Program] = []
    owner: dict[str, str] = {}
    for f in sorted(path.glob("*.yaml")):
        d = yaml.safe_load(f.read_text(encoding="utf-8"))
        if not isinstance(d, dict):
            raise TypeError(f"{f.name}: expected a mapping of fields")
        p = _program(f, d, known)
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


def by_rule() -> dict[str, Program]:
    """Each rule id mapped to the program whose thresholds it holds."""
    return {r: p for p in catalog() for r in p.rules}


def is_stale(p: Program, today: date | None = None) -> bool:
    """True when a person last checked the program's details more than STALE_AFTER_DAYS ago."""
    return ((today or date.today()) - p.verified).days > STALE_AFTER_DAYS
