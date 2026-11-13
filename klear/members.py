# Legislators listed for each district, found by district or by exact name, with the note every mention carries.
from dataclasses import dataclass
from datetime import date

from klear.etl.places_gazetteer import search_plain

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


def named_in(con, question: str) -> list[Member]:
    """Members whose whole surname appears in a question, narrowed by a given name or title it also holds."""
    words = search_plain(question).split()
    found = [m for m in all_members(con) if _contains(words, m.surname)]
    given = [m for m in found if (m.words - set(m.surname) - SUFFIXES) & set(words)]
    if given:
        found = given
    chambers = {c for c, said in CHAMBER_WORDS.items() if said & set(words)}
    if len(chambers) == 1 and any(m.geo_type in chambers for m in found):
        found = [m for m in found if m.geo_type in chambers]
    return found
