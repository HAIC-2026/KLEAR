"""One measure for every House or Senate district, split into five shades for the district map.

Figures come from the store through the rank_districts tool, with no model call, and are written the way each
district's profile writes them, so the shaded map, its table and the profile always show the same values."""

import json
from itertools import pairwise

from klear.agent.tools import Session
from klear.eval.contract import _has, _number, geo_key
from klear.store.methods import place_note
from klear.web.display import (
    NOUNS,
    _cited,
    _compared,
    _plain,
    _said,
    _source,
    _spread,
    _Store,
    _unreliable_note,
)

# Measures the map can be shaded by: rates and medians, which compare fairly across districts of any size.
MEASURES = (
    "pct_people_poverty",
    "pct_children_poverty",
    "pct_65_plus",
    "median_household_income",
    "pct_rent_burden_35_plus",
    "pct_uninsured",
    "pct_hh_snap",
)
CHAMBERS = {"house": "House", "senate": "Senate"}
SHADES = 5
OK, UNRELIABLE, MISSING = "ok", "unreliable", "not available"
HOW = "Darker shades are higher. Each shade holds about the same number of districts."
OVERLAP_NOTE = "Districts whose margins of error overlap may not differ."


class NoMeasure(LookupError):
    """The store has no figures for the measure."""


def _interval(f) -> tuple[float, float] | None:
    """A figure's range or margin of error as low and high ends, as the profile compares them."""
    if _has(f.value_low) and _has(f.value_high):
        return f.value_low, f.value_high
    return None if not _has(f.value) else (f.value - (f.moe or 0), f.value + (f.moe or 0))


def _direction(mine, theirs) -> str | None:
    x, y = _interval(mine), _interval(theirs)
    if x is None or y is None:
        return None
    return "within_margin" if x[0] <= y[1] and y[0] <= x[1] else "higher" if x[0] > y[1] else "lower"


def _versus(f, state, unit: str) -> str:
    """How a district's figure compares with the state's, in the profile's words, like 'about 20 percent higher
    than the rate in Hawaiʻi: 10.0%'."""
    value = _plain(state, unit)
    if note := place_note(state.source_id, state.geo_type):
        value = f"{value} ({note})"
    lead = _compared(f, state, unit, _direction(f, state))
    return f"{lead} the {NOUNS.get(unit, 'figure')} in Hawaiʻi: {value}"


def _groups(figures: list[tuple[float, str]]) -> list[list[int]]:
    """Positions of figures sorted low to high, split into up to SHADES groups of about equal count. Each break
    falls where the written figure changes, nearest the equal split, so no two shades in the legend share a value."""
    n = len(figures)
    cuts = [i for i in range(1, n) if figures[i][1] != figures[i - 1][1]]
    chosen: list[int] = []
    for j in range(1, SHADES):
        if cuts and (best := min(cuts, key=lambda c: (abs(c - j * n / SHADES), c))) not in chosen:
            chosen.append(best)
    bounds = [0, *sorted(chosen), n]
    return [list(range(a, b)) for a, b in pairwise(bounds) if b > a]


