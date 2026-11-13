# Deterministic tools the KLEAR agent calls, plus the session that numbers and remembers what they return.
import json
import math
import re
from typing import NamedTuple

import duckdb

from klear import caveats
from klear.agent import config
from klear.etl.places_gazetteer import plain_name, search_plain
from klear.eval.contract import FactRef, fill_value, geo_display, geo_key, short_name
from klear.eval.scorers import counties, program_names, reliability
from klear.labels import plain_labels
from klear.programs import ELIGIBILITY_NOTE, NEEDS, Program, catalog
from klear.rules.registry import load_rules
from klear.store.db import MOE_STATUSES, SITE_KINDS, VALUE_STATUSES
from klear.store.methods import fact_method
from klear.web.geocode import NOTES as ADDRESS_NOTES
from klear.web.geocode import GeocoderError, looks_like_address, readable

CHAMBERS = {
    "house": ("h", "hd", "hse", "house"),
    "senate": ("s", "sd", "sen", "senate"),
}
DISTRICT_WORDS = ("d", "dis", "dist", "district", "no")
ZIP = re.compile(r"^(?:zip(?:\s*code)?\s*)?(\d{5})$")
SITE_INDICATORS = {k: f"sites_{k}" for k in SITE_KINDS}
UNIT_INDICATORS = {"public_housing": "units_public_housing", "lihtc": "units_lihtc"}


def _fuzzy_limit(plain: str) -> int:
    n = len(plain)
    if " " in plain or n < 5 or plain in config.STOP_WORDS:
        return 0
    return 1 if n < 8 else 2


def display_name(program: str) -> str:
    """Program name without parenthesized parts that hold digits, such as a tax year."""
    return re.sub(r"\s*\([^)]*\d[^)]*\)", "", program).strip()


class ToolError(ValueError):
    pass


def edit_distance(a: str, b: str) -> int:
    prev = list(range(len(b) + 1))
    for i, ca in enumerate(a, 1):
        cur = [i]
        for j, cb in enumerate(b, 1):
            cur.append(min(prev[j] + 1, cur[j - 1] + 1, prev[j - 1] + (ca != cb)))
        prev = cur
    return prev[-1]


def _near(word: str, target: str) -> bool:
    return len(word) >= 4 and edit_distance(word, target) <= config.MAX_EDIT_DISTANCE


def _chamber(word: str) -> str | None:
    for name, forms in CHAMBERS.items():
        if word in forms or _near(word, name):
            return name
    return None


STATE_HEAD = re.compile(r"^(?:the\s+)?(?:state\s+of\s+)?(?:hawaiis?|hi)(?:\s+state)?\b")
STATE_TAIL = re.compile(r"\b(?:in\s+)?(?:the\s+)?(?:state\s+of\s+)?(?:hawaii|hi)(?:\s+state)?$")
# Words that, with or without the state's name, mean the whole state: statewide, the entire state, all of Hawaiʻi.
STATE_WORDS = frozenset((
    "the", "a", "as", "of", "in", "across", "all", "whole", "entire", "overall", "total",
    "state", "states", "statewide", "wide", "hawaii", "hawaiis", "hi",
))  # fmt: skip
NO_MATCH_HINT = (
    "No match. Give a district like House District 22 or Senate District 12, a place name, or a ZIP code."
)
ADDRESS_FOUND = (
    "The Census geocoder placed this street address in these districts. A street address is a place, not a "
    "person: answer for its district with refusal none."
)
ADDRESS_HINT = (
    "Say this plainly with refusal none, write please give a place, ZIP code or district, and never pick a "
    "district from the town name."
)
NO_ZIP_NOTE = (
    "ZIP code {} has no area with residents in Hawaiʻi. Home ZIP codes there start with 967 or 968, "
    "and PO box ZIP codes have no area."
)
# A search for one exact result, as a picked suggestion sends it: place:1523000, area:nb09 or county:15003.
ID_FORM = re.compile(r"^\s*(place|area|county):([0-9a-z]+)\s*$", re.IGNORECASE)
# What search suggests while someone types: at most this many, once they have typed this many characters.
SUGGEST_LIMIT = 8
SUGGEST_MIN = 2
# A chamber and the start of a district number, as typed: h2, hd 2, house 2, sen 1, senate district 1.
DISTRICT_START = re.compile(r"^(h|hd|hse|house|s|sd|sen|senate)\s*(?:d|dist|district)?\s*(\d{1,2})$")
KIND_LABELS = {
    "place": "Town",
    "island": "Island",
    "neighborhood": "Neighborhood",
    "division": "County division",
    "county": "County",
}
# When an island, Census place, neighborhood board area and county division share a name on one island,
# search keeps the first of these.
KIND_ORDER = {"island": 0, "place": 1, "neighborhood": 2, "division": 3}


class _Named(NamedTuple):
    kind: str
    id: str
    name: str
    plain: str
    pop: int
    island: str | None


def _dedupe(names: list[_Named]) -> list[_Named]:
    """Each name once, dropping an area that repeats a higher kind's name on the same island."""
    first: dict[tuple[str, str | None], str] = {}
    out, seen = [], set()
    for n in sorted(names, key=lambda n: KIND_ORDER[n.kind]):
        if first.setdefault((n.plain, n.island), n.kind) != n.kind or (n.kind, n.id) in seen:
            continue
        seen.add((n.kind, n.id))
        out.append(n)
    return out


def _strip_state(text: str, head: bool = True) -> str:
    """Plain text without a trailing Hawaiʻi state name, and without a leading one when head is set."""
    plain = re.sub(r"[^a-z0-9:]+", " ", plain_name(text)).strip()
    return STATE_TAIL.sub("", STATE_HEAD.sub("", plain) if head else plain).strip()


def _statewide(text: str) -> bool:
    """Whether text names the whole state, such as statewide or Hawaiʻi overall."""
    words = re.findall(r"[a-z]+", plain_name(text))
    return bool(words) and set(words) <= STATE_WORDS and bool({"state", "statewide", "hawaii"} & set(words))


def _district_form(text: str) -> tuple[str, str] | None:
    tokens = re.findall(r"[a-z]+|\d+", _strip_state(text))
    if len(tokens) not in (2, 3) or not tokens[-1].isdigit() or not all(t.isalpha() for t in tokens[:-1]):
        return None
    chamber = _chamber(tokens[0])
    if chamber is None or len(tokens[-1]) > 3:
        return None
    if len(tokens) == 3 and tokens[1] not in DISTRICT_WORDS and not _near(tokens[1], "district"):
        return None
    return chamber, f"{int(tokens[-1]):03}"


def _geo_arg(text) -> tuple[str, str]:
    key = geo_key(text) if isinstance(text, str) else None
    if key is None:
        raise ToolError(f"geo must look like TYPE:ID, for example house:022, got {text!r}")
    geo_type, geo_id = key
    if geo_type in ("house", "senate") and geo_id.isdigit():
        geo_id = f"{int(geo_id):03}"
    return geo_type, geo_id


def _str(args: dict, key: str) -> str:
    v = args.get(key)
    if not isinstance(v, str) or not v.strip():
        raise ToolError(f"{key} must be a non-empty string")
    return v


