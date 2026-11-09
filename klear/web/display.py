"""Turns an agent answer into a short lead, number tiles, comparison charts and a source list for the website."""

import re
from datetime import datetime

from klear.agent.answer import actual_direction
from klear.agent.config import PROFILE_HEADLINE
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


# Short names for profile rows; the full indicator label stays available as each row's title.
PROFILE_LABELS = {
    "pop_total": "Total population",
    "pop_65_plus": "Kūpuna (65 and over)",
    "pct_65_plus": "Share who are kūpuna",
    "pop_under_18": "Keiki (under 18)",
    "median_age": "Median age",
    "pop_native_hawaiian_alone_or_combo": "Native Hawaiian, alone or in combination",
    "pct_limited_english": "Speak English less than very well",
    "civilian_veterans": "Veterans",
    "pct_disability": "With a disability",
    "median_household_income": "Median household income",
    "per_capita_income": "Per capita income",
    "pct_people_poverty": "Below the poverty level",
    "pct_children_poverty": "Keiki below the poverty level",
    "pct_65_plus_poverty": "Kūpuna below the poverty level",
    "pct_renter_occupied": "Homes that are rented",
    "median_gross_rent": "Median gross rent",
    "pct_rent_burden_35_plus": "Renters paying 35% or more of income",
    "pct_severe_overcrowding": "Severely overcrowded homes",
    "median_home_value": "Median home value",
    "pct_uninsured": "Without health insurance",
    "pct_public_coverage": "With public health coverage",
    "pct_broadband": "Households with broadband",
    "pct_hh_snap": "Households receiving SNAP",
    "pct_hh_ssi": "Households receiving SSI",
    "pct_hh_public_assistance": "Households receiving cash assistance",
    "irs_eitc_returns": "Tax returns claiming the EITC",
    "irs_eitc_dollars": "EITC dollars claimed",
    "aca_consumers": "Marketplace plan enrollees",
    "aca_aptc_consumers": "Enrollees with premium tax credits",
    "hud_vouchers": "Housing voucher holders",
    "places_food_insecurity": "Adults facing food insecurity",
    "places_housing_insecurity": "Adults facing housing insecurity",
    "places_lack_transport": "Adults without reliable transportation",
    "places_utility_shutoff_threat": "Adults facing a utility shut-off",
    "sites_health_center": "Health center sites",
    "sites_public_housing": "Public housing developments",
    "sites_lihtc": "Low-income housing tax credit projects",
}
NOUNS = {"percent": "rate", "dollars": "amount"}


def _compared(mine, theirs, unit: str, direction: str | None) -> str:
    """How the district's value relates to another place's, in words like Census Reporter's profiles.
    Gaps inside the margins of error read as about the same."""
    if direction == "within_margin" or not _has(mine.value) or not _has(theirs.value) or theirs.value <= 0:
        return "about the same as"
    ratio = mine.value / theirs.value
    if 0.9 <= ratio <= 1.1:
        return "a little higher than" if ratio > 1 else "a little lower than"
    if ratio >= 2:
        return f"about {ratio:.1f} times"
    if ratio > 1:
        return f"about {round((ratio - 1) * 10) * 10} percent higher than"
    return f"about {round(ratio * 10) * 10} percent of"


def _profile_row(row: dict, session, store: _Store) -> dict:
    fact = row["fact"]
    f = session.facts[fact["ref"]]
    unit = store.unit(f)
    year = year_text(fact["vintage"], store.month)
    against = []
    for a in row["against"]:
        theirs = session.facts[a["ref"]]
        value = _plain(theirs, unit)
        if "share" in a:
            against.append({"lead": a["share"], "rest": f"of the total in {a['geo_name']}: {value}"})
        else:
            lead = _compared(f, theirs, unit, a.get("direction"))
            against.append(
                {"lead": lead, "rest": f"the {NOUNS.get(unit, 'figure')} in {a['geo_name']}: {value}"}
            )
    return {
        "id": f.indicator_id,
        "label": PROFILE_LABELS.get(f.indicator_id, fact["label"]),
        "title": fact["label"],
        "value": _plain(f, unit),
        "spread": _spread(f, unit),
        "caution": "Unreliable estimate: the margin of error is large" if fact["reliable"] is False else "",
        "source": {
            "name": store.sources.get(f.source_id, (fact["source"], ""))[0],
            "url": store.sources.get(f.source_id, ("", ""))[1],
            "year": year,
        },
        "against": against,
    }


def _section(title: str, rows: list[dict]) -> dict:
    """A profile section with its sources listed once; when there are several, each row notes which it uses."""
    sources = list({(r["source"]["name"], r["source"]["year"]): r["source"] for r in rows}.values())
    keys = [(x["name"], x["year"]) for x in sources]
    rows = [
        r | {"note": keys.index((r["source"]["name"], r["source"]["year"])) + 1 if len(sources) > 1 else 0}
        for r in rows
    ]
    return {"title": title, "rows": rows, "sources": sources}


def profile(con, raw: dict, session, islands: str = "") -> dict:
    """The district profile tool's result laid out for the website, with figures written like the tiles."""
    store = _Store(con)
    rows = {
        r["fact"]["indicator_id"]: _profile_row(r, session, store) for s in raw["sections"] for r in s["rows"]
    }
    headline = [dict(rows[i]) for i in PROFILE_HEADLINE if i in rows]
    sections = [
        _section(s["title"], [rows[r["fact"]["indicator_id"]] for r in s["rows"]]) for s in raw["sections"]
    ]
    where = ", ".join([*(c["name"] for c in raw["counties"]), *([islands] if islands else [])])
    return {
        "geo": raw["district"]["geo"],
        "district": raw["district"]["name"],
        "where": where,
        "places": [{"name": p["name"], "share": _percent(p["share"])} for p in raw["places"]],
        "overlaps": [{"name": o["name"], "share": _percent(o["share"])} for o in raw["overlaps"]],
        "headline": headline,
        "sections": sections,
    }


def _percent(share: float) -> str:
    return "<1%" if 0 < share < 0.005 else f"{share:.0%}"
