"""Turns an agent answer into a short lead, number tiles, comparison charts and a source list for the website."""

import csv
import io
import re
from datetime import datetime
from pathlib import Path

from yaml import YAMLError

from klear.agent.answer import actual_direction
from klear.agent.config import AGENCIES, COVERS, PROFILE_HEADLINE
from klear.agent.tools import display_name
from klear.config import CURRENT_LAYER, ROOT
from klear.eval.contract import MARKER, _has, _number, geo_display, geo_key, short_name
from klear.rules.registry import load_rules

MAX_TILES = 6
RULES = ROOT / "data" / "rules"
# Rules that hold income limits many programs use, rather than a program residents can use.
LIMIT_RULES = {"fpg_2026_hi", "hud_il_fy2026_hi", "hpha_limits"}
# Where a profile's shares of residents in each place and overlapping district come from (klear/etl/blocks.py).
SHARES_SOURCE = {
    "name": "2020 Census Redistricting Data (P.L. 94-171), population by block",
    "url": "https://www.census.gov/programs-surveys/decennial-census/about/rdo/summary-files.html",
    "year": "2020 Census",
}
# How KLEAR moves ZIP code and tract figures to districts (tier A) and averages modeled ones (tier M), with a
# section for each source, anchored by its source_id.
METHODS_PAGE = "/methods.html"
METHOD_TIERS = {"A", "M"}
METHODS_TEXT = "How KLEAR estimates district ranges"
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
        # Readers are sent to each source's page; its url, what the build downloads, stays in the manifest.
        self.sources = {
            r[0]: (r[1], r[2]) for r in con.execute("SELECT source_id, name, page_url FROM source").fetchall()
        }
        self._kinds: dict[tuple, tuple] = {}
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

    def links(self, f) -> list[dict]:
        """Where a fact can be checked: its ACS table for that place, or the methods page."""
        key = (f.geo_type, f.geo_id, f.indicator_id, f.source_id)
        if key not in self._kinds:
            row = self.con.execute(
                "SELECT tier, table_id FROM fact WHERE geo_type = ? AND geo_id = ? AND indicator_id = ? "
                "AND source_id = ?",
                list(key),
            ).fetchone()
            self._kinds[key] = row or (None, None)
        tier, table = self._kinds[key]
        return figure_links(f.source_id, tier, table, f.geo_type, f.geo_id, self.place(f.geo_type, f.geo_id))

    def vintage(self, f) -> str:
        name = self.sources.get(f.source_id, (f.source_id, ""))[0]
        short = short_name(name)
        year = year_text(f.vintage, self.month)
        return year if short.casefold() in year.casefold() else f"{year} {short}"


def _built_day(con) -> datetime | None:
    row = con.execute("SELECT built_at FROM build_info").fetchone()
    try:
        return datetime.strptime(row[0][:10], "%Y-%m-%d") if row and row[0] else None
    except ValueError:
        return None


def build_month(con) -> str:
    """Month the store was built, like 'October 2026', or '' when the store does not record it."""
    day = _built_day(con)
    return f"{day:%B %Y}" if day else ""


def build_date(con) -> str:
    """Day the store was built, like 'October 9, 2026', or '' when the store does not record it."""
    day = _built_day(con)
    return f"{day:%B} {day.day}, {day.year}" if day else ""


def program_rules(path: Path = RULES) -> list:
    """Confirmed program rules in file order, leaving out income limit tables."""
    return [r for r in load_rules(path) if r.status == "confirmed" and r.rule_id not in LIMIT_RULES]


# The last program list read, with the rule files it was read from. Replaced whole, so threads never see half of it.
_programs: tuple = (None, [])


