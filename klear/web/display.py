"""Turns an agent answer into a short lead, number tiles, comparison charts and a source list for the website."""

import csv
import io
import re
from datetime import datetime

from klear.agent.answer import actual_direction
from klear.agent.config import OUTREACH_NOTE_LIMIT, OUTREACH_PAIRS, PROFILE_HEADLINE
from klear.config import CURRENT_LAYER
from klear.eval.contract import (
    MARKER,
    MEMBER_MARKERS,
    _has,
    _number,
    geo_display,
    geo_key,
    member_text,
    short_name,
)
from klear.labels import in_sentence, meaning, short_label
from klear.members import as_listed, members_of, timing
from klear.programs import ELIGIBILITY_NOTE, NEEDS, Program, by_rule, catalog, is_stale
from klear.store.methods import place_note

MAX_TILES = 6
# Pointer to datatrove.ai, a HAIC sponsor with Hawaiʻi public data not published by district. KLEAR only links to it.
ELSEWHERE = {
    "text": "For other Hawaiʻi public data on this topic, try",
    "name": "datatrove.ai",
    "url": "https://datatrove.ai/",
}
NO_DATA = ("not_available", "out_of_scope")
# Answers that may show figures; declines and out-of-scope replies get no figure line.
ANSWERED = ("none", "not_available")
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
        self.universes = dict(con.execute("SELECT indicator_id, universe FROM indicator").fetchall())

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

    def short(self, indicator_id: str) -> str:
        """The label readers see in place of the official one."""
        return short_label(indicator_id, self.labels.get(indicator_id, indicator_id))

    def meaning(self, indicator_id: str) -> dict:
        """What the short label stands for, shown one tap away: definition, official label and universe."""
        return meaning(
            indicator_id, self.labels.get(indicator_id, indicator_id), self.universes.get(indicator_id)
        )


def _built_day(con) -> datetime | None:
    row = con.execute("SELECT built_at FROM build_info").fetchone()
    try:
        return datetime.strptime(row[0][:10], "%Y-%m-%d") if row and row[0] else None
    except ValueError:
        return None


def build_day(con) -> str | None:
    """Day the store was built in HST, like '2026-10-09', or None when the store does not record it."""
    day = _built_day(con)
    return f"{day:%Y-%m-%d}" if day else None


def build_month(con) -> str:
    """Month the store was built, like 'October 2026', or '' when the store does not record it."""
    day = _built_day(con)
    return f"{day:%B %Y}" if day else ""


def build_date(con) -> str:
    """Day the store was built, like 'October 9, 2026', or '' when the store does not record it."""
    day = _built_day(con)
    return f"{day:%B} {day.day}, {day.year}" if day else ""


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


LIST_ITEM = re.compile(r"^\s*[*-]\s+")


def _opens(text: str, at: int) -> bool:
    """Whether a marker at this position starts a sentence or a list item, so its label keeps its capital."""
    line = LIST_ITEM.sub("", text[:at].rsplit("\n", 1)[-1]).rstrip()
    return not line or line[-1] in ".!?:"


def plain_text(texts: list[str], answer, store: _Store) -> list[str]:
    """Texts with bare numbers and no inline vintages, since tiles and the source line carry them. Labels are
    the short ones readers see, in lower case mid-sentence."""
    by_ref = {f.ref: f for f in answer.facts}

    def sub(m):
        kind, ref = m.groups()
        if kind in MEMBER_MARKERS:
            return member_text(store.con, kind, ref)
        if kind == "geo":
            key = geo_key(ref)
            return store.place(*key) if key else ref
        f = by_ref[ref]
        if kind == "label":
            label = store.short(f.indicator_id)
            return label if _opens(m.string, m.start()) else in_sentence(label)
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
            "label": store.short(indicator),
            "meaning": store.meaning(indicator),
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


def tiles(answer, store: _Store, skip: set[str]) -> tuple[list[dict], set[str]]:
    """Up to MAX_TILES tiles for the facts no chart shows, and the refs they show."""
    home = answer.resolved
    out, used = [], set()
    for f in answer.facts:
        if f.ref in skip or len(out) == MAX_TILES:
            continue
        unit = store.unit(f)
        here = (f.geo_type, f.geo_id)
        used.add(f.ref)
        out.append(
            {
                "value": _plain(f, unit),
                "label": store.short(f.indicator_id),
                "spread": _spread(f, unit),
                "geography": "" if here == home else store.place(*here),
                "meaning": store.meaning(f.indicator_id),
            }
        )
    return out, used


