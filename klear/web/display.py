"""Turns an agent answer into a short lead, number tiles, comparison charts and a source list for the website."""

import re
from datetime import datetime

from klear.agent.answer import actual_direction
from klear.config import CURRENT_LAYER
from klear.eval.contract import MARKER, _has, _number, geo_display, geo_key, short_name

MAX_TILES = 6
VINTAGE_NOTE = re.compile(r"\s*\(\{vintage:\w+\}\)")
TAGS = {
    "higher": "Higher than {other}",
    "lower": "Lower than {other}",
    "within_margin": "Within the margin of error",
}


class _Store:
    """Indicator, source and place lookups for one answer."""

    def __init__(self, con):
        self.con = con
        self.units = dict(con.execute("SELECT indicator_id, unit FROM indicator").fetchall())
        self.labels = dict(con.execute("SELECT indicator_id, label FROM indicator").fetchall())
        self.sources = {
            r[0]: (r[1], r[2]) for r in con.execute("SELECT source_id, name, url FROM source").fetchall()
        }
        self.additive = {
            r[0] for r in con.execute("SELECT indicator_id FROM indicator WHERE additive").fetchall()
        }
        self.month = build_month(con)

    def place(self, geo_type: str, geo_id: str) -> str:
        row = self.con.execute(
            "SELECT name FROM geography WHERE geo_type = ? AND geo_id = ?", [geo_type, geo_id]
        ).fetchone()
        return geo_display(geo_type, geo_id, row[0] if row else geo_id)

    def unit(self, f) -> str:
        return self.units.get(f.indicator_id, "count")

    def vintage(self, f) -> str:
        name = self.sources.get(f.source_id, (f.source_id, ""))[0]
        short = short_name(name)
        year = year_text(f.vintage, self.month)
        return year if short.casefold() in year.casefold() else f"{year} {short}"


def build_month(con) -> str:
    """Month the store was built, like 'October 2026', or '' when the store does not record it."""
    row = con.execute("SELECT built_at FROM build_info").fetchone()
    return f"{datetime.strptime(row[0][:10], '%Y-%m-%d'):%B %Y}" if row else ""


def year_text(vintage: str, month: str) -> str:
    """Reader text for a vintage. Layers that each build downloads fresh read as a current list."""
    if vintage != CURRENT_LAYER:
        return vintage
    return f"Current list as of {month}" if month else "Current list"


def _share(facts, home, store: _Store) -> str:
    """The district's share, like '1.9%', when a total is set against the whole state, else ''."""
    here, other = facts
    if (
        (here.geo_type, here.geo_id) != home
        or other.geo_type != "state"
        or here.indicator_id not in store.additive
    ):
        return ""
    if not _has(here.value) or not _has(other.value) or other.value <= 0:
        return ""
    part = here.value / other.value
    return "<0.1%" if 0 < part < 0.001 else f"{part:.1%}"


def _plain(f, unit: str) -> str:
    if not _has(f.value):
        return "not available"
    out = _number(unit, f.value)
    return f"about {out}" if _has(f.value_low) and _has(f.value_high) else out


def _spread(f, unit: str) -> str:
    if _has(f.value_low) and _has(f.value_high):
        return f"range {_number(unit, f.value_low)} to {_number(unit, f.value_high)}"
    if _has(f.moe):
        return f"± {f.moe:,.1f} points" if unit == "percent" else f"± {_number(unit, f.moe)}"
    return ""


def plain_text(texts: list[str], answer, store: _Store) -> list[str]:
    """Texts with bare numbers and no inline vintages, since tiles and the source line carry them."""
    by_ref = {f.ref: f for f in answer.facts}

    def sub(m):
        kind, ref = m.groups()
        if kind == "geo":
            key = geo_key(ref)
            return store.place(*key) if key else ref
        f = by_ref[ref]
        if kind == "label":
            return store.labels.get(f.indicator_id, f.indicator_id)
        if kind == "vintage":
            return store.vintage(f)
        return _plain(f, store.unit(f))

    return [MARKER.sub(sub, VINTAGE_NOTE.sub("", t)) for t in texts]


def _bar(f, store: _Store) -> dict:
    unit = store.unit(f)
    low = f.value_low if _has(f.value_low) else f.value - (f.moe or 0)
    high = f.value_high if _has(f.value_high) else f.value + (f.moe or 0)
    spread = _spread(f, unit)
    return {
        "geography": store.place(f.geo_type, f.geo_id),
        "value": f.value,
        "low": max(low, 0.0),
        "high": high,
        "text": f"{_number(unit, f.value)} {spread}".strip(),
    }


def charts(answer, store: _Store) -> tuple[list[dict], set[str]]:
    """One chart per indicator shown for two or more places, led by the answer's district."""
    groups: dict[str, list] = {}
    for f in answer.facts:
        if _has(f.value):
            groups.setdefault(f.indicator_id, []).append(f)
    home = answer.resolved
    out, used = [], set()
    for indicator, facts in groups.items():
        places = {(f.geo_type, f.geo_id) for f in facts}
        if len(places) < 2:
            continue
        facts.sort(key=lambda f: (f.geo_type, f.geo_id) != home)
        chart = {
            "label": store.labels.get(indicator, indicator),
            "unit": store.unit(facts[0]),
            "bars": [_bar(f, store) for f in facts],
            "tag": "",
        }
        # A district's total is always below the state's, so name its share instead of a direction.
        if len(facts) == 2 and (share := _share(facts, home, store)):
            chart["tag"] = f"{share} of {chart['bars'][1]['geography']}"
            chart["lower"] = False
        elif len(facts) == 2 and (way := actual_direction(facts[0], facts[1])):
            chart["tag"] = TAGS[way].format(other=chart["bars"][1]["geography"])
            chart["lower"] = way != "within_margin"
        out.append(chart)
        used.update(f.ref for f in facts)
    return out, used


def tiles(answer, store: _Store, skip: set[str]) -> list[dict]:
    home = answer.resolved
    out = []
    for f in answer.facts:
        if f.ref in skip or len(out) == MAX_TILES:
            continue
        unit = store.unit(f)
        here = (f.geo_type, f.geo_id)
        out.append(
            {
                "value": _plain(f, unit),
                "label": store.labels.get(f.indicator_id, f.indicator_id),
                "spread": _spread(f, unit),
                "geography": "" if here == home else store.place(*here),
            }
        )
    return out


def sources(answer, store: _Store) -> list[dict]:
    seen, out = set(), []
    for f in answer.facts:
        name, url = store.sources.get(f.source_id, (f.source_id, ""))
        if (name, f.vintage) not in seen:
            seen.add((name, f.vintage))
            out.append({"name": name, "url": url, "year": year_text(f.vintage, store.month)})
    return out


def build(con, answer) -> dict:
    store = _Store(con)
    drawn, used = charts(answer, store)
    return {
        "lead": plain_text(answer.blocks, answer, store),
        "tiles": tiles(answer, store, used),
        "charts": drawn,
        "sources": sources(answer, store),
        "caveats": plain_text(answer.caveats, answer, store),
    }
