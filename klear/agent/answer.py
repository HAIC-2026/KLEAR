# Builds the contract Answer from the model's submit_answer call, filling every value from the session and checking it.
import re
import unicodedata
from dataclasses import replace

from klear.agent import config
from klear.agent.tools import _spec, display_name
from klear.etl.places_gazetteer import plain_name
from klear.eval.contract import MARKER, REFUSALS, VERSION, Answer, render
from klear.eval.scorers import (
    EMPHASIS,
    FLIP,
    answer_problems,
    counties,
    direction_claims,
    known_programs,
    program_named,
    program_names,
)
from klear.programs import by_rule
from klear.store.methods import place_note
from klear.text import hawaiian, strip_ignorable

_STRS = {"type": "array", "items": {"type": "string"}}
SUBMIT_SPEC = _spec(
    "submit_answer",
    "Submit the final answer. Write blocks as plain prose with markers in place of every number, unit, place "
    "name, label and vintage: {fact:fN} for a value, {vintage:fN} for its vintage and source, {label:fN} for "
    "its indicator label and {geo:TYPE:ID} for a place. Never type digits. Cite only refs that tools returned.",
    {
        "resolved": {
            "type": ["string", "null"],
            "description": "The district the answer is about as TYPE:ID, for example house:022, or null.",
        },
        "refusal": {"type": "string", "enum": list(REFUSALS), "description": "none unless declining."},
        "blocks": {**_STRS, "description": "Answer paragraphs with markers."},
        "facts": {**_STRS, "description": "Every fN ref cited by a marker, for example f1."},
        "programs": {
            **_STRS,
            "description": "Rule ids or information-only catalog program ids the answer names.",
        },
        "caveats": {**_STRS, "description": "Short caveat sentences with markers."},
    },
    ["resolved", "refusal", "blocks", "facts", "programs", "caveats"],
)
REFUSAL_TEXT = {
    "decline_individual": "I describe districts and places from public data. I cannot answer questions about one "
    "person.",
    "decline_partisan": "I share neutral public data about districts. I do not give political advice.",
    "not_available": "The data I have does not include that.",
    "out_of_scope": "That is outside what I can answer. I answer questions about Hawaiʻi House and Senate districts.",
}
OKINA_MARKS = str.maketrans("", "", "\u02bb\u2018\u02bc\u0060")
NO_FACT_REFUSALS = ("decline_individual", "decline_partisan")
DISTRICT = re.compile(r"(house|senate):(\d{3})|(state):(\d{2})")
# Web addresses and email addresses, kept exactly as written.
WEB = re.compile(
    r"(?:https?://|www\.)[^\s)\]>]+|[\w.+-]+@[\w-]+(?:\.[\w-]+)+"
    r"|\b[\w-]+(?:\.[\w-]+)*\.(?:gov|org|com|edu|net|us)\b(?:/[^\s)\]>]*)?",
    re.IGNORECASE,
)
STATEWIDE = re.compile(config.STATEWIDE, re.IGNORECASE)
YEAR = re.compile(r"(?<!\d)(?:19|20)\d{2}(?!\d)")
# A release year in a vintage, which is not a year the data describe.
RELEASE = re.compile(r"(?:19|20)\d{2}\s+release", re.IGNORECASE)
UNPUBLISHED_TEXT = (
    "Figures for the year you asked about are not published for this place yet. The latest available figures "
    "are below, with their years."
)
SAYS_UNPUBLISHED = re.compile(r"\bnot (?:yet )?(?:published|available|released)\b", re.IGNORECASE)
NO_FACTS_TEXT = "I could not retrieve any facts for that question."
# Party, candidate and advocacy words a partisan decline must not repeat.
PARTISAN_WORDS = (
    "democrat",
    "democrats",
    "democratic",
    "republican",
    "republicans",
    "green party",
    "libertarian",
    "vote for",
    "should win",
    "will win",
    "should vote",
)
NUMBER_HINT = (
    "write every place with a geo marker such as {geo:house:009}, never a typed district number, "
    "and every value with a fact marker"
)
DIRECTION_TEXT = {
    "higher": "higher than",
    "lower": "lower than",
    "within_margin": "within the margin of error of",
}
METHOD_TEXT = {
    "A": ", an estimate apportioned to the district",
    "M": ", a modeled estimate",
    "S": ", a sum of published counts",
}


