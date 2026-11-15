# Legislators listed for each district, found by district or by exact name, with the note every mention carries.
import re
import unicodedata
from dataclasses import dataclass, field
from datetime import date

from klear.etl.places_gazetteer import display_name, search_plain
from klear.text import OKINA

GENERAL_ELECTION = date(2026, 11, 3)
RESULTS_URL = "https://elections.hawaii.gov/election-results/"
TITLES = {"house": "Representative", "senate": "Senator"}
SUFFIXES = frozenset({"jr", "sr", "ii", "iii", "iv"})
# Words around a name that are not part of it, such as a title or the chamber.
TITLE_WORDS = frozenset(
    {"representative", "rep", "senator", "sen", "state", "member", "legislator", "the", "hon", "honorable"}
)
CHAMBER_WORDS = {
    "house": frozenset({"representative", "representatives", "rep", "reps", "house"}),
    "senate": frozenset({"senator", "senators", "sen", "senate"}),
}
# Words around a name in a question that are never part of it.
AROUND_NAME = frozenset({
    "who", "whom", "whose", "which", "what", "where", "when", "how", "does", "did", "do", "is", "are", "was",
    "were", "can", "could", "would", "will", "should", "the", "a", "an", "my", "our", "your", "their", "of", "for",
    "to", "from", "about", "in", "on", "at", "by", "with", "and", "or", "s", "tell", "me", "please", "show", "give",
    "find", "ask", "contact", "reach", "call", "email", "office", "district", "districts", "represent",
    "represents", "representing", "serve", "serves", "serving", "hold", "holds", "seat", "number", "no", "hawaii",
    "i", "we", "you", "it", "that", "this", "now", "currently", "today",
})  # fmt: skip
# A question about who holds a seat, or which seat a member holds.
REPRESENTS = re.compile(
    r"\b(?:represent\w*|reps?|senators?|legislators?|lawmakers?|members? of the (?:house|senate|legislature))\b",
    re.IGNORECASE,
)
DISTRICT_IN = re.compile(
    r"\b(house|senate|hd|sd)\s*(?:district\s*|dist\.?\s*)?(?:no\.?\s*|#\s*)?(\d{1,2})\b", re.IGNORECASE
)
# Districts a town spans beyond this many are too many to list members for.
MAX_SEATS = 8
MEMBER_SQL = (
    "SELECT m.geo_type, m.geo_id, m.name, m.profile_url, m.listed_on, m.retrieved_on, s.name, s.page_url "
    "FROM member m JOIN source s USING (source_id)"
)


@dataclass(frozen=True)
class Member:
    geo_type: str
    geo_id: str
    name: str
    profile_url: str
    listed_on: date
    retrieved_on: date
    source: str
    page_url: str

    @property
    def geo(self) -> str:
        return f"{self.geo_type}:{self.geo_id}"

    @property
    def title(self) -> str:
        return TITLES[self.geo_type]

    @property
    def listing(self) -> str:
        """The listing's title, such as Representatives at a Glance."""
        return self.source.split(",")[0]

    @property
    def surname(self) -> list[str]:
        return [w for w in search_plain(self.name.split(",")[0]).split() if w not in SUFFIXES]

    @property
    def words(self) -> set[str]:
        return set(search_plain(self.name).split())


def long_date(d: date) -> str:
    return f"{d:%B} {d.day}, {d.year}"


def _member(row) -> Member:
    return Member(*row[:4], date.fromisoformat(row[4]), date.fromisoformat(row[5]), *row[6:])


def all_members(con) -> list[Member]:
    return [_member(r) for r in con.execute(MEMBER_SQL + " ORDER BY m.geo_type, m.geo_id, m.name").fetchall()]


def members_of(con, geo_type: str, geo_id: str) -> list[Member]:
    rows = con.execute(
        MEMBER_SQL + " WHERE m.geo_type = ? AND m.geo_id = ? ORDER BY m.name", [geo_type, geo_id]
    ).fetchall()
    return [_member(r) for r in rows]


def as_listed(m: Member) -> str:
    """The note on where and when the name was read, as in: as listed on <source> on <date>."""
    return f"as listed on the Legislative Reference Bureau's {m.listing} on {long_date(m.retrieved_on)}"


def timing(m: Member) -> str:
    """When the listing may predate the general election results, said plainly."""
    when = long_date(GENERAL_ELECTION)
    if m.listed_on < GENERAL_ELECTION:
        return (
            f"The listing is dated {long_date(m.listed_on)}, before the {when} general election, so "
            "it may not show members elected then. Election results are at elections.hawaii.gov."
        )
    return (
        f"The listing is dated {long_date(m.listed_on)} and may predate the {when} general election "
        "results taking effect. Election results are at elections.hawaii.gov."
    )


