# Answer contract (version 1) the KLEAR agent must return, with JSON conversion and rendering.
import math
import re
from dataclasses import asdict, dataclass, fields

from klear.store.db import GEO_TYPES

VERSION = "1"
REFUSALS = ("none", "decline_individual", "decline_partisan", "not_available", "out_of_scope")
# Markers: {fact:REF} renders the cited value, {vintage:REF} its vintage and source, {label:REF} its
# indicator label, {geo:TYPE:ID} a place name, {share:TYPE:ID:TYPE:ID} the share of a district's 2020 Census
# population living in the second geography.
MARKER = re.compile(r"\{(fact|vintage|label|geo|share):([^{}\s]+)\}")
# Marker kinds that name places rather than fact refs.
PLACE_MARKERS = ("geo", "share")
CHAMBERS = ("house", "senate")
TEXT_FIELDS = ("ref", "indicator_id", "geo_type", "geo_id", "source_id", "vintage")
NUMBER_FIELDS = ("value", "value_low", "value_high", "moe")
# Short source names by prefix of the stored source name.
SHORT_NAMES = (
    ("American Community Survey", "ACS"),
    ("ACS", "ACS"),
    ("IRS Statistics of Income", "IRS SOI"),
    ("Marketplace open enrollment", "Marketplace"),
    ("Housing Choice Vouchers", "HUD"),
    ("Public Housing", "HUD"),
    ("Low-Income Housing Tax Credit", "HUD LIHTC"),
    ("Health Center", "HRSA"),
    ("PLACES", "PLACES"),
    ("Comprehensive Housing Affordability Strategy", "CHAS"),
)


class ContractError(ValueError):
    pass


def short_name(name: str) -> str:
    return next((short for prefix, short in SHORT_NAMES if name.startswith(prefix)), name)


def geo_key(ref: str) -> tuple[str, str] | None:
    """Split a geo marker body TYPE:ID, or None when malformed."""
    geo_type, _, geo_id = ref.partition(":")
    return (geo_type, geo_id) if geo_type in GEO_TYPES and geo_id else None


def share_key(ref: str) -> tuple[tuple[str, str], tuple[str, str]] | None:
    """Split a share marker body into a House or Senate district and a district of the other chamber or a
    county, or None when malformed."""
    parts = ref.split(":")
    if len(parts) != 4 or not all(parts):
        return None
    district, other = (parts[0], parts[1]), (parts[2], parts[3])
    if district[0] not in CHAMBERS or other[0] not in (*CHAMBERS, "county") or other[0] == district[0]:
        return None
    return district, other


def district_overlap(con, geo: tuple[str, str]) -> list[tuple[str, str, int, float]]:
    """(geo_type, geo_id, pop2020, share) for each district of the other chamber, then each county, holding a
    House or Senate district's 2020 residents, largest share first. Empty for a store without the table."""
    if geo[0] not in CHAMBERS:
        return []
    has = con.execute(
        "SELECT count(*) FROM information_schema.tables WHERE table_name = 'district_overlap'"
    ).fetchone()[0]
    if not has:
        return []
    return con.execute(
        "SELECT geo_type, geo_id, pop2020, share FROM district_overlap WHERE district_type = ? AND district_id = ? "
        "ORDER BY geo_type = 'county', share DESC, geo_id",
        list(geo),
    ).fetchall()


def share_text(share: float) -> str:
    """A population share as a percent, never rounded to 0% or 100% unless it is."""
    if share >= 1:
        return "100%"
    if share > 0.999:
        return ">99.9%"
    return "<0.1%" if share < 0.001 else f"{share:.1%}"


def render_share(con, ref: str) -> str:
    """A share marker's text, like 89.2% of the 2020 Census population of House District 22."""
    key = share_key(ref)
    if key is None:
        raise ContractError(
            f"marker {{share:{ref}}} needs a House or Senate district, then TYPE:ID of the other "
            "chamber's district or a county"
        )
    (district, other), names = key, []
    for geo in key:
        row = con.execute(
            "SELECT name FROM geography WHERE geo_type = ? AND geo_id = ?", list(geo)
        ).fetchone()
        if row is None:
            raise ContractError(f"unknown geography {geo[0]}:{geo[1]}")
        names.append(geo_display(*geo, row[0]))
    share = next((s for t, i, _, s in district_overlap(con, district) if (t, i) == other), None)
    if share is None:
        raise ContractError(f"{names[0]} has no 2020 residents in {names[1]}")
    return f"{share_text(share)} of the 2020 Census population of {names[0]}"


def geo_display(geo_type: str, geo_id: str, name: str) -> str:
    """Clean display name of a geography from its type, id and stored name."""
    if geo_type in ("house", "senate") and geo_id.isdigit():
        return f"{geo_type.capitalize()} District {int(geo_id)}"
    if geo_type == "zcta":
        return f"ZIP code area {geo_id}"
    if geo_type == "tract":
        return f"Census tract {geo_id}"
    if geo_type == "county":
        return re.split(r"[,;(]", name)[0].strip()
    if geo_type == "state":
        return "Hawaiʻi"
    return name


@dataclass
class FactRef:
    ref: str
    indicator_id: str
    geo_type: str
    geo_id: str
    source_id: str
    value: float | None
    value_low: float | None
    value_high: float | None
    moe: float | None
    vintage: str


@dataclass
class Answer:
    contract: str
    resolved: tuple[str, str] | None
    refusal: str
    blocks: list[str]
    facts: list[FactRef]
    programs: list[str]
    caveats: list[str]


def _strings(name: str, v) -> list[str]:
    if not isinstance(v, list) or not all(isinstance(x, str) for x in v):
        raise ContractError(f"{name} must be a list of strings, got {v!r}")
    return list(v)