def sources(answer, store: _Store) -> list[dict]:
    return _cited([_source(f, store) | {"links": store.links(f)} for f in answer.facts])


def member_cards(con, geos) -> list[dict]:
    """Each listed member of the districts with their official page, the listing they were read from and its
    notes, for the links under an answer and on the profile."""
    out = []
    for geo in dict.fromkeys(geos):
        for m in members_of(con, *geo):
            out.append(
                {
                    "title": m.title,
                    "name": m.name,
                    "district": geo_display(m.geo_type, m.geo_id, ""),
                    "url": m.profile_url,
                    "source": f"Legislative Reference Bureau, {m.listing}",
                    "source_url": m.page_url,
                    "listed": as_listed(m),
                    "timing": timing(m),
                }
            )
    return out


def verified(figures: int, sources: int, refusal: str = "none") -> dict:
    """The line under an answer saying how many figures it shows and that each comes from a cited source."""
    if not figures or refusal not in ANSWERED:
        return {}
    where = "the cited source below" if sources == 1 else "the cited sources below"
    if figures == 1:
        text = f"The 1 figure in this answer comes from {where}."
    else:
        text = f"All {figures} figures in this answer come from {where}."
    return {"verified": {"figures": figures, "sources": sources, "text": text}}


def shown_figures(answer, refs: set[str]) -> list:
    """The facts with a value among those the answer shows, in answer order."""
    return [f for f in answer.facts if f.ref in refs and _has(f.value)]


def build(con, answer) -> dict:
    store = _Store(con)
    drawn, charted = charts(answer, store)
    tiled, tiled_refs = tiles(answer, store, charted)
    filled = {
        ref for t in [*answer.blocks, *answer.caveats] for kind, ref in MARKER.findall(t) if kind == "fact"
    }
    shown = shown_figures(answer, charted | tiled_refs | filled)
    cited = {(s["name"], s["year"]) for s in (_source(f, store) for f in shown)}
    texts = " ".join([*answer.blocks, *answer.caveats])
    named = [geo_key(r) for k, r in MARKER.findall(texts) if k == "member" and geo_key(r)]
    return {
        "lead": plain_text(answer.blocks, answer, store),
        "tiles": tiled,
        "charts": drawn,
        "sources": sources(answer, store),
        "caveats": plain_text(answer.caveats, answer, store),
        "members": member_cards(con, named),
    } | verified(len(shown), len(cited), answer.refusal)


def elsewhere(refusal: str, has_figures: bool) -> dict:
    """The datatrove.ai pointer for an answer with no figures because the data is missing or out of scope."""
    return {"elsewhere": dict(ELSEWHERE)} if refusal in NO_DATA and not has_figures else {}


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


def _beside(item: dict | None, session, store: _Store) -> dict | None:
    """The figure shown next to a profile row, or only a sentence when the row has no second figure."""
    if not item:
        return None
    if "fact" not in item:
        return {"value": "", "spread": "", "text": item["text"], "source": None}
    f = session.facts[item["fact"]["ref"]]
    unit = store.unit(f)
    return {
        "value": _plain(f, unit),
        "spread": _spread(f, unit),
        "text": item["text"],
        "source": _source(f, store, item["fact"]["source"]),
    }


def _profile_row(row: dict, session, store: _Store) -> dict:
    fact = row["fact"]
    f = session.facts[fact["ref"]]
    unit = store.unit(f)
    against, compared = [], []
    for a in row["against"]:
        if "missing" in a:
            against.append({"lead": "No figure", "rest": f"for {a['geo_name']}: {a['missing']}"})
            continue
        theirs = session.facts[a["ref"]]
        compared.append(_source(theirs, store))
        value = _plain(theirs, unit)
        if note := place_note(theirs.source_id, theirs.geo_type):
            value = f"{value} ({note})"
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
        "label": store.short(f.indicator_id),
        "meaning": store.meaning(f.indicator_id),
        "value": _plain(f, unit),
        "spread": _spread(f, unit),
        "caution": _caution(fact),
        "caveat": fact.get("note", ""),
        "source": _source(f, store, fact["source"]),
        "against": against,
        "beside": _beside(row.get("beside"), session, store),
        "compared": compared,
    }