def _method_text(tier: str | None, f) -> str:
    """How a fact was made, for the fallback answer: its tier, and what KLEAR derived it from."""
    note = place_note(f.source_id, f.geo_type)
    if note and tier == "S":
        return f", the {note}"
    return METHOD_TEXT.get(tier, "") + (f", the {note}" if note else "")


def allowed_geos(session, resolved: tuple[str, str] | None, context=None) -> set[tuple[str, str]]:
    """Geographies an answer may name: state, the district and its counties, resolved places and context."""
    con = session.con
    allowed = {
        ("state", r[0])
        for r in con.execute("SELECT geo_id FROM geography WHERE geo_type = 'state'").fetchall()
    }
    allowed |= set(session.resolved)
    for geo in {g for g in (resolved, context or session.context) if g is not None}:
        allowed.add(geo)
        allowed |= counties(geo, con)
    return allowed


def stored_district(session, text) -> tuple[str, str] | None:
    m = DISTRICT.fullmatch(text) if isinstance(text, str) else None
    if m is None:
        return None
    geo = tuple(g for g in m.groups() if g is not None)
    exists = session.con.execute("SELECT 1 FROM geography WHERE geo_type = ? AND geo_id = ?", list(geo))
    return geo if exists.fetchone() else None


def _typed_places(session, texts: list[str], allowed: set[tuple[str, str]]) -> list[str]:
    """Place names typed outside markers that no tool resolved."""
    names = {plain_name(n) for n in session.places}
    names |= {n for n, c in config.COUNTY_NAMES.items() if ("county", c) in allowed}
    known = {r[0] for r in session.con.execute("SELECT plain FROM place").fetchall()}
    known |= set(config.COUNTY_NAMES)
    words = _words(MARKER.sub(" ", " ".join(texts)))
    lower = [w.casefold() for w in words]
    found = []
    for n in sorted(known - names - {"hawaii"}):
        parts = n.split()
        for i in range(len(words) - len(parts) + 1):
            if lower[i : i + len(parts)] == parts and all(w[0].isupper() for w in words[i : i + len(parts)]):
                found.append(f"names place {n} that was not resolved; use resolve_place or a geo marker")
                break
    return found


def _words(text: str) -> list[str]:
    """Cased words of text with ignorable characters, emphasis, accents and okina removed."""
    text = EMPHASIS.sub("", strip_ignorable(unicodedata.normalize("NFKC", strip_ignorable(text))))
    text = unicodedata.normalize("NFD", text.translate(OKINA_MARKS))
    return re.findall(r"[A-Za-z0-9]+", "".join(c for c in text if not unicodedata.combining(c)))


def _strings(args: dict, key: str) -> tuple[list[str], list[str]]:
    v = args.get(key)
    if isinstance(v, list) and all(isinstance(x, str) for x in v):
        return list(v), []
    return [], [f"{key} must be a list of strings"]


def build_answer(session, args: dict, context=None) -> tuple[Answer | None, list[str]]:
    """Answer from a submit_answer call with facts copied from the session, plus its problems."""
    if not isinstance(args, dict):
        return None, ["submit_answer arguments must be an object"]
    problems: list[str] = []
    blocks, p = _strings(args, "blocks")
    problems += p
    caveats, p = _strings(args, "caveats")
    problems += p
    refs, p = _strings(args, "facts")
    problems += p
    programs, p = _strings(args, "programs")
    problems += p
    refusal = args.get("refusal")
    if refusal not in REFUSALS:
        problems.append(f"refusal must be one of {list(REFUSALS)}")
    resolved = args.get("resolved")
    if resolved is not None:
        resolved = stored_district(session, resolved)
        if resolved is None:
            problems.append(
                "resolved must be null, state:15 or exactly house:NNN or senate:NNN naming a stored district"
            )
    for r in refs:
        if r not in session.facts:
            problems.append(
                f"fact {r} was not returned by any tool; cite only refs from get_facts or compare"
            )
    known = known_programs()
    problems += [
        f"unknown program id {x!r}, expected one of {sorted(known)}" for x in programs if x not in known
    ]
    if refusal in NO_FACT_REFUSALS and (
        refs or any(k != "geo" for t in [*blocks, *caveats] for k, _ in MARKER.findall(t))
    ):
        problems.append(f"{refusal} answers must cite no facts and use only geo markers")
    if refusal == "decline_partisan":
        low = " ".join(_words(" ".join([*blocks, *caveats]))).casefold()
        problems += [
            f"decline_partisan answers must not say {w!r}"
            for w in PARTISAN_WORDS
            if re.search(rf"(?<!\w){w}(?!\w)", low)
        ]
    if problems:
        return None, problems
    answer = Answer(
        VERSION, resolved, refusal, blocks, [replace(session.facts[r]) for r in refs], programs, caveats
    )
    allowed = allowed_geos(session, resolved, context)
    problems = answer_problems(answer, session.con, allowed) + _typed_places(
        session, [*blocks, *caveats], allowed
    )
    if any(p.startswith("unsourced number") for p in problems):
        problems.append(NUMBER_HINT)
    if not problems:
        text = render(answer, session.con)
        problems = [
            f"program {p} is listed but not named in the text; write it as {display_name(known[p])!r}"
            for p in programs
            if not program_named(text, known[p])
        ]
        problems += _missing_required(session, answer) + _measured_not_available(answer)
        problems += _wrong_directions(answer) + _missing_group_words(session, text)
    return (None if problems else finish(session, answer)), problems


