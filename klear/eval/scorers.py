# Deterministic scorers that check one agent answer against its golden question and the data store, where the model types no digits, units or links and every number, vintage and place comes from a marker.
import html.entities
import math
import re
import unicodedata
from dataclasses import dataclass
from functools import cache
from pathlib import Path

import yaml

from klear.eval.contract import MARKER, Answer, ContractError, geo_key, render
from klear.eval.golden import RULES, Golden
from klear.programs import catalog
from klear.text import hawaiian, misspellings, strip_ignorable

TOL = 1e-9
SIGN = "[-+\u2212\u00b1$%\u20ac]"
MARK_GAP = "[\\s*_\u0060~\\\\()\\[\\]\"'\u2018\u2019\u201c\u201d]*"
STRIP = " *_\u0060~\\()[]\"'\u2018\u2019\u201c\u201d"
UNIT_WORD = re.compile(
    r"(?<!\w)(?:per\s*-?\s*cent|percents?|percentages?|pct|dollars?|usd|cents?|millions?|billions?|bn|mn"
    r"|thousands?|hundreds?|dozens?|score\s+of)(?!\w)",
    re.IGNORECASE,
)
NUMBER_WORD = re.compile(
    r"(?<!\w)(?:zero|two|three|four|five|six|seven|eight|nine|ten|eleven|twelve|thirteen|fourteen|fifteen"
    r"|sixteen|seventeen|eighteen|nineteen|twenty|thirty|forty|fifty|sixty|seventy|eighty|ninety"
    r"|half|halves|thirds?|quarters?|fourths?|fifths?|tenths?|twice|double[ds]?|triple[ds]?|quadruple"
    r"|(?:six|seven|eigh|nin)ths?|(?:twentie|hundred|thousand|million)ths?"
    r"|(?:one|an?)[\s-]+(?:(?:thir|four|fif|six|seven|eigh|nine)teenths?|(?:thir|for|fif|six|seven|eigh|nine)tieths?"
    r"|elevenths?|twelfths?)"
    r"|halved|halving|doubling|tripling|(?:two|three|four|ten)fold)(?!\w)",
    re.IGNORECASE,
)
UNIT_SYMBOLS = "%\u2030\u2031"
EMPHASIS = re.compile("[*_\u0060~\\\\]")
HTML = re.compile(r"<\s*[!/?a-z]", re.IGNORECASE)
ENTITY = re.compile(r"&(#\d+|#[xX][0-9a-fA-F]+|[A-Za-z][A-Za-z0-9]*);?")
ENTITY_NAMES = {k.rstrip(";") for k in html.entities.html5}
ROMAN = re.compile(r"(?<!\w)[IVX]{2,}(?!\w)")
ROMAN_VALID = re.compile(r"X{0,3}(?:IX|IV|V?I{0,3})")
BULLET = re.compile(r"^([ \t]*)[-*](?=[ \t])", re.MULTILINE)
UNIT_K = re.compile(MARK_GAP + r"k(?!\w)", re.IGNORECASE)
UNIT_K_BEFORE = re.compile(r"(?<!\w)k" + MARK_GAP + "$", re.IGNORECASE)
CV_LIMIT = 0.30
# Comparison words per direction and the words that contradict it.
DIRECTION_WORDS = {
    "higher": ("higher", "more", "greater", "above", "exceeds", "exceed"),
    "lower": ("lower", "less", "fewer", "below"),
    "within_margin": (
        "within the margin",
        "not significant",
        "not statistically significant",
        "overlap",
        "overlaps",
        "overlapping",
    ),
}
FLIP = {"higher": "lower", "lower": "higher", "within_margin": "within_margin"}
# Phrases that state a direction from the place before them to the place after them.
COMPARE_PHRASES = {
    "higher": ("higher than", "more than", "greater than", "larger than", "older than", "above", "exceeds"),
    "lower": ("lower than", "less than", "fewer than", "smaller than", "younger than", "below"),
    "within_margin": ("within the margin of error of", "within the margin of"),
}
CLAIM_TOKEN = re.compile(
    r"\{geo:(\w+):(\w+)\}|(?<!\w)("
    + "|".join(re.escape(p) for ps in COMPARE_PHRASES.values() for p in sorted(ps, key=len, reverse=True))
    + r")(?!\w)",
    re.IGNORECASE,
)
PHRASE_DIRECTION = {p: d for d, ps in COMPARE_PHRASES.items() for p in ps}
BLOCK_COLUMN = {"house": "house", "senate": "senate", "zcta": "zcta", "tract": "tract"}
HTML_SPAN = re.compile(r"<!--.*?(?:-->|$)|<\s*[!/?a-z][^>]*(?:>|$)", re.IGNORECASE | re.DOTALL)
FENCE = re.compile(r"^[ \t]*(\u0060{3,}|~{3,}).*?(?:^[ \t]*\1|\Z)", re.MULTILINE | re.DOTALL)
CODE_SPAN = re.compile(r"(\u0060+).+?\1", re.DOTALL)
REF_DEF = re.compile(r"^[ \t]*\[[^\]\n]*\]:.*(?:\n(?![ \t]*$).*)*", re.MULTILINE)