def _strs(args: dict, key: str, optional: bool = False) -> list[str] | None:
    v = args.get(key)
    if v is None and optional:
        return None
    if not isinstance(v, list) or not v or not all(isinstance(x, str) and x.strip() for x in v):
        raise ToolError(f"{key} must be a non-empty list of strings")
    return v


def _tokens(text: str) -> set[str]:
    return {
        w[:-1] if len(w) > 3 and w.endswith("s") else w for w in re.findall(r"[a-z0-9]+", plain_name(text))
    }


class Session:
    def __init__(self, con, geocoder=None):
        # geocoder finds the Census blocks of a street address, or is None when address lookup is off
        self.con, self.geocoder = con, geocoder
        self.facts: dict[str, FactRef] = {}
        self.places: set[str] = set()
        self.geos: set[tuple[str, str]] = set()
        self.resolved: set[tuple[str, str]] = set()
        self.context: tuple[str, str] | None = None
        self.question = ""
        self.tiers: dict[str, str] = {}
        self.unreliable: set[str] = set()
        self.directions: list[tuple[str, str, str]] = []
        self._refs: dict[tuple[str, str, str, str], str] = {}
        self._rules = None
        self._notes: dict[str, str] | None = None
        self._prefetched: dict[tuple[str, str, str], list[tuple]] = {}
        self._areas: bool | None = None
        self._named: list[_Named] | None = None
        self._aliases: dict[str, list[_Named]] = {}
        self._alias_names: dict[str, str] = {}
        self._display_names: dict[tuple[str, str], str | None] = {}

    def run(self, tool: str, args: dict) -> str:
        try:
            fn = getattr(self, f"_{tool}", None) if tool in TOOL_NAMES else None
            if fn is None:
                raise ToolError(f"unknown tool {tool!r}, expected one of {list(TOOL_NAMES)}")
            if not isinstance(args, dict):
                raise ToolError("arguments must be an object")
            out = fn(args)
        except ToolError as e:
            out = {"error": str(e)}
        except Exception as e:  # noqa: BLE001
            out = {"error": f"{tool} failed: {type(e).__name__}: {e}"}
        return json.dumps(out, ensure_ascii=False)

    # geography helpers

    def _name(self, geo_type: str, geo_id: str) -> str | None:
        """A geography's display name, read once per session: rankings name every district of a chamber."""
        key = (geo_type, geo_id)
        if key not in self._display_names:
            row = self.con.execute(
                "SELECT name FROM geography WHERE geo_type = ? AND geo_id = ?", [geo_type, geo_id]
            ).fetchone()
            self._display_names[key] = geo_display(geo_type, geo_id, row[0]) if row else None
        return self._display_names[key]

    def _geo(self, geo_type: str, geo_id: str, **extra) -> dict:
        self.geos.add((geo_type, geo_id))
        return {"geo": f"{geo_type}:{geo_id}", "name": self._name(geo_type, geo_id) or geo_id, **extra}

    def _overlap_entry(self, geo_type: str, geo_id: str, share: float, pop: int) -> dict:
        self.resolved.add((geo_type, geo_id))
        return self._geo(geo_type, geo_id, share=round(share, 4), pop2020=pop)

    # resolve_place

    def _resolve_place(self, args: dict) -> dict:
        text = _str(args, "text")
        if m := ID_FORM.match(text):
            return self._resolve_id(m.group(1).lower(), m.group(2).lower())
        if d := _district_form(text):
            if self._name(*d):
                self.resolved.add(d)
                return {"matches": [{"kind": "district", **self._geo(*d)}]}
            return {"matches": [], "note": self._range_note(d[0]), "hint": NO_MATCH_HINT}
        if zipm := ZIP.match(text.strip().casefold()):
            if self._name("zcta", zipm.group(1)):
                return {"matches": [self._zcta_match(zipm.group(1))]}
            return {"matches": [], "note": NO_ZIP_NOTE.format(zipm.group(1)), "hint": NO_MATCH_HINT}
        if looks_like_address(text):
            return self.find_address(text)
        return self.resolve_name(text)

    def resolve_name(self, text: str) -> dict:
        """Census places, areas, counties or the state by name."""
        key = search_plain(text)
        if found := self._exact(key):
            return {"matches": self._name_matches(found)}
        if county := config.COUNTY_NAMES.get(key) or config.COUNTY_NAMES.get(plain_name(text)):
            return {"matches": [self._county_match(county)]}
        if not _strip_state(text) or _statewide(text):
            self.resolved.add(("state", "15"))
            return {"matches": [{"kind": "state", **self._geo("state", "15")}]}
        bare = search_plain(_strip_state(text, head=False))
        found = self._near(key) or (bare != key and (self._exact(bare) or self._near(bare))) or []
        return {"matches": self._name_matches(found)} if found else {"matches": [], "hint": NO_MATCH_HINT}

    def _resolve_id(self, kind: str, id_: str) -> dict:
        """Exactly one Census place, area or county, by its id."""
        if kind == "county":
            found = [self._county_match(id_)] if self._name("county", id_) else []
        else:
            found = self._name_matches(
                [n for n in self._names() if (n.kind == "place") == (kind == "place") and n.id == id_]
            )
        return {"matches": found} if found else {"matches": [], "hint": NO_MATCH_HINT}

    def _range_note(self, chamber: str) -> str:
        n = self.con.execute("SELECT count(*) FROM geography WHERE geo_type = ?", [chamber]).fetchone()[0]
        return f"Hawaiʻi has {n} {chamber.title()} districts, numbered 1 to {n}."

    def _has_areas(self) -> bool:
        """Whether the store has the area tables; stores built before them search Census places only."""
        if self._areas is None:
            self._areas = bool(
                self.con.execute(
                    "SELECT count(*) FROM information_schema.tables WHERE table_name = 'area_alias'"
                ).fetchone()[0]
            )
        return self._areas

    def _island_of(self, geo_type: str, geo_id: str) -> str | None:
        """The island holding most of a ZIP code area's or Census place's residents."""
        if not self._has_areas():
            return None
        row = self.con.execute(
            "SELECT a.name FROM area_overlap o JOIN area a USING (area_id) WHERE a.kind = 'island' "
            "AND o.geo_type = ? AND o.geo_id = ? ORDER BY o.pop2020 DESC, a.name LIMIT 1",
            [geo_type, geo_id],
        ).fetchone()
        return row[0] if row else None

    def _overlaps(self, table: str, column: str, value: str) -> list[dict]:
        rows = self.con.execute(
            f"SELECT geo_type, geo_id, pop2020, share FROM {table} "
            f"WHERE {column} = ? AND geo_type IN ('house', 'senate', 'county') "
            "ORDER BY geo_type = 'county', geo_type, share DESC, geo_id",
            [value],
        ).fetchall()
        return [self._overlap_entry(t, i, s, int(p)) for t, i, p, s in rows]

    def _block_overlaps(self, column: str, value: str) -> tuple[int, list[dict]]:
        """2020 residents with this block column value, and their shares in each district and county."""
        total = self.con.execute(f"SELECT sum(pop2020) FROM blocks WHERE {column} = ?", [value]).fetchone()[0]
        if not total:
            return 0, []
        rows = []
        for geo_type in ("house", "senate", "county"):
            rows += self.con.execute(
                f"SELECT '{geo_type}', {geo_type}, sum(pop2020) FROM blocks "
                f"WHERE {column} = ? AND pop2020 > 0 AND {geo_type} IS NOT NULL GROUP BY 2 ORDER BY 2",
                [value],
            ).fetchall()
        return int(total), [self._overlap_entry(t, i, p / total, int(p)) for t, i, p in rows]

    def _zcta_match(self, zcta: str) -> dict:
        self.resolved.add(("zcta", zcta))
        _, overlaps = self._block_overlaps("zcta", zcta)
        island = self._island_of("zcta", zcta)
        return {"kind": "zcta", **self._geo("zcta", zcta), "island": island, "overlaps": overlaps}

    def _county_match(self, county: str) -> dict:
        self.resolved.add(("county", county))
        pop, overlaps = self._block_overlaps("county", county)
        overlaps = [o for o in overlaps if not o["geo"].startswith("county:")]
        return {"kind": "county", **self._geo("county", county), "pop2020": pop, "overlaps": overlaps}

    def find_address(self, text: str) -> dict:
        """Districts of a street address through the geocoder, or no matches with a note on why."""
        outcome = "off" if self.geocoder is None else "none"
        found = []
        if self.geocoder is not None:
            try:
                found = self.geocoder(text)
            except GeocoderError:
                outcome = "failed"
        matches = [m for block, label in found if (m := self.block_match(block, readable(label, text)))]
        self._places_named(" ".join([text, *(m["name"] for m in matches)]))
        if matches:
            return {"matches": matches, "note": ADDRESS_FOUND}
        return {"matches": [], "address": outcome, "note": ADDRESS_NOTES[outcome], "hint": ADDRESS_HINT}

    def _places_named(self, text: str) -> None:
        """Remember the Census places an address names, so an answer may name its town."""
        words = f" {search_plain(text)} "
        for name, plain in self.con.execute("SELECT name, plain FROM place").fetchall():
            if plain and f" {plain} " in words:
                self.places.add(name)

    def block_match(self, block: str, label: str) -> dict | None:
        """Districts and county of one 2020 Census block, for a street address the website geocoded to it."""
        row = self.con.execute(
            "SELECT county, zcta, house, senate FROM blocks WHERE block = ?", [block]
        ).fetchone()
        if row is None or row[2] is None or row[3] is None:
            return None
        county, zcta, house, senate = row
        geos = [("house", house), ("senate", senate), ("county", county)]
        self.resolved.update(geos)
        island = self._island_of("zcta", zcta) if zcta else None
        return {"kind": "address", "name": label, "island": island, "overlaps": [self._geo(*g) for g in geos]}

    def _names(self) -> list[_Named]:
        """Census places, then islands, neighborhood board areas and county divisions, each with its island."""
        if self._named is not None:
            return self._named
        places = self.con.execute(
            "SELECT place_id, name, plain, pop2020 FROM place ORDER BY name, place_id"
        ).fetchall()
        if not self._has_areas():
            self._named = [_Named("place", i, n, search_plain(p), int(pop), None) for i, n, p, pop in places]
            return self._named
        islands = dict(
            self.con.execute(
                "SELECT o.geo_id, arg_max(a.name, o.pop2020) FROM area_overlap o JOIN area a USING (area_id) "
                "WHERE a.kind = 'island' AND o.geo_type = 'place' GROUP BY 1"
            ).fetchall()
        )
        rows = [_Named("place", i, n, search_plain(p), int(pop), islands.get(i)) for i, n, p, pop in places]
        rows += [
            _Named(k, i, n, p, int(pop), island)
            for i, k, n, p, island, pop in self.con.execute(
                "SELECT area_id, kind, name, plain, island, pop2020 FROM area ORDER BY name, area_id"
            ).fetchall()
        ]
        by_id = {(r.kind == "place", r.id): r for r in rows}
        for plain, name, target_type, target_id in self.con.execute(
            "SELECT plain, name, target_type, target_id FROM area_alias ORDER BY plain, target_type, target_id"
        ).fetchall():
            if (hit := by_id.get((target_type == "place", target_id))) is not None:
                self._aliases.setdefault(plain, []).append(hit)
                self._alias_names[plain] = name
        self._named = rows
        return rows

    def _exact(self, key: str) -> list[_Named]:
        if not key:
            return []
        return _dedupe([n for n in self._names() if n.plain == key] + self._aliases.get(key, []))

    def _near(self, key: str) -> list[_Named]:
        """Names that start with key as a word, then as letters, then names holding all its words, then the
        closest spellings."""
        names = self._names()
        for prefix in (key + " ", key):
            if len(key) >= 3 and (hit := [n for n in names if n.plain.startswith(prefix)]):
                return _dedupe(hit)
        words = set(key.split())
        if (
            len(key) >= 3
            and key not in config.STOP_WORDS
            and (hit := [n for n in names if words <= set(n.plain.split())])
        ):
            return _dedupe(hit)
        limit = _fuzzy_limit(key)
        if limit == 0:
            return []
        pool = [(n.plain, [n]) for n in names] + list(self._aliases.items())
        dist = [(edit_distance(key, p), hits) for p, hits in pool]
        best = min((d for d, _ in dist), default=99)
        return _dedupe([n for d, hits in dist if d == best for n in hits]) if best <= limit else []

    def _name_matches(self, found: list[_Named]) -> list[dict]:
        out = []
        for n in found:
            self.places.add(n.name)
            table, column = (
                ("place_overlap", "place_id") if n.kind == "place" else ("area_overlap", "area_id")
            )
            overlaps = self._overlaps(table, column, n.id)
            if n.kind == "island":
                for o in overlaps:
                    if o["geo"].startswith("county:"):
                        whole = self.con.execute(
                            "SELECT sum(pop2020) FROM blocks WHERE county = ?", [o["geo"].split(":")[1]]
                        ).fetchone()[0]
                        o["of_county"] = round(o["pop2020"] / whole, 4)
            out.append(
                {
                    "kind": n.kind,
                    "name": n.name,
                    f"{'place' if n.kind == 'place' else 'area'}_id": n.id,
                    "island": n.island,
                    "pop2020": n.pop,
                    "overlaps": overlaps,
                }
            )
        return out

    # suggestions for the website's search box (not an agent tool)

    def suggest(self, text: str, limit: int = SUGGEST_LIMIT) -> list[dict]:
        """What search can find that starts with the text typed so far: ZIP codes for digits, districts for a
        chamber and digits, else names. Each has a label, a detail line and the exact query that finds it."""
        key = search_plain(text)
        if len(key) < SUGGEST_MIN:
            return []
        if key.isdigit():
            return self._suggest_zips(key, limit)
        if m := DISTRICT_START.match(key):
            return self._suggest_districts(_chamber(m.group(1)) or "house", m.group(2), limit)
        return self._suggest_names(key, limit)

    def _main_islands(self, geo_type: str) -> dict[str, str]:
        if not self._has_areas():
            return {}
        return dict(
            self.con.execute(
                "SELECT o.geo_id, arg_max(a.name, o.pop2020) FROM area_overlap o JOIN area a USING (area_id) "
                "WHERE a.kind = 'island' AND o.geo_type = ? GROUP BY 1",
                [geo_type],
            ).fetchall()
        )

    def _suggest_zips(self, digits: str, limit: int) -> list[dict]:
        islands = self._main_islands("zcta")
        rows = self.con.execute(
            "SELECT geo_id FROM geography WHERE geo_type = 'zcta' AND starts_with(geo_id, ?) ORDER BY geo_id LIMIT ?",
            [digits, limit],
        ).fetchall()
        return [
            {"label": f"ZIP code {z}", "detail": islands.get(z) or "ZIP code", "query": z, "kind": "zcta"}
            for (z,) in rows
        ]

    def _suggest_districts(self, chamber: str, digits: str, limit: int) -> list[dict]:
        islands = self._main_islands(chamber)
        ids = [
            r[0]
            for r in self.con.execute(
                "SELECT geo_id FROM geography WHERE geo_type = ? ORDER BY geo_id", [chamber]
            ).fetchall()
        ]
        word = chamber.title()
        hits = [i for i in ids if i.isdigit() and str(int(i)).startswith(digits.lstrip("0") or "0")]
        return [
            {
                "label": f"{word} District {int(i)}",
                "detail": islands.get(i) or f"{word} district",
                "query": f"{word} {int(i)}",
                "kind": chamber,
            }
            for i in hits[:limit]
        ]

    def _suggest_names(self, key: str, limit: int) -> list[dict]:
        """Names whose first word starts with key, then names with a later word that does. Within each, towns,
        islands, neighborhoods, divisions and other names for whole regions come first, then counties, then
        other names for a part of one area such as Lanikai, each most residents first."""

        def rank(plain: str) -> int | None:
            if plain.startswith(key):
                return 0
            return 1 if f" {key}" in f" {plain}" else None

        found = []  # (rank, group, -residents, label, suggestion)
        for n in _dedupe([n for n in self._names() if rank(n.plain) is not None]):
            detail = " · ".join(
                x for x in (n.island if n.island != n.name else None, KIND_LABELS[n.kind]) if x
            )
            query = f"{'place' if n.kind == 'place' else 'area'}:{n.id}"
            item = {"label": n.name, "detail": detail, "query": query, "kind": n.kind}
            found.append((rank(n.plain), 0, -n.pop, n.name, item))
        for geo_id, name in self.con.execute(
            "SELECT geo_id, name FROM geography WHERE geo_type = 'county' ORDER BY geo_id"
        ).fetchall():
            label = geo_display("county", geo_id, name)
            if (r := rank(search_plain(label))) is not None:
                pop = self.con.execute(
                    "SELECT sum(pop2020) FROM blocks WHERE county = ?", [geo_id]
                ).fetchone()[0]
                item = {
                    "label": label,
                    "detail": KIND_LABELS["county"],
                    "query": f"county:{geo_id}",
                    "kind": "county",
                }
                found.append((r, 1, -(pop or 0), label, item))
        # Other names, once for each set of places they stand for: Upcountry and Upcountry Maui are one entry.
        seen = set()
        for plain in sorted(
            (p for p in self._aliases if rank(p) is not None), key=lambda p: (rank(p), len(p), p)
        ):
            targets = self._aliases[plain]
            ids = frozenset((t.kind, t.id) for t in targets)
            if ids in seen:
                continue
            seen.add(ids)
            islands = list(dict.fromkeys(t.island for t in targets if t.island))
            whole = len(targets) > 1 or targets[0].kind == "island" or targets[0].plain == plain
            what = (
                f"{len(targets)} areas"
                if len(targets) > 1
                else KIND_LABELS["island"]
                if targets[0].kind == "island"
                else f"in {targets[0].name}"
            )
            label = self._alias_names.get(plain, plain)
            item = {"label": label, "detail": " · ".join([*islands, what]), "query": plain, "kind": "alias"}
            # A name for part of one area, such as Lanikai in Kailua, has fewer residents than the whole area.
            found.append((rank(plain), 0 if whole else 2, -sum(t.pop for t in targets), label, item))
        found.sort(key=lambda f: f[:4])
        return [f[4] for f in found[:limit]]

    # search_indicators

    def _search_indicators(self, args: dict) -> dict:
        words = _strs(args, "words")
        query: set[str] = set()
        for w in words:
            for t in _tokens(w):
                query |= set(config.ALIASES.get(t, (t,)))
        rows = self.con.execute(
            "SELECT indicator_id, label, unit FROM indicator ORDER BY indicator_id"
        ).fetchall()
        scored = [
            (len(query & _tokens(f"{i} {label}".replace("_", " "))), i, label, u) for i, label, u in rows
        ]
        scored = sorted((s for s in scored if s[0] > 0), key=lambda s: (-s[0], s[1]))[: config.SEARCH_LIMIT]
        plain = plain_labels()
        out = []
        for _, i, label, unit in scored:
            types = self.con.execute(
                "SELECT geo_type FROM indicator_coverage WHERE indicator_id = ? ORDER BY geo_type", [i]
            ).fetchall()
            entry = {"indicator_id": i, "label": label, "unit": unit, "geo_types": [t[0] for t in types]}
            if i in plain:
                entry["meaning"] = plain[i].plain
            if self._modeled(i):
                entry["method"] = "modeled estimate"
            out.append(entry)
        return {"indicators": out}

    def _modeled(self, indicator_id: str) -> bool:
        row = self.con.execute(
            "SELECT 1 FROM fact WHERE indicator_id = ? AND tier = 'M' LIMIT 1", [indicator_id]
        ).fetchone()
        return row is not None

    # get_facts

    def _ref(self, key: tuple[str, str, str, str], make) -> str:
        if key not in self._refs:
            ref = f"f{len(self._refs) + 1}"
            self._refs[key] = ref
            self.facts[ref] = make(ref)
        return self._refs[key]

    _ROW_SQL = (
        "SELECT i.label, i.unit, f.value, f.moe, f.value_status, f.moe_status, f.value_low, f.value_high, "
        "f.source_id, s.name, s.vintage, f.tier, f.indicator_id, f.geo_type, f.geo_id FROM fact f "
        "JOIN indicator i USING (indicator_id) JOIN source s USING (source_id) "
    )

    def _rows(self, indicator_id: str, geo: tuple[str, str]) -> list[tuple]:
        if (indicator_id, *geo) in self._prefetched:
            return self._prefetched[(indicator_id, *geo)]
        rows = self.con.execute(
            self._ROW_SQL
            + "WHERE f.indicator_id = ? AND f.geo_type = ? AND f.geo_id = ? ORDER BY f.source_id",
            [indicator_id, *geo],
        ).fetchall()
        return [r[:12] for r in rows]

    def _prefetch(self, indicator_ids, geos) -> None:
        """Read the rows for many indicators and geographies in one query, for _rows to serve."""
        keys = [(i, *g) for i in indicator_ids for g in geos]
        for key in keys:
            self._prefetched.setdefault(key, [])
        rows = self.con.execute(
            self._ROW_SQL
            + "WHERE f.indicator_id IN (SELECT unnest(?)) AND (f.geo_type || ':' || f.geo_id) IN "
            "(SELECT unnest(?)) ORDER BY f.source_id",
            [list(indicator_ids), [f"{g[0]}:{g[1]}" for g in geos]],
        ).fetchall()
        for r in rows:
            self._prefetched[(r[12], r[13], r[14])].append(r[:12])

    def _note(self, indicator_id: str) -> str | None:
        if self._notes is None:
            try:
                rows = self.con.execute(
                    "SELECT indicator_id, note FROM indicator WHERE note IS NOT NULL"
                ).fetchall()
            except duckdb.BinderException:
                rows = []
            self._notes = dict(rows)
        return self._notes.get(indicator_id)

    def _entry(self, indicator_id: str, geo: tuple[str, str], row: tuple) -> dict:
        label, unit, value, moe, vstatus, mstatus, low, high, source_id, source, vintage, tier = row
        ref = self._ref(
            (indicator_id, *geo, source_id),
            lambda r: FactRef(r, indicator_id, *geo, source_id, value, low, high, moe, vintage),
        )
        f = self.facts[ref]
        reliable = reliability(f)
        self.tiers[ref] = tier
        if reliable is False:
            self.unreliable.add(ref)
        out = {
            "ref": ref,
            "indicator_id": indicator_id,
            "label": label,
            "geo": f"{geo[0]}:{geo[1]}",
            "geo_name": self._name(*geo),
            "value_text": fill_value(f, unit),
            "vintage": vintage,
            "source": short_name(source),
            "source_id": source_id,
            "status": VALUE_STATUSES[vstatus] if vstatus != "ok" else MOE_STATUSES[mstatus],
            "method": fact_method(tier, source_id, geo[0]),
            "reliable": reliable,
        }
        if note := self._note(indicator_id):
            out["note"] = note
        if tied := config.TIED_PROGRAMS.get(indicator_id):
            names = program_names()
            out["programs"] = [{"rule_id": r, "name": display_name(names[r])} for r in tied if r in names]
        return out

    def _missing_reason(self, indicator_id: str, geo: tuple[str, str]) -> str:
        if not self.con.execute("SELECT 1 FROM indicator WHERE indicator_id = ?", [indicator_id]).fetchone():
            return "unknown indicator"
        if self._name(*geo) is None:
            return "unknown geography"
        row = self.con.execute(
            "SELECT reason FROM coverage_exclusion WHERE indicator_id = ? AND geo_type = ? AND geo_id = ?",
            [indicator_id, *geo],
        ).fetchone()
        if row:
            return row[0]
        covered = self.con.execute(
            "SELECT 1 FROM indicator_coverage WHERE indicator_id = ? AND geo_type = ?", [indicator_id, geo[0]]
        ).fetchone()
        if not covered:
            return f"not published for {geo[0]} geographies"
        return "no value stored, the source did not publish it or it is suppressed"

    def _facts_for(self, indicator_ids: list[str], geos: list[tuple[str, str]]) -> dict:
        facts, missing = [], []
        for geo in geos:
            for ind in indicator_ids:
                rows = self._rows(ind, geo)
                if usable := [r for r in rows if r[4] not in config.NULL_REASONS]:
                    self.geos.add(geo)
                    facts += [self._entry(ind, geo, r) for r in usable]
                else:
                    reason = config.NULL_REASONS[rows[0][4]] if rows else None
                    missing.append(
                        {
                            "indicator_id": ind,
                            "geo": f"{geo[0]}:{geo[1]}",
                            "reason": reason or self._missing_reason(ind, geo),
                        }
                    )
        return {"facts": facts, "missing": missing}

    def _get_facts(self, args: dict) -> dict:
        ids = _strs(args, "indicator_ids")
        geos = [_geo_arg(g) for g in _strs(args, "geos")]
        return self._noted(self._facts_for(ids, geos), geos)

    def _noted(self, out: dict, geos) -> dict:
        """The result with the caveats for its districts and counties, when any apply."""
        if notes := [{k: c[k] for k in ("geo", "title", "text")} for c in caveats.for_places(self.con, geos)]:
            out["place_caveats"] = notes
        return out

    # compare

    @staticmethod
    def _interval(row: tuple) -> tuple[float, float] | None:
        value, moe, low, high = row[2], row[3], row[6], row[7]
        if low is not None:
            return low, high
        return None if value is None else (value - (moe or 0), value + (moe or 0))

    def _compare(self, args: dict) -> dict:
        ind = _str(args, "indicator_id")
        geo = _geo_arg(args.get("geo"))
        base = self._facts_for([ind], [geo])
        out = {"geo": f"{geo[0]}:{geo[1]}", **base, "comparisons": []}
        rows = self._rows(ind, geo)
        mine = self._interval(rows[0]) if rows else None
        if mine is None:
            return self._noted(out, [geo])
        extra = [_geo_arg(g) for g in _strs(args, "against", optional=True) or []]
        others = (
            sorted(counties(geo, self.con)) + ([("state", "15")] if geo != ("state", "15") else []) + extra
        )
        others = [o for o in others if o != geo]
        for other in dict.fromkeys(others):
            theirs = self._facts_for([ind], [other])
            if not theirs["facts"]:
                out["missing"] += theirs["missing"]
                continue
            interval = self._interval(self._rows(ind, other)[0])
            if interval is None:
                direction = "not_comparable"
            elif mine[0] <= interval[1] and interval[0] <= mine[1]:
                direction = "within_margin"
            else:
                direction = "higher" if mine[0] > interval[1] else "lower"
            out["comparisons"].append(
                {"geo": f"{other[0]}:{other[1]}", "direction": direction, "facts": theirs["facts"]}
            )
            if base["facts"] and direction != "not_comparable":
                self.directions.append((base["facts"][0]["ref"], theirs["facts"][0]["ref"], direction))
        return self._noted(out, [geo, *others])

    # district_profile

    @staticmethod
    def _fact_interval(f: FactRef) -> tuple[float, float] | None:
        if f.value_low is not None and f.value_high is not None:
            return f.value_low, f.value_high
        return None if f.value is None else (f.value - (f.moe or 0), f.value + (f.moe or 0))

    def _against(self, ind: str, mine: dict, other: tuple[str, str], additive: bool) -> dict:
        """One comparison for a profile row: the other place's value, and the district's share of a total
        or the direction of a rate, or the reason the other place has no value."""
        found = self._facts_for([ind], [other])
        if not found["facts"]:
            reason = found["missing"][0]["reason"]
            return {"geo": f"{other[0]}:{other[1]}", "geo_name": self._name(*other), "missing": reason}
        t = found["facts"][0]
        entry = {"geo": t["geo"], "geo_name": t["geo_name"], "ref": t["ref"], "value_text": t["value_text"]}
        a, b = self.facts[mine["ref"]], self.facts[t["ref"]]
        if additive:
            if a.value is not None and b.value:
                part = a.value / b.value
                entry["share"] = "<0.1%" if 0 < part < 0.001 else f"{part:.1%}"
            return entry
        x, y = self._fact_interval(a), self._fact_interval(b)
        if x is not None and y is not None:
            entry["direction"] = (
                "within_margin" if x[0] <= y[1] and y[0] <= x[1] else "higher" if x[0] > y[1] else "lower"
            )
            self.directions.append((a.ref, b.ref, entry["direction"]))
        return entry

    def _district_profile(self, args: dict) -> dict:
        geo = _geo_arg(args.get("district"))
        if geo[0] not in ("house", "senate") or self._name(*geo) is None:
            raise ToolError("district must be a stored House or Senate district, for example house:022")
        self.resolved.add(geo)
        other = "senate" if geo[0] == "house" else "house"
        by_county = self.con.execute(
            f"SELECT county, sum(pop2020) FROM blocks WHERE {geo[0]} = ? GROUP BY 1 ORDER BY 2 DESC", [geo[1]]
        ).fetchall()
        total = sum(p for _, p in by_county) or 1
        overlaps = self.con.execute(
            f"SELECT {other}, sum(pop2020) FROM blocks WHERE {geo[0]} = ? AND pop2020 > 0 GROUP BY 1 ORDER BY 2 DESC",
            [geo[1]],
        ).fetchall()
        places = self.con.execute(
            "SELECT p.name, o.pop2020 FROM place_overlap o JOIN place p USING (place_id) "
            "WHERE o.geo_type = ? AND o.geo_id = ? AND o.pop2020 > 0 ORDER BY o.pop2020 DESC",
            list(geo),
        ).fetchall()
        additive = {
            r[0] for r in self.con.execute("SELECT indicator_id FROM indicator WHERE additive").fetchall()
        }
        against = ([("county", by_county[0][0])] if by_county else []) + [("state", "15")]
        beside = sorted({b for b, _ in config.PROFILE_BESIDE.values() if b})
        self._prefetch([i for _, ids in config.PROFILE_SECTIONS for i in ids] + beside, [geo, *against])
        out = {
            "district": self._geo(*geo),
            "counties": [self._geo("county", c, share=round(p / total, 4)) for c, p in by_county if p],
            "places": [{"name": n, "share": round(p / total, 4)} for n, p in places],
            "overlaps": [self._geo(other, g, share=round(p / total, 4)) for g, p in overlaps],
            "sections": [],
            "missing": [],
        }
        for title, ids in config.PROFILE_SECTIONS:
            rows = []
            for ind in ids:
                mine = self._facts_for([ind], [geo])
                out["missing"] += mine["missing"]
                if mine["facts"]:
                    fact = mine["facts"][0]
                    row = {
                        "fact": fact,
                        "against": [self._against(ind, fact, g, ind in additive) for g in against],
                    }
                    if extra := self._beside(ind, geo):
                        row["beside"] = extra
                    rows.append(row)
            out["sections"].append({"title": title, "rows": rows})
        return self._noted(out, [geo])

    def _beside(self, ind: str, geo: tuple[str, str]) -> dict | None:
        """The figure a profile row is shown next to, with how the two differ."""
        if ind not in config.PROFILE_BESIDE:
            return None
        other, text = config.PROFILE_BESIDE[ind]
        if other is None:
            return {"text": text}
        facts = self._facts_for([other], [geo])["facts"]
        return {"fact": facts[0], "text": text} if facts else None

    # rank_districts

    def _county_arg(self, value) -> tuple[str, str] | None:
        if value is None:
            return None
        if not isinstance(value, str) or not value.strip():
            raise ToolError("county must be a county as county:ID or a county name, for example county:15009")
        geo = geo_key(value) if ":" in value else None
        if geo is None:
            county = config.COUNTY_NAMES.get(plain_name(value)) or config.COUNTY_NAMES.get(
                plain_name(f"{value} county")
            )
            geo = ("county", county) if county else None
        if geo is None or geo[0] != "county" or self._name(*geo) is None:
            raise ToolError(f"unknown county {value!r}, give it as county:ID, for example county:15009")
        return geo

    def _districts(self, chamber: str, county: tuple[str, str] | None) -> list[tuple[str, str]]:
        """Districts of a chamber, or those with most of their 2020 population in the county."""
        if county is None:
            rows = self.con.execute(
                "SELECT geo_id FROM geography WHERE geo_type = ? ORDER BY geo_id", [chamber]
            ).fetchall()
        else:
            rows = self.con.execute(
                f"SELECT {chamber} FROM blocks WHERE {chamber} IS NOT NULL GROUP BY 1 "
                "HAVING sum(CASE WHEN county = ? THEN pop2020 ELSE 0 END) * 2 > sum(pop2020) ORDER BY 1",
                [county[1]],
            ).fetchall()
        return [(chamber, r[0]) for r in rows]

    def _rank_districts(self, args: dict) -> dict:
        ind = _str(args, "indicator")
        chamber = args.get("chamber")
        if chamber not in config.CHAMBER_TYPES:
            raise ToolError(f"chamber must be one of {list(config.CHAMBER_TYPES)}")
        row = self.con.execute("SELECT label, unit FROM indicator WHERE indicator_id = ?", [ind]).fetchone()
        if row is None:
            raise ToolError(f"unknown indicator {ind!r}; find ids with search_indicators")
        county = self._county_arg(args.get("county"))
        geos = self._districts(chamber, county)
        if not geos:
            raise ToolError(f"no {chamber} district has most of its people in {county[0]}:{county[1]}")
        self._prefetch([ind], geos)
        got = self._facts_for([ind], geos)
        placed, unranked, seen = [], [], set()
        for fact in got["facts"]:
            if fact["geo"] in seen:
                continue
            seen.add(fact["geo"])
            f = self.facts[fact["ref"]]
            interval = self._fact_interval(f)
            entry = {k: fact[k] for k in ("ref", "geo", "geo_name", "value_text", "reliable")}
            if interval is None or fact["status"] == MOE_STATUSES["not_computable"]:
                unranked.append(entry | {"reason": fact["status"]})
            else:
                placed.append((f.value, interval, entry))
        placed.sort(key=lambda p: (-p[0], p[2]["geo"]))
        tiers = []
        while placed:
            top = placed[0][1]
            members = [p for p in placed if p[1][1] >= top[0]]
            placed = [p for p in placed if p[1][1] < top[0]]
            tiers.append({"tier": len(tiers) + 1, "districts": [p[2] for p in members]})
        for g in [county, *geos] if county else geos:
            self.resolved.add(g)
            self.geos.add(g)
        note = self._note(ind)
        ranked = [geo_key(d["geo"]) for t in tiers for d in t["districts"]]
        notes = caveats.grouped(self.con, ranked)
        return {
            "indicator_id": ind,
            "label": row[0],
            "unit": row[1],
            "chamber": chamber,
            "county": None if county is None else self._geo(*county)["geo"],
            "order": "highest first",
            "how_to_read": config.RANK_NOTE,
            "tiers": tiers,
            "unranked": unranked,
            "missing": got["missing"],
            "unreliable": sum(d["reliable"] is False for t in tiers for d in t["districts"]),
            **({"note": note} if note else {}),
            **({"place_caveats": notes} if notes else {}),
        }

    # outreach: the website's outreach page and the outreach note on district profiles, not a model tool

    def outreach(self, pair: str, chamber: str) -> dict:
        """An outreach pair across a chamber: the group's count ranked into tiers as rank_districts ranks it, the
        state's count, and each district's program-use figure, which is shown beside it and never ranked."""
        if pair not in config.OUTREACH_PAIRS:
            raise ToolError(f"pair must be one of {list(config.OUTREACH_PAIRS)}")
        ind = config.OUTREACH_PAIRS[pair]["indicator"]
        ranked = self._rank_districts({"indicator": ind, "chamber": chamber})
        geos = [_geo_arg(d["geo"]) for t in ranked["tiers"] for d in t["districts"]]
        geos += [_geo_arg(d["geo"]) for d in ranked["unranked"]]
        use = config.PROFILE_BESIDE.get(ind, (None, ""))[0]
        if use:
            self._prefetch([use], geos)
        beside = {f"{g[0]}:{g[1]}": b["fact"] for g in geos if (b := self._beside(ind, g)) and "fact" in b}
        state = self._facts_for([ind], [("state", "15")])["facts"]
        return {
            "pair": pair,
            **ranked,
            "state": state[0] if state else None,
            "use": use if beside else None,
            "beside": beside,
        }

    def outreach_tops(self, geo: tuple[str, str]) -> dict:
        """The outreach pairs whose top tier holds a district, the smallest top tier first, and how many pairs
        the store could rank."""
        tops, ranked = [], 0
        for pair, spec in config.OUTREACH_PAIRS.items():
            try:
                tiers = self._rank_districts({"indicator": spec["indicator"], "chamber": geo[0]})["tiers"]
            except ToolError:
                continue  # a store without this measure
            ranked += bool(tiers)
            top = tiers[0]["districts"] if tiers else []
            if mine := next((d for d in top if d["geo"] == f"{geo[0]}:{geo[1]}"), None):
                tops.append({"pair": pair, "fact": mine, "shared": len(top)})
        tops.sort(key=lambda t: t["shared"])  # a stable sort keeps the pairs' order between equal tiers
        return {"tops": tops, "ranked": ranked}

    # list_programs, district_programs

    def _program_entry(self, p: Program) -> dict:
        if self._rules is None:
            self._rules = {r.rule_id: r for r in load_rules()}
        return {
            "program_id": p.program_id,
            "name": p.name,
            "covers": p.description,
            "serves": p.serves,
            "agency": p.agency,
            "url": p.url,
            "phone": p.phone,
            "verified": p.verified.isoformat(),
            "rules": [
                {
                    "rule_id": r,
                    "name": display_name(self._rules[r].program),
                    "program": self._rules[r].program,
                    "status": self._rules[r].status,
                }
                for r in p.rules
            ],
        }

    def _list_programs(self, args: dict) -> dict:
        words = _strs(args, "words", optional=True)
        query = {t for w in words or [] for t in _tokens(w)}
        out = []
        for p in catalog():
            entry = self._program_entry(p)
            names = " ".join(f"{r['rule_id'].replace('_', ' ')} {r['program']}" for r in entry["rules"])
            if query and not query & _tokens(
                f"{p.program_id.replace('_', ' ')} {p.name} {p.description} {names}"
            ):
                continue
            out.append(entry)
        return {"programs": out}

    def _need(self, need_id: str, geo: tuple[str, str]) -> dict | None:
        """The district's need figure set against the state's, or None when either is missing."""
        need = NEEDS[need_id]
        state = ("state", "15")
        refs = {}
        for g in (geo, state):
            got = {
                f["indicator_id"]: f["ref"]
                for f in reversed(self._facts_for(list(need.indicators), [g])["facts"])
            }
            if len(got) < len(need.indicators):
                return None
            refs[g] = [got[i] for i in need.indicators]
        (x, mine), (y, theirs) = (self._need_interval(refs[g]) for g in (geo, state))
        if x is None or y is None or not theirs:
            direction, ratio = "not_comparable", 0.0
        else:
            direction = (
                "within_margin" if x[0] <= y[1] and y[0] <= x[1] else "higher" if x[0] > y[1] else "lower"
            )
            ratio = mine / theirs
        if len(need.indicators) == 1 and direction != "not_comparable":
            self.directions.append((refs[geo][0], refs[state][0], direction))
        return {
            "need": need_id,
            "refs": refs[geo],
            "state_refs": refs[state],
            "direction": direction,
            "ratio": round(ratio, 3),
        }

    def _need_interval(self, refs: list[str]) -> tuple[tuple[float, float] | None, float | None]:
        """Interval and value of a rate, or of a count's share of a total with conservative bounds."""
        facts = [self.facts[r] for r in refs]
        if any(f.value is None for f in facts):
            return None, None
        if len(facts) == 1:
            return self._fact_interval(facts[0]), facts[0].value
        part, whole = facts
        p, w = self._fact_interval(part), self._fact_interval(whole)
        if not whole.value or p is None or w is None or w[1] <= 0:
            return None, None
        return (p[0] / w[1], p[1] / w[0] if w[0] > 0 else math.inf), part.value / whole.value

    def _district_programs(self, args: dict) -> dict:
        geo = _geo_arg(args.get("district"))
        if geo[0] not in ("house", "senate") or self._name(*geo) is None:
            raise ToolError("district must be a stored House or Senate district, for example house:022")
        self.resolved.add(geo)
        programs = catalog()
        needs = list(dict.fromkeys(n for p in programs for n in p.needs))
        self._prefetch(
            list(dict.fromkeys(i for n in needs for i in NEEDS[n].indicators)), [geo, ("state", "15")]
        )
        found = {n: self._need(n, geo) for n in needs}
        out = []
        for p in programs:
            reasons = sorted((r for n in p.needs if (r := found[n])), key=lambda r: -r["ratio"])
            higher = [r for r in reasons if r["direction"] == "higher"]
            entry = self._program_entry(p)
            entry["need"] = "higher" if higher else "not_higher" if reasons else "no_data"
            entry["reasons"] = higher or reasons[:1]
            out.append(entry)
        out.sort(key=lambda e: (e["need"] != "higher", -max((r["ratio"] for r in e["reasons"]), default=0)))
        return self._noted({"district": self._geo(*geo), "programs": out, "note": ELIGIBILITY_NOTE}, [geo])

    # find_sites, explain_source

    def _find_sites(self, args: dict) -> dict:
        geo = _geo_arg(args.get("geo"))
        if geo[0] not in ("house", "senate"):
            raise ToolError("geo must be a house or senate district")
        kind = args.get("kind")
        if kind is not None and kind not in SITE_KINDS:
            raise ToolError(f"kind must be one of {sorted(SITE_KINDS)}")
        kinds = [kind] if kind else sorted(SITE_KINDS)
        units = [UNIT_INDICATORS[k] for k in kinds if k in UNIT_INDICATORS]
        out = self._facts_for([SITE_INDICATORS[k] for k in kinds], [geo])
        if units:
            got = self._facts_for(units, [geo])
            out["facts"] += got["facts"]
            out["missing"] += [
                m | {"reason": "none, the place has no site of this kind"}
                if self._zero_sites(m["indicator_id"], geo)
                else m
                for m in got["missing"]
            ]
        marks = ",".join("?" * len(kinds))
        rows = self.con.execute(
            f"SELECT kind, name FROM site WHERE {geo[0]} = ? AND kind IN ({marks}) ORDER BY kind, name",
            [geo[1], *kinds],
        ).fetchall()
        out["site_total"] = len(rows)
        out["sites"] = [{"kind": k, "name": n} for k, n in rows[: config.SITE_LIMIT]]
        return self._noted(out, [geo])

    def _zero_sites(self, units: str, geo: tuple[str, str]) -> bool:
        """Whether the place has no site of the kind a units indicator counts."""
        kind = next(k for k, u in UNIT_INDICATORS.items() if u == units)
        row = self.con.execute(
            "SELECT value FROM fact WHERE indicator_id = ? AND geo_type = ? AND geo_id = ?",
            [SITE_INDICATORS[kind], *geo],
        ).fetchone()
        return row is not None and row[0] == 0

    def _explain_source(self, args: dict) -> dict:
        sid = _str(args, "source_id")
        row = self.con.execute(
            "SELECT name, publisher, vintage, url, page_url FROM source WHERE source_id = ?", [sid]
        ).fetchone()
        if row is None:
            raise ToolError(f"unknown source {sid!r}")
        return dict(zip(("name", "publisher", "vintage", "url", "page_url"), row, strict=True))