def spelled(text: str) -> str:
    """Text with Hawaiian place names spelled with ʻokina and kahakō, markers and web addresses kept as written."""
    kept: list[list[int]] = []
    for a, b in sorted(m.span() for rx in (MARKER, WEB) for m in rx.finditer(text)):
        if kept and a <= kept[-1][1]:
            kept[-1][1] = max(kept[-1][1], b)
        else:
            kept.append([a, b])
    out, pos = [], 0
    for a, b in kept:
        out += [hawaiian(text[pos:a]), text[a:b]]
        pos = b
    return "".join([*out, hawaiian(text[pos:])])


def tied_programs(indicators, listed) -> list[str]:
    """Rule ids of programs tied to the indicators that no listed rule's program already covers."""
    owners = by_rule()
    names = program_names()
    have = {owners[r].program_id if r in owners else r for r in listed}
    out = []
    for i in indicators:
        for r in config.TIED_PROGRAMS.get(i, ()):
            owner = owners[r].program_id if r in owners else r
            if r in names and owner not in have:
                have.add(owner)
                out.append(r)
    return out


def _joined(parts: list[str]) -> str:
    return parts[0] if len(parts) == 1 else f"{', '.join(parts[:-1])} and {parts[-1]}"


def unpublished_years(
    question: str, vintages: list[str], labels: list[str], programs: list[str]
) -> list[int]:
    """Years the question asks for that no dated vintage, label or listed program covers."""
    asked = sorted({int(y) for y in YEAR.findall(question)})
    covered: set[int] = set()
    for v in vintages:
        years = [int(y) for y in YEAR.findall(RELEASE.sub(" ", v or ""))]
        if not years:
            return []
        covered |= set(range(min(years), max(years) + 1))
    covered |= {int(y) for t in [*labels, *programs] for y in YEAR.findall(t)}
    return [y for y in asked if y not in covered] if vintages else []


def finish(session, answer: Answer) -> Answer:
    """The accepted answer with Hawaiian spelling fixed, its resolved place, the year it can give, the programs
    its figures are tied to, and a note on modeled figures."""
    blocks = [spelled(t) for t in answer.blocks]
    caveats = [spelled(t) for t in answer.caveats]
    resolved, refusal, programs = answer.resolved, answer.refusal, list(answer.programs)
    state_only = bool(answer.facts) and all(f.geo_type == "state" for f in answer.facts)
    if STATEWIDE.search(session.question) and (resolved is None or state_only):
        resolved = ("state", "15")
    if refusal not in ("none", "not_available"):
        return replace(answer, blocks=blocks, caveats=caveats, resolved=resolved)
    names = known_programs()
    ids = list(dict.fromkeys(f.indicator_id for f in answer.facts))
    labels = [
        r[0]
        for r in session.con.execute(
            "SELECT label FROM indicator WHERE indicator_id IN (SELECT unnest(?))", [ids]
        ).fetchall()
    ]
    if unpublished_years(
        session.question, [f.vintage for f in answer.facts], labels, [f"{p} {names[p]}" for p in programs]
    ):
        refusal = "not_available"
        if not SAYS_UNPUBLISHED.search(" ".join([*blocks, *caveats])):
            blocks.insert(0, UNPUBLISHED_TEXT)
    programs += tied_programs(ids, programs)
    text = render(replace(answer, blocks=blocks, caveats=caveats), session.con)
    unnamed = [display_name(names[p]) for p in programs if not program_named(text, names[p])]
    if unnamed:
        blocks.append(f"Related programs: {_joined(unnamed)}.")
    modeled = list(dict.fromkeys(f.indicator_id for f in answer.facts if session.tiers.get(f.ref) == "M"))
    if modeled and not re.search(r"\bmodel(?:l)?ed\b", text, re.IGNORECASE):
        refs = {f.indicator_id: f.ref for f in reversed(answer.facts)}
        shown = _joined([f"{{label:{refs[i]}}}" for i in modeled])
        caveats.append(
            f"{shown} {'is a modeled estimate' if len(modeled) == 1 else 'are modeled estimates'}."
        )
    return replace(
        answer, resolved=resolved, refusal=refusal, blocks=blocks, caveats=caveats, programs=programs
    )