@dataclass
class Outcome:
    check: str
    passed: bool | None
    detail: str


def _visible(text: str) -> str:
    return strip_ignorable(unicodedata.normalize("NFKC", strip_ignorable(text)))


def _link_spans(text: str) -> list[tuple[int, int]]:
    """Spans of markdown link or image text in brackets plus any (destination "title") after them."""
    spans, opens, i = [], [], 0
    while i < len(text):
        c = text[i]
        if c == "\\":
            i += 2
            continue
        if c == "[":
            opens.append(i)
        elif c == "]" and opens:
            start, i = opens.pop(), i + 1
            if text[i : i + 1] == "(":
                depth = 0
                while i < len(text):
                    if text[i] == "\\":
                        i += 2
                        continue
                    depth += {"(": 1, ")": -1}.get(text[i], 0)
                    i += 1
                    if depth == 0:
                        break
            spans.append((start, i))
            continue
        i += 1
    return spans


def _hidden_spans(text: str) -> list[tuple[int, int]]:
    """Spans a reader never sees as plain text: HTML, code, links, images and reference definitions."""
    spans = [m.span() for rx in (HTML_SPAN, FENCE, CODE_SPAN, REF_DEF) for m in rx.finditer(text)]
    return spans + _link_spans(text)


def _markers(text: str) -> tuple[list[tuple[str, str]], list[tuple[str, str]]]:
    """Split the markers of text into shown and hidden ones."""
    text = strip_ignorable(text)
    spans = _hidden_spans(text)
    shown, hidden = [], []
    for m in MARKER.finditer(text):
        inside = any(a < m.end() and m.start() < b for a, b in spans)
        (hidden if inside else shown).append((m.group(1), m.group(2)))
    return shown, hidden


def _number(text: str) -> str | None:
    for t in (text, _visible(text)):
        for i, c in enumerate(t):
            if unicodedata.numeric(c, None) is None:
                continue
            if not c.isascii():
                return f"{c} (U+{ord(c):04X})"
            return repr(re.match("[0-9]+", t[i:]).group())
    return None


def _marker_neighbor(text: str) -> str | None:
    for m in MARKER.finditer(text):
        before, after = text[: m.start()], text[m.end() :]
        x = re.match(MARK_GAP + SIGN, after)
        if (
            x
            and x.group().endswith("-")
            and x.group()[-2:-1].isspace()
            and after[x.end() : x.end() + 1].isspace()
        ):
            x = None
        if x or (x := re.search(SIGN + MARK_GAP + "$", before)):
            return f"modifier {x.group().strip(STRIP)!r} next to marker"
        if after[:1].isalnum() or before[-1:].isalnum():
            return "word attached to marker"
        if UNIT_K.match(after) or UNIT_K_BEFORE.search(before):
            return "unit word 'k' next to marker"
    return None


def _entity(text: str) -> str | None:
    for m in ENTITY.finditer(text):
        if m.group(1).startswith("#") or m.group(1) in ENTITY_NAMES:
            return m.group()
    return None


def _roman(text: str) -> str | None:
    return next((m.group() for m in ROMAN.finditer(text) if ROMAN_VALID.fullmatch(m.group())), None)