def _public(row: dict) -> dict:
    return {k: v for k, v in row.items() if k != "compared"}


def _section(title: str, rows: list[dict]) -> dict:
    """A profile section with its sources listed once; when there are several, each row notes which it uses.
    Sources that only the county and state figures come from are listed after them. A caveat every row
    shares is stated once for the section."""
    cited = [r["source"] for r in rows] + [
        r["beside"]["source"] for r in rows if (r["beside"] or {}).get("source")
    ]
    sources = list({(x["name"], x["year"]): x for x in cited}.values())
    keys = [(x["name"], x["year"]) for x in sources]
    compared = {(c["name"], c["year"]): c for r in rows for c in r["compared"]}

    def number(src: dict | None) -> int:
        return keys.index((src["name"], src["year"])) + 1 if src and len(sources) > 1 else 0

    rows = [
        _public(r)
        | {"note": number(r["source"])}
        | ({"beside": r["beside"] | {"note": number(r["beside"]["source"])}} if r["beside"] else {})
        for r in rows
    ]
    caveats = {r["caveat"] for r in rows}
    shared = caveats.pop() if len(caveats) == 1 else ""
    if shared:
        rows = [r | {"caveat": ""} for r in rows]
    others = [c for k, c in compared.items() if k not in keys]
    return {"title": title, "rows": rows, "sources": sources, "compared": others, "caveat": shared}


def profile(
    con, raw: dict, session, islands: str = "", matched: dict | None = None, tops: dict | None = None
) -> dict:
    """The district profile tool's result laid out for the website, with figures written like the tiles, the
    district programs tool's result when given, and the outreach pairs whose top tier holds the district."""
    store = _Store(con)
    rows = {
        r["fact"]["indicator_id"]: _profile_row(r, session, store) for s in raw["sections"] for r in s["rows"]
    }
    headline = [_public(rows[i]) for i in PROFILE_HEADLINE if i in rows]
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
        "county": raw["counties"][0]["name"] if raw["counties"] else "",
        "places": [{"name": p["name"], "share": _percent(p["share"])} for p in raw["places"]],
        "overlaps": [{"name": o["name"], "share": _percent(o["share"])} for o in raw["overlaps"]],
        "members": member_cards(con, [tuple(raw["district"]["geo"].split(":"))]),
        "headline": headline,
        "sections": sections,
        "shares_source": SHARES_SOURCE,
        "programs": matched_programs(con, matched, session) if matched else None,
        "outreach": outreach_note(raw["district"], tops, session, store) if tops and tops["ranked"] else None,
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


# How a reason sentence ends for each direction against the state.
TAILS = {
    "higher": "compared with {state} statewide",
    "within_margin": "about the same as the {state} statewide",
    "lower": "lower than the {state} statewide",
    "not_comparable": "compared with {state} statewide",
}


def _need_figures(reason: dict, session, store: _Store) -> tuple[str, str, str]:
    """The district value, its margin or range, and the state value, as a reason sentence shows them."""
    mine = [session.facts[r] for r in reason["refs"]]
    state = [session.facts[r] for r in reason["state_refs"]]
    if len(mine) == 1:
        unit = store.unit(mine[0])
        return _number(unit, mine[0].value), _spread(mine[0], unit), _plain(state[0], unit)
    (part, whole), (s_part, s_whole) = mine, state
    low = part.value_low if _has(part.value_low) else part.value
    high = part.value_high if _has(part.value_high) else part.value
    w_low = whole.value_low if _has(whole.value_low) else whole.value
    w_high = whole.value_high if _has(whole.value_high) else whole.value
    spread = ""
    if (low, high, w_low, w_high) != (part.value, part.value, whole.value, whole.value):
        spread = (
            f"range {low / w_high:.1%} to {high / w_low:.1%}"
            if w_low > 0
            else "the possible range is very wide"
        )
    return f"{part.value / whole.value:.1%}", spread, f"{s_part.value / s_whole.value:.1%}"


def need_text(reason: dict, session, store: _Store, place: str) -> str:
    """One reason in words, like 'Kūpuna are 22.8% of House District 22 residents (± 1.8 points), compared
    with 20.5% statewide.'"""
    value, spread, state = _need_figures(reason, session, store)
    clause = NEEDS[reason["need"]].says.format(value=value, place=place)
    if spread:
        clause += f" ({spread})"
    return f"{clause}, {TAILS[reason['direction']].format(state=state)}."