def _triggered(session, table) -> list[str]:
    """Ids of every table entry whose trigger words the question contains."""
    asked = {w.casefold() for w in _words(session.question)}
    return list(dict.fromkeys(i for words, ids in table if asked.issuperset(words) for i in ids))


def _stored(session, indicator: str, geo: tuple[str, str]) -> bool:
    return bool(
        session.con.execute(
            "SELECT 1 FROM fact WHERE indicator_id = ? AND geo_type = ? AND geo_id = ? AND value IS NOT NULL",
            [indicator, *geo],
        ).fetchone()
    )


def required_facts(session, geo: tuple[str, str]) -> list[str]:
    """Indicators the question asks for by name that the store holds for geo."""
    return [i for i in _triggered(session, config.REQUIRED_FACTS) if _stored(session, i, geo)]


def main_county(con, geo: tuple[str, str]) -> tuple[str, str] | None:
    """The county holding most of a House or Senate district's 2020 population."""
    if geo[0] not in config.CHAMBER_TYPES:
        return None
    row = con.execute(
        f"SELECT county FROM blocks WHERE {geo[0]} = ? GROUP BY 1 ORDER BY sum(pop2020) DESC, 1 LIMIT 1",
        [geo[1]],
    ).fetchone()
    return ("county", row[0]) if row else None


def required_comparisons(session, geo: tuple[str, str]) -> list[tuple[str, tuple[str, str]]]:
    """Indicators and places the question asks to compare the district with: its main county and the state."""
    places = [p for p in (main_county(session.con, geo), ("state", "15")) if p]
    return [
        (i, p)
        for i in _triggered(session, config.REQUIRED_COMPARISONS)
        for p in places
        if _stored(session, i, p)
    ]


def required_programs(session) -> list[str]:
    """Rule ids of the programs the question asks about."""
    return _triggered(session, config.REQUIRED_PROGRAMS)


def _missing_required(session, answer: Answer) -> list[str]:
    """Required facts, comparisons and programs the answer leaves out."""
    if answer.refusal != "none":
        return []
    names = known_programs()
    out = [
        f"this question asks about {p}; list it in programs and write it as {display_name(names[p])!r}"
        for p in required_programs(session)
        if p not in answer.programs
    ]
    if answer.resolved is None:
        return out
    cited = {(f.indicator_id, f.geo_type, f.geo_id) for f in answer.facts}
    geo = ":".join(answer.resolved)
    out += [
        f"this question asks for {i} at {geo}; call get_facts for it and cite it"
        for i in required_facts(session, answer.resolved)
        if (i, *answer.resolved) not in cited
    ]
    return out + [
        f"this question compares {i} at {geo} with {p[0]}:{p[1]}; call get_facts for it there and cite it"
        for i, p in required_comparisons(session, answer.resolved)
        if (i, *p) not in cited
    ]


def _interval(f) -> tuple[float, float] | None:
    if f.value_low is not None and f.value_high is not None:
        return f.value_low, f.value_high
    return None if f.value is None else (f.value - (f.moe or 0), f.value + (f.moe or 0))