def _text_problems(text: str) -> list[str]:
    raw = strip_ignorable(text)
    if MARKER.findall(raw) != MARKER.findall(text):
        return ["invisible character inside a marker"]
    problems = []
    rest = MARKER.sub(" ", raw)
    if (n := _number(rest)) is not None:
        problems.append(f"unsourced number {n}")
    shown, rest = _visible(text), _visible(rest)
    if HTML.search(shown) or _entity(shown):
        problems.append("HTML tag, comment or entity")
    plain = EMPHASIS.sub("", rest)
    if w := UNIT_WORD.search(plain):
        problems.append(f"unit word {w.group()!r}")
    if w := NUMBER_WORD.search(plain):
        problems.append(f"number word {w.group()!r}")
    if n := _roman(plain):
        problems.append(f"Roman numeral {n!r}")
    if sym := [c for c in rest if c in UNIT_SYMBOLS or unicodedata.category(c) == "Sc"]:
        problems.append(f"unit symbol {sym[0]!r}")
    if (problem := _marker_neighbor(BULLET.sub(r"\1 ", shown))) is not None:
        problems.append(problem)
    if (b := re.search(r"[{}]", rest)) is not None:
        problems.append(f"malformed marker {rest[b.start() : b.start() + 12].strip()!r}")
    on, off = _markers(text)
    problems += [f"marker {{{k}:{r}}} inside a link, image, code or HTML" for k, r in off]
    facts = {r for k, r in on if k == "fact"}
    problems += [
        f"{{vintage:{r}}} without {{fact:{r}}} in the same block"
        for k, r in on
        if k == "vintage" and r not in facts
    ]
    return problems


def _same(x: float | None, y: float | None) -> bool:
    xn = x is None or math.isnan(x)
    yn = y is None or math.isnan(y)
    return xn and yn if xn or yn else abs(x - y) <= TOL


def _fact_problem(f, con) -> str | None:
    where = f"{f.indicator_id} at {f.geo_type} {f.geo_id} ({f.source_id})"
    row = con.execute(
        "SELECT value, value_low, value_high, moe FROM fact "
        "WHERE indicator_id = ? AND geo_type = ? AND geo_id = ? AND source_id = ?",
        [f.indicator_id, f.geo_type, f.geo_id, f.source_id],
    ).fetchone()
    if row is None:
        return f"{f.ref}: no store row for {where}"
    for name, stored in zip(("value", "value_low", "value_high", "moe"), row, strict=True):
        if not _same(getattr(f, name), stored):
            return f"{f.ref}: {name} {getattr(f, name)} != store {stored} for {where}"
    src = con.execute("SELECT vintage FROM source WHERE source_id = ?", [f.source_id]).fetchone()
    if src is None or f.vintage != src[0]:
        return f"{f.ref}: vintage {f.vintage!r} != source {src[0] if src else None!r} for {where}"
    return None


def counties(geo: tuple[str, str], con) -> set[tuple[str, str]]:
    """Counties holding any 2020 population of geo, plus its stored parent county."""
    out = set()
    row = con.execute(
        "SELECT parent_county FROM geography WHERE geo_type = ? AND geo_id = ?", list(geo)
    ).fetchone()
    if row and row[0]:
        out.add(("county", row[0]))
    column = BLOCK_COLUMN.get(geo[0])
    has_blocks = con.execute(
        "SELECT count(*) FROM information_schema.tables WHERE table_name = 'blocks'"
    ).fetchone()[0]
    if column and has_blocks:
        rows = con.execute(
            f"SELECT DISTINCT county FROM blocks WHERE {column} = ? AND pop2020 > 0", [geo[1]]
        ).fetchall()
        out |= {("county", r[0]) for r in rows}
    return out


def _allowed_geos(g: Golden, a: Answer, con) -> set[tuple[str, str]]:
    geos = {(t[1], t[2]) for t in g.facts} | set(g.allowed_geos)
    geos |= {
        (r[0], r[1])
        for r in con.execute("SELECT geo_type, geo_id FROM geography WHERE geo_type = 'state'").fetchall()
    }
    for geo in (a.resolved, g.context):
        if geo is not None:
            geos.add(tuple(geo))
            geos |= counties(tuple(geo), con)
    return geos


def _shown_facts(a: Answer) -> set[str]:
    return {r for t in [*a.blocks, *a.caveats] for k, r in _markers(t)[0] if k == "fact"}