def checked_text(p: Program, stale: bool) -> str:
    """When a person last checked the program, or a nudge to the official page once that is too long ago."""
    when = f"{p.verified:%B} {p.verified.day}, {p.verified.year}"
    if stale:
        return f"Check the official page. These details were last checked on {when}."
    return f"Checked against official pages on {when}."


def _program_row(p: Program, reasons: list[str]) -> dict:
    stale = is_stale(p)
    return {
        "id": p.program_id,
        "name": p.name,
        "description": p.description,
        "agency": p.agency,
        "url": p.url,
        "phone": p.phone,
        "phone_label": p.phone_label,
        "verified": f"{p.verified:%B} {p.verified.day}, {p.verified.year}",
        "checked": checked_text(p, stale),
        "stale": stale,
        "reasons": reasons,
    }


def matched_programs(con, raw: dict, session) -> dict:
    """The district programs tool's result laid out for the website: programs whose need figures sit above the
    state's first, each with its reasons in words, then the rest of the catalog."""
    store = _Store(con)
    place = raw["district"]["name"]
    known = {p.program_id: p for p in catalog()}
    rows = {"higher": [], "other": []}
    cited, figures = {}, set()
    for e in raw["programs"]:
        reasons = e["reasons"][:2]
        # Each reason shows the district's figure and the state's.
        figures.update(key for r in reasons for key in (tuple(r["refs"]), tuple(r["state_refs"])))
        rows["higher" if e["need"] == "higher" else "other"].append(
            _program_row(known[e["program_id"]], [need_text(r, session, store, place) for r in reasons])
        )
        for f in (session.facts[ref] for r in reasons for ref in r["refs"] + r["state_refs"]):
            name, url = store.sources.get(f.source_id, (f.source_id, ""))
            cited.setdefault(
                (name, f.vintage), {"name": name, "url": url, "year": year_text(f.vintage, store.month)}
            )
    if rows["higher"]:
        intro = (
            f"Ranked by how far {place}'s figures sit above the state's. A figure within its margin of error "
            "of the state's counts as about the same."
        )
    else:
        intro = (
            f"None of {place}'s need figures sit clearly above the state's, so no program stands out here."
        )
    return {
        "title": "Programs to explore here",
        "intro": intro,
        "programs": rows["higher"],
        "others_title": "Also available statewide" if rows["higher"] else "Available statewide",
        "others": rows["other"],
        "note": ELIGIBILITY_NOTE,
        "sources": list(cited.values()),
        "figures": len(figures),
    }


def program_rows(rule_ids: list[str]) -> list[dict]:
    """One row per program named by the rule ids an answer lists, without district reasons."""
    owners = by_rule()
    found = [owners[r] for r in rule_ids if r in owners]
    return [_program_row(p, []) for p in dict.fromkeys(found)]


# The outreach page: a chamber's districts in tiers by a group that meets a program's income test.
OUTREACH_PAGE = "/outreach.html"
CHAMBER_WORDS = {"house": "House", "senate": "Senate"}
ORDINALS = ("Top", "Second", "Third", "Fourth", "Fifth", "Sixth", "Seventh", "Eighth", "Ninth", "Tenth")
UNRANKED = "no margin of error, so it is not placed in a group"
# The page's opening sentence names each district of a top tier this small, with its figure, and counts a larger one.
NAMED_MAX = 4


def _figure(f, unit: str, inside: bool = False) -> str:
    """A fact's figure for a sentence, like '1,755 ± 268' or 'about 900 (range 800 to 1,000)'."""
    return _said({"number": _plain(f, unit), "spread": _spread(f, unit)}, inside)


def _district_path(geo: str) -> str:
    chamber, number = geo.split(":")
    return f"/district/{chamber}-{int(number)}"


def _join(items: list[str]) -> str:
    return items[0] if len(items) == 1 else f"{', '.join(items[:-1])} and {items[-1]}"