def _spec(name: str, description: str, properties: dict, required: list[str]) -> dict:
    return {
        "name": name,
        "description": description,
        "input_schema": {"type": "object", "properties": properties, "required": required},
    }


_STR = {"type": "string"}
_STRS = {"type": "array", "items": {"type": "string"}}
_GEO = {
    "type": "string",
    "description": "Geography as TYPE:ID, for example house:022, senate:017, county:15003, zcta:96744.",
}
TOOL_SPECS = [
    _spec(
        "resolve_place",
        "Resolve a district, ZIP code, street address, island, county, neighborhood or town to geographies. "
        "A street address goes to the Census geocoder. Returns every "
        "candidate with its island and the districts and counties it overlaps, or no matches with a note on why. "
        "Islands, neighborhood board areas and county divisions have no facts of their own: use their districts, "
        "or their county when of_county is close to 1. Never guess between candidates.",
        {
            "text": {
                "type": "string",
                "description": "District, ZIP code, whole street address or place name as the user wrote it.",
            }
        },
        ["text"],
    ),
    _spec(
        "search_indicators",
        "Find indicators by words. Returns ids, labels, units, the geography types each is published for, meaning "
        "(a plain definition) and method (modeled estimate for CDC PLACES measures). Pick the indicator whose label "
        "and meaning match the question's words. Housing insecurity, food insecurity, lack of reliable "
        "transportation, utility shut-off threats, uninsured adults 18 to 64 and adults who received SNAP are "
        "modeled measures held for districts: use them for those words, not rent burden or a household count, and "
        "call each a modeled estimate.",
        {"words": {**_STRS, "description": "Search words, for example kupuna or poverty."}},
        ["words"],
    ),
    _spec(
        "get_facts",
        "Get stored facts for indicators at geographies. Each fact has a ref, the exact value text, vintage, "
        "source, source_id, method and a reliability flag (false when the margin of error is large, null when "
        "the fact has no margin of error to judge). Facts that do not exist, are suppressed or do not apply are "
        "listed under missing with the reason. A fact tied to a benefit program lists it under programs: put its "
        "rule_id in programs and write its name in the answer. Districts and counties with a known caveat, such "
        "as the Maui wildfires, are listed under place_caveats.",
        {"indicator_ids": _STRS, "geos": {**_STRS, "description": "Geographies as TYPE:ID."}},
        ["indicator_ids", "geos"],
    ),
    _spec(
        "compare",
        "Compare an indicator at a district with each county holding its population and the state. Returns the "
        "facts and a direction for each comparison: higher, lower or within_margin.",
        {
            "indicator_id": _STR,
            "geo": _GEO,
            "against": {
                **_STRS,
                "description": "Optional extra geographies as TYPE:ID, for example another district.",
            },
        },
        ["indicator_id", "geo"],
    ),
    _spec(
        "district_profile",
        "The standard profile of a House or Senate district, the same one the website shows: its counties, "
        "Census places and overlapping districts in the other chamber with population shares, then sections "
        "(People, Income, Housing, Health and access, Programs in use, Who may qualify, Modeled needs, Help sites). "
        "Each row is a "
        "fact with its ref, compared with the main county and the state: a direction for rates, and the "
        "district's share for totals. Use it when asked for a profile or overview of a district.",
        {"district": {**_GEO, "description": "House or Senate district as TYPE:ID, for example house:022."}},
        ["district"],
    ),
    _spec(
        "rank_districts",
        "Rank every House or Senate district by an indicator, highest first, optionally only the districts with "
        "most of their people in one county. Districts are grouped into tiers: a tier starts at the highest "
        "district not yet placed and holds every district whose range overlaps it, so the data cannot say which "
        "district in a tier is higher. Each district has a fact ref, value text and reliability flag. Use it for "
        "questions about where a measure is highest or lowest, or where outreach could reach the most people.",
        {
            "indicator": {**_STR, "description": "Indicator id from search_indicators."},
            "chamber": {"type": "string", "enum": ["house", "senate"]},
            "county": {
                "type": "string",
                "description": "Optional county as county:ID, for example county:15009.",
            },
        },
        ["indicator", "chamber"],
    ),
    _spec(
        "list_programs",
        "List benefit programs, one entry per program, optionally filtered by words: what it covers, who it "
        "serves, the agency, the official page to apply or learn more, the agency phone, the date those details "
        "were verified, and its rules. Each rule has the rule id to put in programs, the name to write in "
        "answers, and its verification status.",
        {"words": {**_STRS, "description": "Optional words to filter programs."}},
        [],
    ),
    _spec(
        "district_programs",
        "Programs to explore in a House or Senate district, ranked by how far the district's need figures sit "
        "above the state's. Each program has the list_programs fields, need (higher when a figure is above the "
        "state outside the margins of error, not_higher, or no_data) and reasons: refs for the district and "
        "state facts behind each need figure, the direction and the ratio of district to state. Also returns "
        "the note that district data cannot establish any person's eligibility.",
        {"district": {**_GEO, "description": "House or Senate district as TYPE:ID, for example house:022."}},
        ["district"],
    ),
    _spec(
        "find_sites",
        "Find housing and health sites in a district. Returns site-count facts, the housing units at public "
        "housing and tax credit projects, and up to 20 site names. Asked how many units or homes, cite the units "
        "fact, not the count of developments.",
        {"geo": _GEO, "kind": {"type": "string", "enum": sorted(SITE_KINDS)}},
        ["geo"],
    ),
    _spec(
        "explain_source",
        "Explain a data source by source id: name, publisher, vintage, the url the data was downloaded from "
        "and page_url, the publisher's page about it.",
        {"source_id": _STR},
        ["source_id"],
    ),
]
TOOL_NAMES = tuple(t["name"] for t in TOOL_SPECS)


def main(argv: list[str] | None = None) -> int:
    """Run TOOL 'JSON' pairs in one session on the built store and print each result."""
    import sys

    import duckdb

    from klear.eval.golden import ROOT

    args = sys.argv[1:] if argv is None else argv
    if not args or len(args) % 2:
        print("usage: python -m klear.agent.tools TOOL 'JSON' [TOOL 'JSON' ...]", file=sys.stderr)
        return 2
    with duckdb.connect(str(ROOT / "data" / "build" / "klear.duckdb"), read_only=True) as con:
        session = Session(con)
        for tool, raw in zip(args[::2], args[1::2], strict=True):
            print(f"{tool} {raw}\n{session.run(tool, json.loads(raw))}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