def answer_problems(a: Answer, con, allowed: set[tuple[str, str]]) -> list[str]:
    """Golden-free fidelity problems of an answer."""
    texts = [*a.blocks, *a.caveats]
    problems = [p for text in texts for p in _text_problems(text)]
    refs = [f.ref for f in a.facts]
    problems += [f"duplicate fact ref {r}" for r in sorted({r for r in refs if refs.count(r) > 1})]
    cited = {(k, r) for text in texts for k, r in MARKER.findall(text)}
    problems += [
        f"marker {{{k}:{r}}} has no fact ref" for k, r in sorted(cited) if k != "geo" and r not in refs
    ]
    shown = _shown_facts(a)
    problems += [f"fact {f.ref} is never used by a marker" for f in a.facts if f.ref not in shown]
    for _, r in sorted(c for c in cited if c[0] == "geo"):
        if geo_key(r) not in allowed:
            problems.append(f"geography {r.replace(':', ' ')} is not allowed here")
    for f in a.facts:
        if (f.geo_type, f.geo_id) not in allowed:
            problems.append(
                f"{f.ref}: {f.indicator_id} geography {f.geo_type} {f.geo_id} is not allowed here"
            )
        if (problem := _fact_problem(f, con)) is not None:
            problems.append(problem)
    try:
        render(a, con)
    except (ContractError, KeyError) as e:
        problems.append(f"render failed: {e}")
    return problems


def _fidelity(g: Golden, a: Answer, con) -> Outcome:
    return _verdict("fidelity", answer_problems(a, con, _allowed_geos(g, a, con)))


def _verdict(check: str, problems: list[str]) -> Outcome:
    return Outcome(check, not problems, "; ".join(problems))


@cache
def program_names(rules: Path = RULES) -> dict[str, str]:
    return {
        (d := yaml.safe_load(p.read_text(encoding="utf-8")))["rule_id"]: d["program"]
        for p in Path(rules).glob("*.yaml")
    } | {p.program_id: p.name for p in catalog() if not p.rules}


def _plain(text: str) -> str:
    return unicodedata.normalize("NFC", strip_ignorable(text)).casefold()


def _has(low: str, phrase: str) -> bool:
    return re.search(rf"(?<!\w){re.escape(_plain(phrase))}(?!\w)", low) is not None


def _names_program(low: str, name: str) -> bool:
    """True when every word of the name outside parentheses, or a parenthesized acronym, appears."""
    outside = re.sub(r"\([^)]*\)", " ", name)
    words = re.findall(r"[^\W\d_]+", _plain(outside))
    acronyms = [x for x in re.findall(r"\(([^)\s\d]+)\)", name) if x.upper() == x]
    return (bool(words) and all(_has(low, w) for w in words)) or any(_has(low, x) for x in acronyms)


def program_named(text: str, name: str) -> bool:
    """True when rendered text names the program the way the programs check expects."""
    return _names_program(_plain(text), name)


def cv(f) -> float | None:
    if f.moe is None or not f.value or math.isnan(f.moe) or math.isnan(f.value):
        return None
    return (f.moe / 1.645) / abs(f.value)


def _reliability(a: Answer, low: str) -> Outcome:
    shown = _shown_facts(a)
    shaky = [f.ref for f in a.facts if f.ref in shown and (c := cv(f)) is not None and c > CV_LIMIT]
    if not shaky:
        return Outcome("reliability", None, "no shown fact has CV above 0.30")
    ok = _has(low, "unreliable")
    return Outcome(
        "reliability", ok, "" if ok else f"CV above 0.30 for {', '.join(shaky)} without unreliable"
    )


def _sourcing(a: Answer) -> Outcome:
    on = [m for t in [*a.blocks, *a.caveats] for m in _markers(t)[0]]
    by_ref = {f.ref: f for f in a.facts}
    shown = {by_ref[r].source_id for k, r in on if k == "fact" and r in by_ref}
    dated = {by_ref[r].source_id for k, r in on if k == "vintage" and r in by_ref}
    if not shown:
        return Outcome("sourcing", None, "no facts shown")
    return _verdict("sourcing", [f"no vintage shown for source {s}" for s in sorted(shown - dated)])