def _outreach_row(entry: dict, raw: dict, session, store: _Store) -> dict:
    """One district on the outreach page: its count as its profile shows it, a caution when the estimate is
    unreliable, and its program-use figure."""
    f = session.facts[entry["ref"]]
    unit = store.unit(f)
    row = {
        "geo": entry["geo"],
        "name": entry["geo_name"],
        "path": _district_path(entry["geo"]),
        "value": _plain(f, unit),
        "spread": _spread(f, unit),
        # True or False from the margin of error, or None when the figure has none to judge.
        "reliable": entry["reliable"],
        "caution": _caution(entry),
        "use": None,
    }
    if use := raw["beside"].get(entry["geo"]):
        u = session.facts[use["ref"]]
        row["use"] = {"value": _plain(u, store.unit(u)), "spread": _spread(u, store.unit(u))}
    return row


def _tier_note(index: int, size: int) -> str:
    """What a tier lets a reader say. Districts in a tier overlap, and every one of them is clearly below the
    highest district of the tier above, but may overlap the others there."""
    if index == 0:
        if size == 1:
            return "Its range sits above the range of every other district."
        return "Their ranges overlap, so the data cannot say which of these districts is highest."
    below = "clearly below the highest district in the group above"
    if size == 1:
        return f"It is {below}."
    return f"Their ranges overlap, so the data cannot put them in order. Each is {below}."


def _outreach_lead(top: list, store: _Store, who: str, chamber: str, total: int) -> str:
    """The page's opening sentence: the top tier and its figures."""
    word = CHAMBER_WORDS[chamber]
    if not top:
        return f"No {word} district has a figure for {who}."
    top = sorted(top, key=lambda f: f.geo_id)  # district order, since the data cannot order them
    names = [store.place(f.geo_type, f.geo_id) for f in top]
    unit = store.unit(top[0])
    if len(top) == 1:
        return f"{names[0]} has the most {who} of any {word} district: {_figure(top[0], unit)}."
    if len(top) <= NAMED_MAX:
        named = _join([f"{n} ({_figure(f, unit, inside=True)})" for n, f in zip(names, top, strict=True)])
        return f"{named} have the most {who}. Their ranges overlap, so the data cannot say which is highest."
    values = [f.value for f in top]
    low, high = _number(unit, min(values)), _number(unit, max(values))
    return (
        f"{len(top)} of {total} {word} districts share the top group for {who}, from {low} to {high} each. "
        "Their ranges overlap, so no one district stands out."
    )


def _page_sources(facts, session, store: _Store) -> list[dict]:
    """Each source behind the page's figures once, linked to the methods page when KLEAR moved its figures to
    districts or CDC modeled them. ACS tables are not linked, since each table page shows one place."""
    return _cited(
        [
            _source(f, store)
            | (
                {"links": [{"text": METHODS_TEXT, "url": f"{METHODS_PAGE}#{f.source_id}"}]}
                if session.tiers.get(f.ref) in METHOD_TIERS
                else {}
            )
            for f in facts
        ]
    )


def _unreliable_text(shaky: int, total: int) -> str:
    if not shaky:
        return ""
    if shaky == 1:
        return (
            f"1 of these {total} estimates is unreliable because its margin of error is large. It is marked."
        )
    return f"{shaky} of these {total} estimates are unreliable because their margins of error are large. Each is marked."


def _state_figure(f, store: _Store) -> dict | None:
    if f is None:
        return None
    unit = store.unit(f)
    return {"name": store.place(f.geo_type, f.geo_id), "value": _plain(f, unit), "spread": _spread(f, unit)}