def mention(members: list[Member]) -> str:
    """The district's listed members with the as-listed note, such as: Senator DOE, Jane (as listed on ... on ...)."""
    if not members:
        return ""
    names = " and ".join(f"{m.title} {m.name}" for m in members)
    return f"{names} ({as_listed(members[0])})"


def _query(text: str) -> tuple[list[str], set[str]]:
    """The words of a name as typed, without titles, and the chambers its title names."""
    words = search_plain(text).split()
    chambers = {c for c, said in CHAMBER_WORDS.items() if said & set(words)}
    skip = TITLE_WORDS | CHAMBER_WORDS["house"] | CHAMBER_WORDS["senate"]
    return [w for w in words if w not in skip], chambers


def _contains(words: list[str], part: list[str]) -> bool:
    n = len(part)
    return n > 0 and any(words[i : i + n] == part for i in range(len(words) - n + 1))


def by_name(con, text: str) -> list[Member]:
    """Members whose listed name holds every typed word and their whole surname, ignoring case, ʻokina and
    kahakō. Exact words only: no near spellings. A title such as Senator keeps that chamber's members."""
    words, chambers = _query(text)
    if not words:
        return []
    found = [m for m in all_members(con) if _contains(words, m.surname) and set(words) <= m.words | SUFFIXES]
    return [m for m in found if m.geo_type in chambers] if chambers and len(chambers) == 1 else found


def _typed_words(text: str) -> tuple[list[str], list[bool]]:
    """The words search_plain reads in text, and whether each was typed with a capital, or all False when the
    text is in one case and so says nothing about names."""
    words = search_plain(text).split()
    cased = unicodedata.normalize("NFKD", display_name(text))
    cased = "".join(c for c in cased if not unicodedata.combining(c) and c != OKINA)
    typed = re.sub(r"[^A-Za-z0-9]+", " ", cased.replace("-", " ")).split()
    if [w.lower() for w in typed] != words or text == text.lower() or text == text.upper():
        return words, [False] * len(words)
    return words, [w[0].isupper() for w in typed]


def _is_place(con, word: str) -> bool:
    """Whether a word is itself a stored town or area name."""
    tables = {r[0] for r in con.execute("SELECT table_name FROM information_schema.tables").fetchall()}
    for table in ("place", "area", "area_alias"):
        if table in tables and con.execute(f"SELECT 1 FROM {table} WHERE plain = ?", [word]).fetchone():
            return True
    return False


def _name_words(words: list[str], capital: list[bool], start: int, step: int) -> list[int]:
    """Positions of the run of name-like words next to start, going step: not titles or question words, and
    capitalized when the question's case says so."""
    skip = AROUND_NAME | TITLE_WORDS | CHAMBER_WORDS["house"] | CHAMBER_WORDS["senate"]
    cased = any(capital)
    out, i = [], start
    while 0 <= i < len(words) and words[i] not in skip and (not cased or capital[i] or len(words[i]) == 1):
        out.append(i)
        i += step
    return out


def _title_before(words: list[str], i: int) -> str | None:
    """The chamber a title right before position i names, such as house for Rep."""
    if i <= 0:
        return None
    return next(
        (c for c in ("house", "senate") if words[i - 1] in CHAMBER_WORDS[c] - {"house", "senate"}), None
    )


def _surname_at(words: list[str], k: int, surname: list[str]) -> bool:
    """Whether surname starts at words[k], also as a possessive typed with a plain apostrophe, such as Kaais."""
    n = len(surname)
    return words[k : k + n - 1] == surname[:-1] and words[k + n - 1] in (surname[-1], f"{surname[-1]}s")


def named_in(con, question: str) -> list[Member]:
    """Members a question names: their whole surname with any given names typed next to it all theirs, or a
    surname alone after a title such as Representative or with no name-like word beside it. A title for the
    other chamber, or a given name that is not theirs, rules a member out, as by_name does."""
    words, capital = _typed_words(question)
    spans = []
    for m in all_members(con):
        given = m.words - set(m.surname) - SUFFIXES
        n = len(m.surname)
        for i in (k for k in range(len(words) - n + 1) if _surname_at(words, k, m.surname)):
            before = _name_words(words, capital, i - 1, -1)
            after = _name_words(words, capital, i + n, 1)
            typed = {words[k] for k in before + after} - SUFFIXES
            if typed - given:
                continue
            first = min(before, default=i)
            title = _title_before(words, first)
            if title is not None and title != m.geo_type:
                continue
            if not typed and title is None:
                if any(capital) and not capital[i]:
                    continue
                if n == 1 and _is_place(con, m.surname[0]):
                    continue
            spans.append((m, first, max(after, default=i + n - 1)))
    found = [
        m
        for m, a, b in spans
        if not any(o is not m and x <= a and b <= y and (x, y) != (a, b) for o, x, y in spans)
    ]
    found = list(dict.fromkeys(found))
    chambers = {c for c, said in CHAMBER_WORDS.items() if said & set(words)}
    if len(chambers) == 1 and any(m.geo_type in chambers for m in found):
        found = [m for m in found if m.geo_type in chambers]
    return found