def programs(path: Path = RULES) -> list[dict]:
    """Programs to explore, one entry per program across its rule years. Every district gets the same list
    until programs are matched to district figures. Read again only when a rule file changes, and empty when
    the rules cannot be read, so a broken rule file never takes the profile down with it."""
    global _programs
    try:
        key = (path, tuple((f.name, f.stat().st_mtime_ns) for f in sorted(path.glob("*.yaml"))))
        read, out = _programs
        if read != key:
            found = {}
            for r in program_rules(path):
                name = display_name(r.program)
                entry = {
                    "name": name,
                    "covers": COVERS.get(r.rule_id, ""),
                    "agency": AGENCIES.get(r.rule_id, ""),
                }
                found.setdefault(name, entry | {"url": r.url})
            out = list(found.values())
            _programs = (key, out)
        return [dict(p) for p in out]
    except (OSError, ValueError, YAMLError):
        return []


def year_text(vintage: str, month: str) -> str:
    """Reader text for a vintage. Layers that each build downloads fresh read as a current list."""
    if vintage != CURRENT_LAYER:
        return vintage
    return f"Current list as of {month}" if month else "Current list"


def figure_links(
    source_id: str, tier: str | None, table_id: str | None, geo_type: str, geo_id: str, place: str
) -> list[dict]:
    """Where one figure can be checked. An ACS figure links to its table for that place on data.census.gov, which
    shows the same number. A figure KLEAR moved to districts or CDC modeled links to the methods page, since no
    publisher prints it for a district."""
    from klear.etl.profile import table_page  # ACS table names and Census GEO_IDs live with the ACS build

    if url := table_page(source_id, table_id, geo_type, geo_id):
        return [{"text": f"Table {table_id} for {place}", "url": url}]
    if tier in METHOD_TIERS:
        return [{"text": METHODS_TEXT, "url": f"{METHODS_PAGE}#{source_id}"}]
    return []


def _cited(sources: list[dict]) -> list[dict]:
    """One citation per source and vintage, in first-cited order, with links to where the figures it covers can be
    checked. A citation with nothing to check has no links."""
    out: dict[tuple, dict] = {}
    for s in sources:
        cited = out.setdefault((s["name"], s["year"]), {k: v for k, v in s.items() if k != "links"})
        if new := [x for x in s.get("links", []) if x not in cited.get("links", [])]:
            cited["links"] = cited.get("links", []) + new
    return list(out.values())


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
        # The estimate and its margin or range apart, for the chart's data table.
        "number": _plain(f, unit),
        "spread": spread,
    }


def _said(bar: dict, inside: bool = False) -> str:
    """A bar's figure for a sentence, like '12.3% ± 2.1 points' or 'about 900 (range 800 to 1,000)'."""
    number, spread = bar["number"], bar["spread"]
    if not spread:
        return number
    if spread.startswith("range"):
        return f"{number}, {spread}" if inside else f"{number} ({spread})"
    return f"{number} {spread}"


def _summary(facts, bars: list[dict], home, store: _Store) -> str:
    """One sentence that says what a chart shows: the first place's figure, then how each other place compares."""
    here, first = facts[0], bars[0]
    clauses = []
    for f, bar in zip(facts[1:], bars[1:]):
        other = f"{bar['geography']} ({_said(bar, inside=True)})"
        # A district's total is part of its county's and the state's, so only its share of the state says anything.
        if f.indicator_id in store.additive and f.geo_type in ("county", "state"):
            share = _share([here, f], home, store)
            clauses.append(f"is {share} of the total for {other}" if share else f"compares with {other}")
            continue
        way = actual_direction(here, f)
        verb = {
            "higher": "is higher than",
            "lower": "is lower than",
            "within_margin": "is within the margin of error of",
        }
        clauses.append(f"{verb.get(way, 'compares with')} {other}")
    joined = clauses[0] if len(clauses) == 1 else f"{', '.join(clauses[:-1])} and {clauses[-1]}"
    return f"{first['geography']}, at {_said(first)}, {joined}."


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
        chart["summary"] = _summary(facts, chart["bars"], home, store)
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
    return _cited([_source(f, store) | {"links": store.links(f)} for f in answer.facts])


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


def _source(f, store: _Store, name: str = "") -> dict:
    """A fact's source name, address and vintage as readers see it."""
    found, url = store.sources.get(f.source_id, (name or f.source_id, ""))
    return {"name": found, "url": url, "year": year_text(f.vintage, store.month)}