def outreach(con, raw: dict, session) -> dict:
    """The outreach tool's result laid out for the outreach page: a sentence on the top tier, then each tier's
    districts in district order, since the data cannot order them, and the programs that serve the group."""
    store = _Store(con)
    spec = OUTREACH_PAIRS[raw["pair"]]
    chamber, ind = raw["chamber"], raw["indicator_id"]
    tiers = []
    for i, t in enumerate(raw["tiers"]):
        rows = sorted((_outreach_row(d, raw, session, store) for d in t["districts"]), key=lambda r: r["geo"])
        title = f"{ORDINALS[i]} group" if i < len(ORDINALS) else f"Group {i + 1}"
        tiers.append({"title": title, "note": _tier_note(i, len(rows)), "districts": rows})
    # A figure with no margin of error has no range, so the tiers cannot place it.
    unranked = [_outreach_row(d, raw, session, store) | {"reason": UNRANKED} for d in raw["unranked"]]
    missing = [
        {
            "geo": m["geo"],
            "name": store.place(*geo_key(m["geo"])),
            "path": _district_path(m["geo"]),
            "reason": m["reason"],
        }
        for m in raw["missing"]
    ]
    ranked = [session.facts[d["ref"]] for t in raw["tiers"] for d in t["districts"]]
    top = [session.facts[d["ref"]] for d in raw["tiers"][0]["districts"]] if raw["tiers"] else []
    total = len(ranked) + len(unranked) + len(missing)
    # Districts with no figure have no estimate to judge, so they are left out of the count.
    estimates = [r for t in tiers for r in t["districts"]] + unranked
    shaky = sum(r["reliable"] is False for r in estimates)
    state = session.facts[raw["state"]["ref"]] if raw["state"] else None
    use_facts = [session.facts[b["ref"]] for b in raw["beside"].values()]
    use = None
    if raw["use"]:
        about = store.meaning(raw["use"])["plain"]
        ranged = any(session.tiers.get(f.ref) in METHOD_TIERS for f in use_facts)
        use = {
            "label": store.short(raw["use"]),
            "note": " ".join(
                [
                    "This column is for comparison only. It does not set the groups.",
                    about,
                    "KLEAR estimates it for districts from ZIP code data, so it shows a range."
                    if ranged
                    else "",
                ]
            ).strip(),
        }
    known = {p.program_id: p for p in catalog()}
    return {
        "pair": raw["pair"],
        "chamber": chamber,
        "chamber_name": CHAMBER_WORDS[chamber],
        "title": spec["title"],
        "label": store.short(ind),
        "meaning": store.meaning(ind),
        "heading": f"{store.short(ind)}, by {CHAMBER_WORDS[chamber]} district",
        "lead": _outreach_lead(top, store, spec["who"], chamber, total),
        "year": store.vintage(top[0]) if top else "",
        "state": _state_figure(state, store),
        "unreliable": _unreliable_text(shaky, len(estimates)),
        "caveat": raw.get("note", ""),
        "use": use,
        "tiers": tiers,
        "unranked": unranked,
        "missing": missing,
        "programs": [_program_row(known[p], []) for p in spec["programs"] if p in known],
        "note": ELIGIBILITY_NOTE,
        "sources": _page_sources([*ranked, *([state] if state else []), *use_facts], session, store),
        "pairs": [{"id": k, "title": v["title"], "who": v["who"]} for k, v in OUTREACH_PAIRS.items()],
    }


def outreach_note(district: dict, tops: dict, session, store: _Store) -> dict:
    """The profile's short note on outreach: up to OUTREACH_NOTE_LIMIT pairs whose top tier holds the district,
    the smallest tier first, each linked to its outreach page, or a line saying the district is in none."""
    name = district["name"]
    chamber = geo_key(district["geo"])[0]
    word = CHAMBER_WORDS[chamber]
    items = []
    for t in tops["tops"][:OUTREACH_NOTE_LIMIT]:
        spec = OUTREACH_PAIRS[t["pair"]]
        f = session.facts[t["fact"]["ref"]]
        figure = _figure(f, store.unit(f))
        if t["shared"] == 1:
            text = f"{name} has the most {spec['who']} of any {word} district: {figure}."
        else:
            text = (
                f"{name} is one of {t['shared']} {word} districts in the top group for {spec['who']}, with "
                f"{figure}. Their ranges overlap."
            )
        items.append(
            {
                "text": text,
                "caution": _caution(t["fact"]),
                "link": f"Compare {word} districts for {spec['title']}",
                "url": f"{OUTREACH_PAGE}?pair={t['pair']}&chamber={chamber}",
            }
        )
    more = len(tops["tops"]) - len(items)
    return {
        "title": "Outreach opportunities",
        "items": items,
        "none": ""
        if items
        else f"{name} is not in the top group of {word} districts for any outreach measure.",
        "more": f"It is in the top group for {more} more {'measure' if more == 1 else 'measures'}."
        if more
        else "",
        "link": {"text": "Where outreach could help most", "url": f"{OUTREACH_PAGE}?chamber={chamber}"},
        "note": "Groups meet a program's income test only.",
    }


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
            places += [
                (session.facts.get(a.get("ref")), a["geo_name"], a.get("missing", "")) for a in row["against"]
            ]
            for f, place, note in places:
                if f is None:
                    # a place with no figure keeps its row and says why
                    writer.writerow(
                        [section["title"], fact["label"], place, "", "", "", "", "", "", "", "", note]
                    )
                    continue
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