def direction_claims(text: str) -> list[tuple[tuple[str, str], str, tuple[str, str]]]:
    """(subject geo, direction, object geo) claims in marker text; a sentence with no subject takes the last topic."""
    claims, topic = [], None
    for sentence in re.split(r"[.!?;\n]+", text):
        subject = pending = first = None
        for m in CLAIM_TOKEN.finditer(sentence):
            if m.group(3):
                subject = subject or topic
                pending = PHRASE_DIRECTION[m.group(3).casefold()] if subject else None
            elif pending:
                claims.append((subject, pending, (m.group(1), m.group(2))))
                pending = None
            else:
                subject = (m.group(1), m.group(2))
                first = first or subject
        topic = first or topic
    return claims


def _direction(g: Golden, a: Answer, low: str) -> Outcome:
    if g.direction is None:
        return Outcome("direction", None, "no expected direction")
    pair = [(t, i) for _, t, i in g.facts[:2]]
    said = [
        d if (s, o) == tuple(pair) else FLIP[d]
        for s, d, o in direction_claims("\n".join([*a.blocks, *a.caveats]))
        if len(pair) == 2 and {s, o} == set(pair) and s != o
    ]
    if said:
        wrong = sorted({d for d in said if d != g.direction})
        return _verdict("direction", [f"says {', '.join(wrong)} but expected {g.direction}"] if wrong else [])
    problems = []
    if not any(_has(low, w) for w in DIRECTION_WORDS[g.direction]):
        problems.append(f"no {g.direction} wording ({', '.join(DIRECTION_WORDS[g.direction])})")
    if g.direction != "within_margin" and (
        found := [w for w in DIRECTION_WORDS[FLIP[g.direction]] if _has(low, w)]
    ):
        problems.append(f"says {', '.join(found)} but expected {g.direction}")
    return _verdict("direction", problems)


def score(g: Golden, a: Answer, con, programs: dict[str, str] | None = None) -> list[Outcome]:
    names = program_names() if programs is None else programs
    try:
        text = render(a, con)
    except (ContractError, KeyError):
        text = "\n\n".join(MARKER.sub(" ", t) for t in [*a.blocks, *a.caveats])
    low = _plain(text)
    out = [_fidelity(g, a, con)]
    if g.district is None:
        out.append(Outcome("district", None, "no expected district"))
    else:
        got = a.resolved
        out.append(_verdict("district", [] if got == g.district else [f"expected {g.district}, got {got}"]))
    if not g.facts:
        out.append(Outcome("facts", None, "no required facts"))
    else:
        used = _shown_facts(a)
        have = {(f.indicator_id, f.geo_type, f.geo_id) for f in a.facts if f.ref in used}
        out.append(_verdict("facts", [f"missing {t}" for t in g.facts if tuple(t) not in have]))
    if not g.must_say:
        out.append(Outcome("must_say", None, "none required"))
    else:
        out.append(_verdict("must_say", [f"missing {s!r}" for s in g.must_say if _plain(s) not in low]))
    if not g.must_not_say:
        out.append(Outcome("must_not_say", None, "none forbidden"))
    else:
        out.append(_verdict("must_not_say", [f"found {s!r}" for s in g.must_not_say if _plain(s) in low]))
    if not g.programs and not g.programs_forbidden:
        out.append(Outcome("programs", None, "no program expectations"))
    else:
        problems = [f"missing program {p}" for p in g.programs if p not in a.programs]
        problems += [
            f"program {p} not named in the text as {names.get(p, p)!r}"
            for p in g.programs
            if p not in names or not _names_program(low, names[p])
        ]
        problems += [f"forbidden program {p}" for p in g.programs_forbidden if p in a.programs]
        out.append(_verdict("programs", problems))
    out.append(
        _verdict("refusal", [] if a.refusal == g.refusal else [f"expected {g.refusal}, got {a.refusal}"])
    )
    spell = strip_ignorable(text)
    wrong = misspellings(spell)
    ok = not wrong and hawaiian(spell) == spell
    out.append(Outcome("spelling", ok, "" if ok else f"misspelled or unmarked: {wrong or 'not normalized'}"))
    out += [_reliability(a, low), _sourcing(a), _direction(g, a, low)]
    return out