@dataclass
class Seats:
    """The districts a question about members asks for, and what it called that area.

    kind is named (members named in the question), district (districts it names), place (a town or area) or
    selected (the chosen district). With a chamber the districts do not belong to, geos are that chamber's
    districts over them, of holds the districts asked about and shares each one's share of their residents."""

    kind: str
    area: str
    geos: list[tuple[str, str]]
    shares: dict[tuple[str, str], float] = field(default_factory=dict)
    members: list[Member] = field(default_factory=list)
    chamber: str | None = None
    choices: list[dict] = field(default_factory=list)
    note: str = ""
    of: list[tuple[str, str]] = field(default_factory=list)


def asked_chamber(question: str) -> str | None:
    """The one chamber a question asks for, apart from districts it names, such as senate for my senator."""
    words = set(search_plain(DISTRICT_IN.sub(" ", question)).split())
    chambers = {c for c, said in CHAMBER_WORDS.items() if said & words}
    return chambers.pop() if len(chambers) == 1 else None


def _over(con, geo: tuple[str, str], chamber: str) -> list[tuple[tuple[str, str], float]]:
    """Districts of chamber that hold residents of geo, with their share of geo's residents, largest first."""
    rows = con.execute(
        f"SELECT {chamber}, sum(pop2020) FROM blocks WHERE {geo[0]} = ? AND pop2020 > 0 GROUP BY 1 "
        "ORDER BY 2 DESC, 1",
        [geo[1]],
    ).fetchall()
    total = sum(p for _, p in rows) or 1
    return [((chamber, g), p / total) for g, p in rows if g]


def asked_seats(session, question: str, context: tuple[str, str] | None, skip=frozenset()) -> Seats | None:
    """The seats a question about members asks for: members it names, else districts it names, else the
    districts a town or area it names overlaps, else the chosen district, or the other chamber's districts over
    it when the question asks for that chamber. None when none applies. session resolves places; skip holds
    words that never name a place."""
    con = session.con
    if named := named_in(con, question):
        return Seats("named", "", list(dict.fromkeys((m.geo_type, m.geo_id) for m in named)), members=named)
    chamber = asked_chamber(question) if REPRESENTS.search(question) else None
    geos = []
    for m in DISTRICT_IN.finditer(question):
        c = "senate" if m.group(1).casefold() in ("senate", "sd") else "house"
        geos.append((c, f"{int(m.group(2)):03d}"))
    if geos:
        stored = [g for g in dict.fromkeys(geos) if _stored(con, g)]
        if not stored:
            n = con.execute("SELECT count(*) FROM geography WHERE geo_type = ?", [geos[0][0]]).fetchone()[0]
            note = f"Hawaiʻi has {n} {geos[0][0].title()} districts, numbered 1 to {n}."
            return Seats("district", "", [], note=note)
        return _from_districts(con, "district", stored, chamber)
    words = AROUND_NAME | TITLE_WORDS | CHAMBER_WORDS["house"] | CHAMBER_WORDS["senate"] | set(skip)
    found = session.place_named(question, frozenset(words))
    if found is not None:
        matches = found.get("matches") or []
        if len(matches) > 1:
            return Seats("place", "", [], choices=matches)
        if not matches:
            return Seats("place", "", [], note=found.get("note") or "")
        match = matches[0]
        shares = {}
        for o in match.get("overlaps", []):
            g = tuple(o["geo"].split(":"))
            if g[0] in ("house", "senate") and (chamber is None or g[0] == chamber):
                shares[g] = o.get("share", 1.0)
        if match["kind"] == "district":
            return _from_districts(con, "district", [tuple(match["geo"].split(":"))], chamber)
        return Seats("place", match["name"], list(shares), shares=shares, chamber=chamber)
    if context is None or context[0] not in ("house", "senate"):
        return None
    return _from_districts(con, "selected", [context], chamber)


def _stored(con, geo: tuple[str, str]) -> bool:
    return bool(
        con.execute("SELECT 1 FROM geography WHERE geo_type = ? AND geo_id = ?", list(geo)).fetchone()
    )


def _from_districts(con, kind: str, geos: list[tuple[str, str]], chamber: str | None) -> Seats:
    if chamber is None or all(g[0] == chamber for g in geos):
        return Seats(kind, "", geos, chamber=chamber, of=geos)
    shares = dict(x for g in geos for x in ([(g, 1.0)] if g[0] == chamber else _over(con, g, chamber)))
    return Seats(kind, "", list(shares), shares=shares, chamber=chamber, of=geos)
