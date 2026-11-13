"""Notes for districts and counties, picked by the rules in data/places/caveats.yaml from 2020 Census blocks."""

import functools
import statistics
from dataclasses import dataclass

import yaml

from klear.config import ROOT

RULES = ROOT / "data" / "places" / "caveats.yaml"
CHAMBERS = ("house", "senate")
# Districts a typical size needs, so one or two districts are never measured against themselves.
MIN_PEERS = 3


@dataclass(frozen=True)
class Rule:
    """One caveat: its text and sources, and what picks its districts."""

    caveat_id: str
    title: str
    text: str
    sources: tuple[dict, ...]
    places: tuple[str, ...] = ()
    min_share: float = 0.0
    places_detail: str = ""
    above_median: float | None = None
    above_median_detail: str = ""
    counties: tuple[str, ...] = ()


@functools.cache
def load(path=RULES) -> tuple[dict[str, str], tuple[Rule, ...]]:
    """Island units by county and the caveat rules."""
    raw = yaml.safe_load(path.read_text(encoding="utf-8"))
    rules = []
    for e in raw["caveats"]:
        rules.append(
            Rule(
                caveat_id=e["id"],
                title=e["title"],
                text=" ".join(e["text"].split()),
                sources=tuple({"name": s["name"], "url": s["url"]} for s in e["sources"]),
                places=tuple(e.get("places", ())),
                min_share=float(e.get("min_share", 0.0)),
                places_detail=" ".join(e.get("places_detail", "").split()),
                above_median=e.get("above_median"),
                above_median_detail=" ".join(e.get("above_median_detail", "").split()),
                counties=tuple(str(c) for c in e.get("counties", ())),
            )
        )
        if not rules[-1].sources:
            raise ValueError(f"caveat {e['id']} needs at least one source")
    return {str(k): v for k, v in raw["island_units"].items()}, tuple(rules)


def _joined(names: list[str]) -> str:
    return names[0] if len(names) == 1 else f"{', '.join(names[:-1])} and {names[-1]}"


class _Blocks:
    """2020 Census counts by district and county, read once per lookup."""

    def __init__(self, con):
        self.con = con
        self._chambers: dict[str, dict[str, tuple[str, int]]] = {}

    def districts(self, chamber: str) -> dict[str, tuple[str, int]]:
        """Each district's main county and 2020 count."""
        if chamber not in self._chambers:
            rows = self.con.execute(
                f"SELECT {chamber}, arg_max(county, pop), sum(pop) FROM (SELECT {chamber}, county, sum(pop2020) pop "
                f"FROM blocks WHERE {chamber} IS NOT NULL GROUP BY 1, 2) GROUP BY 1"
            ).fetchall()
            self._chambers[chamber] = {g: (c, int(p)) for g, c, p in rows}
        return self._chambers[chamber]

    def county_count(self, county: str) -> int:
        """A county's 2020 count."""
        return int(
            self.con.execute(
                "SELECT coalesce(sum(pop2020), 0) FROM blocks WHERE county = ?", [county]
            ).fetchone()[0]
        )

    def in_counties(self, chamber: str, geo_id: str, counties: tuple[str, ...]) -> list[str]:
        """Which of these counties hold 2020 residents of a district."""
        rows = self.con.execute(
            f"SELECT DISTINCT county FROM blocks WHERE {chamber} = ? AND pop2020 > 0 AND county IN (SELECT unnest(?))",
            [geo_id, list(counties)],
        ).fetchall()
        return [r[0] for r in rows]

    def places(self, chamber: str, geo_id: str, names: tuple[str, ...]) -> list[tuple[str, int]]:
        """Named Census places with 2020 residents in a district, largest first."""
        return self.con.execute(
            "SELECT p.name, sum(o.pop2020) FROM place_overlap o JOIN place p USING (place_id) "
            "WHERE o.geo_type = ? AND o.geo_id = ? AND o.pop2020 > 0 AND p.name IN (SELECT unnest(?)) "
            "GROUP BY 1 ORDER BY 2 DESC, 1",
            [chamber, geo_id, list(names)],
        ).fetchall()


def _district(rule: Rule, blocks: _Blocks, units: dict[str, str], chamber: str, geo_id: str) -> str | None:
    """The rule's text for one district, with the detail lines its matching parts add, or None."""
    county, count = blocks.districts(chamber).get(geo_id, (None, 0))
    if not count:
        return None
    details, hit = [], False
    if rule.counties and blocks.in_counties(chamber, geo_id, rule.counties):
        hit = True
    if rule.places:
        found = blocks.places(chamber, geo_id, rule.places)
        if found and sum(p for _, p in found) >= rule.min_share * count:
            hit = True
            if rule.places_detail:
                details.append(rule.places_detail.format(places=_joined([n for n, _ in found])))
    if rule.above_median is not None and county in units:
        unit = units[county]
        peers = [p for c, p in blocks.districts(chamber).values() if units.get(c) == unit]
        typical = statistics.median(peers)
        if len(peers) >= MIN_PEERS and count > typical * (1 + rule.above_median):
            hit = True
            if rule.above_median_detail:
                details.append(
                    rule.above_median_detail.format(
                        count=f"{count:,}",
                        typical=f"{round(typical, -2):,.0f}",
                        group=f"{unit} {chamber.title()}",
                    )
                )
    return " ".join([_base(rule, blocks), *details]) if hit else None


def _base(rule: Rule, blocks: _Blocks) -> str:
    """The rule's text without detail lines."""
    return rule.text.format(
        county_count=f"{blocks.county_count(rule.counties[0]):,}" if rule.counties else ""
    )


def for_places(con, geos) -> list[dict]:
    """Caveats for the districts and counties among geos, in rule order: id, title, text, sources and geo."""
    units, rules = load()
    blocks = _Blocks(con)
    out = []
    for rule in rules:
        for geo_type, geo_id in dict.fromkeys(tuple(g) for g in geos):
            if geo_type in CHAMBERS:
                text = _district(rule, blocks, units, geo_type, geo_id)
            elif geo_type == "county" and geo_id in rule.counties and blocks.county_count(geo_id):
                text = _base(rule, blocks)
            else:
                text = None
            if text:
                out.append(
                    {
                        "id": rule.caveat_id,
                        "geo": f"{geo_type}:{geo_id}",
                        "title": rule.title,
                        "text": text,
                        "sources": [dict(s) for s in rule.sources],
                    }
                )
    return out


def grouped(con, geos) -> list[dict]:
    """One entry per caveat that applies to any of geos: title, text without detail lines, and the geos."""
    rules = {r.caveat_id: r for r in load()[1]}
    blocks, out = _Blocks(con), {}
    for c in for_places(con, geos):
        entry = out.setdefault(
            c["id"], {"title": c["title"], "text": _base(rules[c["id"]], blocks), "geos": []}
        )
        entry["geos"].append(c["geo"])
    return list(out.values())


def picked(con) -> dict[str, list[str]]:
    """Every district and county each caveat applies to, as TYPE:ID, by caveat id."""
    geos = [(c, g) for c in CHAMBERS for g in _Blocks(con).districts(c)]
    geos += [("county", c) for r in load()[1] for c in r.counties]
    out: dict[str, list[str]] = {}
    for c in for_places(con, sorted(geos)):
        out.setdefault(c["id"], []).append(c["geo"])
    return out