def _ramp(count: int) -> list[int]:
    """Shades 1 to SHADES for a number of groups, spread across the ramp when there are fewer groups."""
    if count == 1:
        return [(SHADES + 1) // 2]
    return [int(1 + j * (SHADES - 1) / (count - 1) + 0.5) for j in range(count)]


def _unreliable(shaky: int, total: int) -> str:
    if not shaky:
        return ""
    if total == 1:
        return "The only estimate is unreliable because its margin of error is large. It is striped."
    if shaky == 1:
        return (
            f"1 of the {total} estimates is unreliable because its margin of error is large. It is striped."
        )
    return (
        f"{shaky} of the {total} estimates are unreliable because their margins of error are large. "
        "They are striped."
    )


def _sentence(text: str) -> str:
    return f"{text[:1].upper()}{text[1:]}" + ("" if text.endswith(".") else ".")


def map_view(con, indicator: str, chamber: str) -> dict:
    """Every district of a chamber with its figure, margin, status and shade, highest first, then the districts
    with no figure; the shades' ranges; and the measure's label, source, year and state figure."""
    session = Session(con)
    ranked = json.loads(session.run("rank_districts", {"indicator": indicator, "chamber": chamber}))
    if "error" in ranked:
        if ranked["error"].startswith("rank_districts failed:"):
            raise RuntimeError(ranked["error"])
        raise NoMeasure(ranked["error"])
    found = json.loads(session.run("get_facts", {"indicator_ids": [indicator], "geos": ["state:15"]}))
    store = _Store(con)
    label, unit = store.short(indicator), ranked["unit"]
    state = session.facts[found["facts"][0]["ref"]] if found.get("facts") else None
    entries = [d for t in ranked["tiers"] for d in t["districts"]] + ranked["unranked"]
    shown, missing, facts = [], [], []
    for entry in entries:
        f = session.facts[entry["ref"]]
        if not _has(f.value):
            missing.append({"geo": entry["geo"], "reason": entry.get("reason", "")})
            continue
        facts.append(f)
        value, spread = _plain(f, unit), _spread(f, unit)
        low, high = _interval(f)
        status = UNRELIABLE if entry["reliable"] is False else OK
        text = f"{label}: {_said({'number': value, 'spread': spread})}."
        if state is not None:
            text += f" {_sentence(_versus(f, state, unit))}"
        if status == UNRELIABLE:
            text += f" {_unreliable_note(entry)}."
        shown.append(
            {
                "geo": entry["geo"],
                "name": entry["geo_name"],
                "value": value,
                "spread": spread,
                "number": f.value,
                "low": round(max(low, 0.0), 6),
                "high": round(high, 6),
                "status": status,
                "caution": _unreliable_note(entry),
                "text": text,
            }
        )
    missing += [{"geo": m["geo"], "reason": m["reason"]} for m in ranked["missing"]]
    shown.sort(key=lambda d: (d["number"], d["geo"]))
    groups = _groups([(d["number"], _number(unit, d["number"])) for d in shown])
    shades = []
    for shade, members in zip(_ramp(len(groups)), groups, strict=True):
        low, high = shown[members[0]]["number"], shown[members[-1]]["number"]
        lo, hi = _number(unit, low), _number(unit, high)
        shades.append(
            {
                "shade": shade,
                "low": low,
                "high": high,
                "text": lo if lo == hi else f"{lo} to {hi}",
                "count": len(members),
            }
        )
        for i in members:
            shown[i]["shade"] = shade
    shown.reverse()
    absent = [
        {
            "geo": m["geo"],
            "name": store.place(*geo_key(m["geo"])),
            "value": MISSING,
            "spread": "",
            "number": None,
            "low": None,
            "high": None,
            "status": MISSING,
            "shade": None,
            "reason": m["reason"],
            "text": f"{label}: no figure." + (f" {_sentence(m['reason'])}" if m["reason"] else ""),
        }
        for m in sorted(missing, key=lambda m: m["geo"])
    ]
    word = CHAMBERS[chamber]
    sources = _cited([_source(f, store) for f in facts])
    return {
        "indicator": indicator,
        "chamber": chamber,
        "label": label,
        "meaning": store.meaning(indicator),
        "unit": unit,
        "title": f"{label}, by {word} district",
        "source": sources[0] if sources else None,
        "sources": sources,
        "year": sources[0]["year"] if sources else "",
        "state": None
        if state is None
        else {
            "name": "Hawaiʻi",
            "value": _plain(state, unit),
            "spread": _spread(state, unit),
            "number": state.value,
        },
        "shades": shades,
        "districts": shown + absent,
        "how": HOW if shades else f"No {word} district has a figure for this measure.",
        "overlap": OVERLAP_NOTE,
        "unreliable": _unreliable(sum(d["status"] == UNRELIABLE for d in shown), len(shown)),
        "caveat": ranked.get("note", ""),
    }
