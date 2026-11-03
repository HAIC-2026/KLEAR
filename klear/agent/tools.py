# Deterministic tools the KLEAR agent calls, plus the session that numbers and remembers what they return.
import json
import re

from klear.agent import config
from klear.etl.places_gazetteer import plain_name
from klear.eval.contract import FactRef, fill_value, geo_display, geo_key, short_name
from klear.eval.scorers import CV_LIMIT, counties, cv
from klear.rules.registry import load_rules
from klear.store.db import MOE_STATUSES, SITE_KINDS, VALUE_STATUSES

CHAMBERS = {
    "house": ("h", "hd", "hse", "house"),
    "senate": ("s", "sd", "sen", "senate"),
}
DISTRICT_WORDS = ("d", "dis", "dist", "district", "no")
ZIP = re.compile(r"^(?:zip(?:\s*code)?\s*)?(\d{5})$")
SITE_INDICATORS = {k: f"sites_{k}" for k in SITE_KINDS}


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
NO_MATCH_HINT = (
    "No match. Give a district like House District 22 or Senate District 12, a place name, or a ZIP code."
)


def _strip_state(text: str, head: bool = True) -> str:
    """Plain text without a trailing Hawaiʻi state name, and without a leading one when head is set."""
    plain = re.sub(r"[^a-z0-9:]+", " ", plain_name(text)).strip()
    return STATE_TAIL.sub("", STATE_HEAD.sub("", plain) if head else plain).strip()


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
    def __init__(self, con):
        self.con = con
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
        row = self.con.execute(
            "SELECT name FROM geography WHERE geo_type = ? AND geo_id = ?", [geo_type, geo_id]
        ).fetchone()
        return geo_display(geo_type, geo_id, row[0]) if row else None

    def _geo(self, geo_type: str, geo_id: str, **extra) -> dict:
        self.geos.add((geo_type, geo_id))
        return {"geo": f"{geo_type}:{geo_id}", "name": self._name(geo_type, geo_id) or geo_id, **extra}

    def _overlap_entry(self, geo_type: str, geo_id: str, share: float, pop: int) -> dict:
        self.resolved.add((geo_type, geo_id))
        return self._geo(geo_type, geo_id, share=round(share, 4), pop2020=pop)

    # resolve_place

    def _resolve_place(self, args: dict) -> dict:
        text = _str(args, "text")
        if (d := _district_form(text)) and self._name(*d):
            self.resolved.add(d)
            return {"matches": [{"kind": "district", **self._geo(*d)}]}
        zipm = ZIP.match(text.strip().casefold())
        if zipm and self._name("zcta", zipm.group(1)):
            return {"matches": [self._zcta_match(zipm.group(1))]}
        plain = plain_name(text)
        if county := config.COUNTY_NAMES.get(plain):
            self.resolved.add(("county", county))
            return {"matches": [{"kind": "county", **self._geo("county", county)}]}
        if not _strip_state(text):
            return {"matches": [{"kind": "state", **self._geo("state", "15")}]}
        bare = _strip_state(text, head=False)
        found = self._find_places(plain) or (self._find_places(bare) if bare != plain else [])
        matches = [self._place_match(p) for p in found]
        return {"matches": matches} if matches else {"matches": [], "hint": NO_MATCH_HINT}

    def _zcta_match(self, zcta: str) -> dict:
        rows = []
        for column, geo_type in (("house", "house"), ("senate", "senate"), ("county", "county")):
            rows += self.con.execute(
                f"SELECT '{geo_type}', {column}, sum(pop2020) FROM blocks "
                f"WHERE zcta = ? AND pop2020 > 0 AND {column} IS NOT NULL GROUP BY {column} ORDER BY {column}",
                [zcta],
            ).fetchall()
        total = self.con.execute("SELECT sum(pop2020) FROM blocks WHERE zcta = ?", [zcta]).fetchone()[0] or 0
        self.resolved.add(("zcta", zcta))
        overlaps = [self._overlap_entry(t, i, p / total, int(p)) for t, i, p in rows]
        return {"kind": "zcta", **self._geo("zcta", zcta), "overlaps": overlaps}

    def _find_places(self, plain: str) -> list[tuple]:
        if not plain:
            return []
        places = self.con.execute(
            "SELECT place_id, name, plain, pop2020 FROM place ORDER BY name, place_id"
        ).fetchall()
        if hit := [p for p in places if p[2] == plain]:
            return hit
        if len(plain) >= 3 and (hit := [p for p in places if p[2].startswith(plain)]):
            return hit
        words = set(plain.split())
        if (
            len(plain) >= 3
            and plain not in config.STOP_WORDS
            and (hit := [p for p in places if words <= set(p[2].split())])
        ):
            return hit
        limit = _fuzzy_limit(plain)
        if limit == 0:
            return []
        dist = [(edit_distance(plain, p[2]), p) for p in places]
        best = min((d for d, _ in dist), default=99)
        return [p for d, p in dist if d == best] if best <= limit else []

    def _place_match(self, place: tuple) -> dict:
        place_id, name, _, pop = place
        self.places.add(name)
        rows = self.con.execute(
            "SELECT geo_type, geo_id, pop2020, share FROM place_overlap "
            "WHERE place_id = ? AND geo_type IN ('house', 'senate', 'county') "
            "ORDER BY geo_type = 'county', geo_type, share DESC, geo_id",
            [place_id],
        ).fetchall()
        return {
            "kind": "place",
            "name": name,
            "place_id": place_id,
            "pop2020": int(pop),
            "overlaps": [self._overlap_entry(t, i, s, int(p)) for t, i, p, s in rows],
        }

    # search_indicators

    def _search_indicators(self, args: dict) -> dict:
        words = _strs(args, "words")
        query: set[str] = set()
        for w in words:
            p = plain_name(w)
            query |= set(config.ALIASES.get(p, ())) or _tokens(w)
        rows = self.con.execute(
            "SELECT indicator_id, label, unit FROM indicator ORDER BY indicator_id"
        ).fetchall()
        scored = [
            (len(query & _tokens(f"{i} {label}".replace("_", " "))), i, label, u) for i, label, u in rows
        ]
        scored = sorted((s for s in scored if s[0] > 0), key=lambda s: (-s[0], s[1]))[: config.SEARCH_LIMIT]
        out = []
        for _, i, label, unit in scored:
            types = self.con.execute(
                "SELECT geo_type FROM indicator_coverage WHERE indicator_id = ? ORDER BY geo_type", [i]
            ).fetchall()
            out.append({"indicator_id": i, "label": label, "unit": unit, "geo_types": [t[0] for t in types]})
        return {"indicators": out}

    # get_facts

    def _ref(self, key: tuple[str, str, str, str], make) -> str:
        if key not in self._refs:
            ref = f"f{len(self._refs) + 1}"
            self._refs[key] = ref
            self.facts[ref] = make(ref)
        return self._refs[key]

    def _rows(self, indicator_id: str, geo: tuple[str, str]) -> list[tuple]:
        return self.con.execute(
            "SELECT i.label, i.unit, f.value, f.moe, f.value_status, f.moe_status, f.value_low, f.value_high, "
            "f.source_id, s.name, s.vintage, f.tier FROM fact f JOIN indicator i USING (indicator_id) "
            "JOIN source s USING (source_id) WHERE f.indicator_id = ? AND f.geo_type = ? AND f.geo_id = ? "
            "ORDER BY f.source_id",
            [indicator_id, *geo],
        ).fetchall()

    def _entry(self, indicator_id: str, geo: tuple[str, str], row: tuple) -> dict:
        label, unit, value, moe, vstatus, mstatus, low, high, source_id, source, vintage, tier = row
        ref = self._ref(
            (indicator_id, *geo, source_id),
            lambda r: FactRef(r, indicator_id, *geo, source_id, value, low, high, moe, vintage),
        )
        f = self.facts[ref]
        c = cv(f)
        reliable = None if moe is None else not (c > CV_LIMIT if c is not None else value == 0 and moe > 0)
        self.tiers[ref] = tier
        if reliable is False:
            self.unreliable.add(ref)
        return {
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
            "method": config.SOURCE_METHODS.get(source_id)
            if tier == "A" and source_id in config.SOURCE_METHODS
            else config.METHODS.get(tier, tier),
            "reliable": reliable,
        }

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
        return self._facts_for(ids, geos)

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
            return out
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
        return out

    # list_programs, find_sites, explain_source

    def _list_programs(self, args: dict) -> dict:
        words = _strs(args, "words", optional=True)
        if self._rules is None:
            self._rules = load_rules()
        query = {t for w in words or [] for t in _tokens(w)}
        out = []
        for r in self._rules:
            covers = config.COVERS.get(r.rule_id, "")
            if query and not query & _tokens(f"{r.rule_id.replace('_', ' ')} {r.program} {covers}"):
                continue
            agency = config.AGENCIES.get(r.rule_id, "")
            out.append(
                {
                    "rule_id": r.rule_id,
                    "name": display_name(r.program),
                    "program": r.program,
                    "covers": covers,
                    "agency": agency,
                    "status": r.status,
                }
            )
        return {"programs": out}

    def _find_sites(self, args: dict) -> dict:
        geo = _geo_arg(args.get("geo"))
        if geo[0] not in ("house", "senate"):
            raise ToolError("geo must be a house or senate district")
        kind = args.get("kind")
        if kind is not None and kind not in SITE_KINDS:
            raise ToolError(f"kind must be one of {sorted(SITE_KINDS)}")
        kinds = [kind] if kind else sorted(SITE_KINDS)
        out = self._facts_for([SITE_INDICATORS[k] for k in kinds], [geo])
        marks = ",".join("?" * len(kinds))
        rows = self.con.execute(
            f"SELECT kind, name FROM site WHERE {geo[0]} = ? AND kind IN ({marks}) ORDER BY kind, name",
            [geo[1], *kinds],
        ).fetchall()
        out["site_total"] = len(rows)
        out["sites"] = [{"kind": k, "name": n} for k, n in rows[: config.SITE_LIMIT]]
        return out

    def _explain_source(self, args: dict) -> dict:
        sid = _str(args, "source_id")
        row = self.con.execute(
            "SELECT name, publisher, vintage, url FROM source WHERE source_id = ?", [sid]
        ).fetchone()
        if row is None:
            raise ToolError(f"unknown source {sid!r}")
        return dict(zip(("name", "publisher", "vintage", "url"), row, strict=True))


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
        "Resolve a district, ZIP code or place name to geographies. Returns every candidate with its overlapping "
        "districts and counties, or no matches. Never guess between candidates.",
        {"text": {"type": "string", "description": "District, ZIP code or place name as the user wrote it."}},
        ["text"],
    ),
    _spec(
        "search_indicators",
        "Find indicators by words. Returns ids, labels, units and the geography types each is published for.",
        {"words": {**_STRS, "description": "Search words, for example kupuna or poverty."}},
        ["words"],
    ),
    _spec(
        "get_facts",
        "Get stored facts for indicators at geographies. Each fact has a ref, the exact value text, vintage, "
        "source, source_id, method and a reliability flag (false when the margin of error is large, null when "
        "the fact has no margin of error to judge). Facts that do not exist, are suppressed or do not apply are "
        "listed under missing with the reason.",
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
        "list_programs",
        "List benefit programs with rule id, name to write in answers, full program title, what it covers, "
        "agency and verification status, optionally filtered by words.",
        {"words": {**_STRS, "description": "Optional words to filter programs."}},
        [],
    ),
    _spec(
        "find_sites",
        "Find housing and health sites in a district. Returns site-count facts and up to 20 site names.",
        {"geo": _GEO, "kind": {"type": "string", "enum": sorted(SITE_KINDS)}},
        ["geo"],
    ),
    _spec(
        "explain_source",
        "Explain a data source by source id: name, publisher, vintage and url.",
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