def _caution(fact: dict) -> str:
    return "Unreliable estimate: the margin of error is large" if fact["reliable"] is False else ""


def _profile_row(row: dict, session, store: _Store) -> dict:
    fact = row["fact"]
    f = session.facts[fact["ref"]]
    unit = store.unit(f)
    against = []
    for a in row["against"]:
        theirs = session.facts[a["ref"]]
        value = _plain(theirs, unit)
        # The other place's figure on its own too, for the printed summary's county and state columns.
        figure = {
            "geo": a["geo"],
            "place": a["geo_name"],
            "value": value,
            "spread": _spread(theirs, unit),
            "source": _source(theirs, store, fact["source"]),
        }
        if "share" in a:
            against.append({"lead": a["share"], "rest": f"of the total in {a['geo_name']}: {value}"} | figure)
        else:
            lead = _compared(f, theirs, unit, a.get("direction"))
            rest = f"the {NOUNS.get(unit, 'figure')} in {a['geo_name']}: {value}"
            against.append({"lead": lead, "rest": rest} | figure)
    return {
        "id": f.indicator_id,
        "label": PROFILE_LABELS.get(f.indicator_id, fact["label"]),
        "title": fact["label"],
        "value": _plain(f, unit),
        "spread": _spread(f, unit),
        "caution": _caution(fact),
        "source": _source(f, store, fact["source"]),
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
        _linked(
            _section(s["title"], [rows[r["fact"]["indicator_id"]] for r in s["rows"]]),
            s["rows"],
            session,
            store,
        )
        for s in raw["sections"]
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
        "shares_source": SHARES_SOURCE,
        "programs": programs(),
        "built": build_date(con),
    }


def _linked(section: dict, raw_rows: list[dict], session, store: _Store) -> dict:
    """A section whose sources each list where the district's figures from that source can be checked: its
    tables on data.census.gov, or the methods page."""
    cited = _cited(
        [
            _source(f, store, r["fact"]["source"]) | {"links": store.links(f)}
            for r in raw_rows
            for f in [session.facts[r["fact"]["ref"]]]
        ]
    )
    links = {(x["name"], x["year"]): x["links"] for x in cited if "links" in x}
    sources = [
        x | {"links": links[k]} if (k := (x["name"], x["year"])) in links else x for x in section["sources"]
    ]
    return section | {"sources": sources}


def _percent(share: float) -> str:
    return "<1%" if 0 < share < 0.005 else f"{share:.0%}"


CSV_COLUMNS = (
    "Section",
    "Measure",
    "Geography",
    "Estimate",
    "Margin of error (90%)",
    "Range low",
    "Range high",
    "Unit",
    "Source",
    "Vintage",
    "Source URL",
    "Note",
)


def _csv_number(value) -> str:
    """A raw number for spreadsheets: no separators or symbols, whole numbers without a decimal point."""
    if not _has(value):
        return ""
    return str(int(value)) if float(value).is_integer() else str(round(value, 4))


def profile_csv(con, raw: dict, session) -> str:
    """Every profile figure and its county and state comparisons, one row per place, as CSV with a byte
    order mark so spreadsheets read the ʻokina and kahakō."""
    store = _Store(con)
    out = io.StringIO()
    writer = csv.writer(out)
    writer.writerow(CSV_COLUMNS)
    district = raw["district"]["name"]
    for section in raw["sections"]:
        for row in section["rows"]:
            fact = row["fact"]
            places = [(session.facts[fact["ref"]], district, _caution(fact))]
            places += [(session.facts[a["ref"]], a["geo_name"], "") for a in row["against"]]
            for f, place, note in places:
                source = _source(f, store, fact["source"])
                writer.writerow(
                    [
                        section["title"],
                        fact["label"],
                        place,
                        _csv_number(f.value),
                        _csv_number(f.moe),
                        _csv_number(f.value_low),
                        _csv_number(f.value_high),
                        store.unit(f),
                        source["name"],
                        source["year"],
                        source["url"],
                        note or ("" if _has(f.value) else "Not available"),
                    ]
                )
    return "﻿" + out.getvalue()