def actual_direction(a, b) -> str | None:
    """higher, lower or within_margin for fact a against fact b, as compare reports it."""
    mine, theirs = _interval(a), _interval(b)
    if mine is None or theirs is None:
        return None
    if mine[0] <= theirs[1] and theirs[0] <= mine[1]:
        return "within_margin"
    return "higher" if mine[0] > theirs[1] else "lower"


def _wrong_directions(answer: Answer) -> list[str]:
    """Higher or lower claims that the cited facts show the other way round."""
    by_geo: dict[tuple[str, str], dict[str, object]] = {}
    for f in answer.facts:
        by_geo.setdefault((f.geo_type, f.geo_id), {}).setdefault(f.indicator_id, f)
    out = []
    for s, said, o in direction_claims("\n".join([*answer.blocks, *answer.caveats])):
        mine, theirs = by_geo.get(s, {}), by_geo.get(o, {})
        found = {actual_direction(mine[i], theirs[i]) for i in mine.keys() & theirs.keys()}
        if said in ("higher", "lower") and found and found <= {FLIP[said]}:
            out.append(
                f"the text says {{geo:{s[0]}:{s[1]}}} is {said} than {{geo:{o[0]}:{o[1]}}}, but the facts "
                f"show it is {FLIP[said]}; use the direction compare returns"
            )
    return out


def asked_group_words(session) -> list[str]:
    """Hawaiian group words the question uses, spelled as an answer must write them."""
    asked = {w.casefold() for w in _words(session.question)}
    return [word for plain, word in config.GROUP_WORDS.items() if plain in asked]


def _missing_group_words(session, text: str) -> list[str]:
    """Hawaiian group words the question used that the answer leaves out."""
    low = unicodedata.normalize("NFC", text).casefold()
    return [
        f"the question says {word}; write {word} in the answer, spelled with ʻokina and kahakō"
        for word in asked_group_words(session)
        if word.casefold() not in low
    ]


def _measured_not_available(answer: Answer) -> list[str]:
    """A not_available answer that cites a measure of a program it lists."""
    if answer.refusal != "not_available":
        return []
    cited = {f.indicator_id for f in answer.facts}
    hits = sorted({i for p in answer.programs for i in config.MEASURES.get(p, ()) if i in cited})
    if not hits:
        return []
    names = ", ".join(hits)
    return [
        f"the answer cites {names}, a measure of a listed program, so set refusal none and say plainly what those figures do not show"
    ]


def template_answer(
    session, resolved: tuple[str, str] | None, refusal: str, context=None, programs=()
) -> Answer:
    """Fallback answer built only from markers over the session facts, or a neutral refusal sentence."""
    if refusal != "none":
        return Answer(VERSION, resolved, refusal, [REFUSAL_TEXT[refusal]], [], [], [])
    allowed = allowed_geos(session, resolved, context)
    facts = [replace(f) for f in session.facts.values() if (f.geo_type, f.geo_id) in allowed]
    if not facts:
        return Answer(VERSION, resolved, "none", [NO_FACTS_TEXT], [], [], [])
    shown = {f.ref: f for f in facts}
    lines = [
        f"* {{label:{f.ref}}} in {{geo:{f.geo_type}:{f.geo_id}}}: {{fact:{f.ref}}} ({{vintage:{f.ref}}})"
        + _method_text(session.tiers.get(f.ref), f)
        + (", an unreliable estimate" if f.ref in session.unreliable else "")
        for f in facts
    ]
    blocks = ["\n".join(lines)]
    if groups := asked_group_words(session):
        blocks.insert(0, "Figures on " + " and ".join(groups) + ":")
    compared = [
        f"* {{geo:{shown[a].geo_type}:{shown[a].geo_id}}} is {DIRECTION_TEXT[d]} "
        f"{{geo:{shown[b].geo_type}:{shown[b].geo_id}}}"
        for a, b, d in dict.fromkeys(session.directions)
        if a in shown and b in shown and d in DIRECTION_TEXT
    ]
    if compared:
        blocks.append("\n".join(compared))
    names = known_programs()
    programs = [p for p in dict.fromkeys(programs) if p in names]
    programs += tied_programs(dict.fromkeys(f.indicator_id for f in facts), programs)
    if programs:
        blocks.append("Related programs: " + ", ".join(display_name(names[p]) for p in programs) + ".")
    return Answer(VERSION, resolved, "none", blocks, facts, programs, [])