def _fact(d) -> FactRef:
    if not isinstance(d, dict):
        raise ContractError(f"facts must hold objects, got {d!r}")
    if set(d) != {f.name for f in fields(FactRef)}:
        raise ContractError(f"fact fields must be {[f.name for f in fields(FactRef)]}, got {sorted(d)}")
    for k in TEXT_FIELDS:
        if not isinstance(d[k], str):
            raise ContractError(f"fact {k} must be a string, got {d[k]!r}")
    for k in NUMBER_FIELDS:
        if d[k] is not None and (isinstance(d[k], bool) or not isinstance(d[k], int | float)):
            raise ContractError(f"fact {k} must be a number or null, got {d[k]!r}")
    return FactRef(**d)


def answer_from_json(d: dict) -> Answer:
    if not isinstance(d, dict):
        raise ContractError(f"answer must be an object, got {type(d).__name__}")
    if missing := sorted({f.name for f in fields(Answer)} - set(d)):
        raise ContractError(f"malformed answer: missing {missing}")
    if d["contract"] != VERSION:
        raise ContractError(f"unknown contract version {d['contract']!r}, expected {VERSION!r}")
    if d["refusal"] not in REFUSALS:
        raise ContractError(f"unknown refusal {d['refusal']!r}, expected one of {REFUSALS}")
    resolved = d["resolved"]
    if resolved is not None:
        resolved = tuple(_strings("resolved", resolved))
    if not isinstance(d["facts"], list):
        raise ContractError(f"facts must be a list, got {d['facts']!r}")
    facts = [_fact(f) for f in d["facts"]]
    a = Answer(
        d["contract"],
        resolved,
        d["refusal"],
        _strings("blocks", d["blocks"]),
        facts,
        _strings("programs", d["programs"]),
        _strings("caveats", d["caveats"]),
    )
    if a.resolved is not None and len(a.resolved) != 2:
        raise ContractError(f"resolved must be [geo_type, geo_id], got {list(a.resolved)}")
    refs = [f.ref for f in facts]
    if dupes := sorted({r for r in refs if refs.count(r) > 1}):
        raise ContractError(f"duplicate fact refs {dupes}")
    for text in [*a.blocks, *a.caveats]:
        for kind, ref in MARKER.findall(text):
            if kind == "geo" and geo_key(ref) is None:
                raise ContractError(f"marker {{geo:{ref}}} needs TYPE:ID with TYPE in {sorted(GEO_TYPES)}")
            if kind == "share" and share_key(ref) is None:
                raise ContractError(
                    f"marker {{share:{ref}}} needs TYPE:ID:TYPE:ID, a district then the other geography"
                )
            if kind not in PLACE_MARKERS and ref not in refs:
                raise ContractError(f"marker {{{kind}:{ref}}} has no fact ref")
    return a


def answer_to_json(a: Answer) -> dict:
    return {
        "contract": a.contract,
        "resolved": list(a.resolved) if a.resolved is not None else None,
        "refusal": a.refusal,
        "blocks": list(a.blocks),
        "facts": [asdict(f) for f in a.facts],
        "programs": list(a.programs),
        "caveats": list(a.caveats),
    }


def _number(unit: str, v: float) -> str:
    if unit == "dollars":
        return f"${v:,.0f}"
    if unit == "percent":
        return f"{v:,.1f}%"
    if unit == "years":
        return f"{v:,.1f}"
    if unit == "ratio":
        return f"{v:,.2f}"
    return f"{v:,.0f}"


def _has(v: float | None) -> bool:
    return v is not None and not math.isnan(v)


def fill_value(f: FactRef, unit: str) -> str:
    if not _has(f.value):
        return "not available"
    out = _number(unit, f.value)
    if _has(f.value_low) and _has(f.value_high):
        return f"{out} (range {_number(unit, f.value_low)} to {_number(unit, f.value_high)})"
    if _has(f.moe):
        return f"{out} (margin of error ±{_number(unit, f.moe)})"
    return out


def render(a: Answer, con) -> str:
    by_ref = {f.ref: f for f in a.facts}
    units = dict(con.execute("SELECT indicator_id, unit FROM indicator").fetchall())
    labels = dict(con.execute("SELECT indicator_id, label FROM indicator").fetchall())
    sources = dict(con.execute("SELECT source_id, name FROM source").fetchall())

    def sub(m: re.Match) -> str:
        if m.group(1) == "share":
            return render_share(con, m.group(2))
        if m.group(1) == "geo":
            row = None
            if (key := geo_key(m.group(2))) is not None:
                row = con.execute(
                    "SELECT name FROM geography WHERE geo_type = ? AND geo_id = ?", list(key)
                ).fetchone()
            if row is None:
                raise ContractError(f"unknown geography {m.group(2)}")
            return geo_display(*key, row[0])
        f = by_ref[m.group(2)]
        if m.group(1) == "label":
            if f.indicator_id not in labels:
                raise ContractError(f"fact {f.ref} names unknown indicator {f.indicator_id}")
            return labels[f.indicator_id]
        if m.group(1) == "vintage":
            if f.source_id not in sources:
                raise ContractError(f"fact {f.ref} names unknown source {f.source_id}")
            short = short_name(sources[f.source_id])
            return f.vintage if short.casefold() in f.vintage.casefold() else f"{f.vintage} {short}"
        if f.indicator_id not in units:
            raise ContractError(f"fact {f.ref} names unknown indicator {f.indicator_id}")
        return fill_value(f, units[f.indicator_id])

    return "\n\n".join(MARKER.sub(sub, t) for t in [*a.blocks, *a.caveats])
