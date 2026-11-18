"""Free, deterministic answers from the validated local fact store."""

import json
import re

from klear.agent.answer import (
    DIRECTION_TEXT,
    STATEWIDE,
    actual_direction,
    main_county,
    tied_programs,
    unpublished_years,
)
from klear.agent.config import (
    COUNTY_NAMES,
    COUNTY_SECTION,
    EDUCATION,
    FIXED_INCOME,
    FIXED_INCOME_COMPARED,
    FIXED_INCOME_PROGRAMS,
    INDUSTRIES,
    OCCUPATIONS,
    PROFILE_INDICATORS,
    PROFILE_LARGEST,
)
from klear.agent.tools import NAME_WORDS, STATE_WORDS, Session, ToolError
from klear.etl.places_gazetteer import search_plain
from klear.eval.contract import geo_display
from klear.labels import in_sentence, meaning, short_label
from klear.members import MAX_SEATS, REPRESENTS, Seats, as_listed, asked_seats, members_of, named_in, timing
from klear.web.display import (
    CAUTION,
    _plain,
    _spread,
    build_month,
    matched_programs,
    member_cards,
    place_caveats,
    program_rows,
    verified,
    year_text,
)
from klear.web.geocode import address_in

PERSONAL = re.compile(
    r"\b(?:a specific person|an individual|named resident|home address|phone number|personal information|who lives at|identify a resident|my neighbor|specific resident|(?:household|family|owner|tenant|occupant)s? (?:at|of|living at))\b",
    re.IGNORECASE,
)
PARTISAN = re.compile(
    r"\b(?:vote for|vote against|win (?:an? )?election|campaign|candidate|ballot|persuade voters|target voters|voter|which party|political party|democrat|republican|partisan)\b",
    re.IGNORECASE,
)
WORDS = re.compile(r"[a-z0-9]+")
STOP = {
    "the",
    "and",
    "for",
    "this",
    "that",
    "district",
    "what",
    "how",
    "many",
    "about",
    "are",
    "does",
    "have",
    "with",
    "from",
    "here",
    "there",
    "people",
    "residents",
    "a",
    "an",
    "is",
    "of",
    "in",
    "on",
    "to",
    "at",
    "by",
    "or",
    "as",
    "it",
    "be",
    "do",
    "i",
    "me",
    "my",
    "we",
    "you",
    "your",
    "can",
    "will",
    "who",
    "when",
    "where",
    "why",
    "which",
    "tell",
    "give",
    "show",
    "write",
    "please",
    "any",
    "some",
    "each",
    "more",
    "less",
    "than",
    "much",
    "very",
    "not",
    "one",
    "last",
    "live",
    "lives",
    "get",
    "good",
    "year",
    "time",
}
# Words that name a kind of measure rather than a topic, so they never match a label on their own.
GENERIC = {"rate", "rates", "percent", "percentage", "share", "number", "total", "count", "median", "average"}
# Words a follow-up uses to point back or to other places, which never name a measure.
POINTING = {
    "compare",
    "compared",
    "comparison",
    "versus",
    "vs",
    "against",
    "county",
    "counties",
    "state",
    "statewide",
    "hawaii",
    "island",
    "whole",
    "those",
    "these",
    "them",
    "same",
    "else",
    "too",
    "also",
    "now",
}
# Questions that rank or compare every district, which only the assistant answers.
RANKING = re.compile(
    r"\b(?:which|what)\s+(?:(?:house|senate)\s+)?districts\b"
    r"|\bdistricts?\s+(?:with|has|have)\s+the\s+(?:highest|lowest|most|fewest|largest|smallest)\b"
    r"|\b(?:rank|ranks|ranked|ranking)\b.*\bdistricts\b|\btop\s+(?:\w+\s+)?districts\b",
    re.IGNORECASE,
)
# Words that set the subject against another place, so a statewide phrase adds the state instead of replacing it.
COMPARING = re.compile(
    r"\b(?:compar\w*|versus|vs|against|than|relative\s+to|next\s+to|alongside)\b", re.IGNORECASE
)
CHAMBERS = ("house", "senate")
STATE = ("state", "15")
# Words that point back at the two places being compared: these two districts, both of them, each of them.
BOTH = re.compile(
    r"\b(?:these|those|the|both)\s+(?:two|2)\b|\bboth\s+(?:districts?|places|of\s+them)\b|\beach\s+of\s+them\b",
    re.IGNORECASE,
)
# Words that point at the district the reader chose: this district, my district, here.
HERE = re.compile(r"\b(?:this|my|our|selected|current)\s+district\b|\bhere\b", re.IGNORECASE)
# How a question names a district's chamber, and the words that may stand between it and the number.
CHAMBER_NAMES = {"house": "house", "hd": "house", "senate": "senate", "sd": "senate"}
DISTRICT_NOUNS = {"district", "districts", "dist", "no", "number"}
# Words that join a second number to a district already named, as in House Districts 49 and 50.
NUMBER_JOINS = {"and", "or", "vs", "versus"}
# Words after a number that make it an age, a share or a year rather than a district.
NOT_A_DISTRICT = {"percent", "year", "years", "older", "over", "plus", "and over"}
# Words that, beside the state's name, mean the whole state rather than a county or island.
STATE_BEFORE = {"the", "whole", "entire"}
STATE_AFTER = {"as", "average", "overall", "of", "wide", "figure", "figures", "rate"}
# The word for each kind of place, in the line under figures shown side by side.
KIND_WORDS = {"house": "district", "senate": "district", "county": "county", "state": "state"}
# A share of residents at or above this reads as all of them, and an island holding this much of a county's
# residents is answered with the county's figures.
WHOLE = 0.995
ISLAND_COUNTY = 0.99
ONE_AT_A_TIME = (
    "The basic lookup answers this kind of question for one place at a time. Ask about each district on its own, "
    "or set them side by side in Compare, which shows every profile figure for both."
)
NO_PAIR_MEASURE = (
    "I could not match that question to a published district measure, so I cannot compare these places on it. "
    "Try asking about population, kūpuna, income, poverty, housing, work or health, or open Compare to see every "
    "profile figure side by side."
)
NAME_A_PLACE = (
    "Name a place in your question, such as a House or Senate district, a town, a ZIP code or a street "
    "address, or choose a district above. KLEAR will answer for it."
)
PROGRAMS_NEED_DISTRICT = (
    "Programs are matched to one House or Senate district. Choose a district above, or name one in your "
    "question."
)
KUPUNA = ("kūpuna", "kupuna", "senior", "older adult", "65")
# Topics answered by one measure family, checked before the broad topics: stems a question must hold, then indicators.
TOPICS = (
    (
        ("industr", "tourism", "tourist", "hotel", "construction", "public administration", "government job"),
        INDUSTRIES,
    ),
    (
        (
            "occupation",
            "kind of job",
            "kinds of job",
            "type of job",
            "types of job",
            "what jobs",
            "which jobs",
        ),
        OCCUPATIONS,
    ),
    (("degree", "college", "educat", "diploma", "high school", "graduat"), EDUCATION),
    (("food insecur", "food-insecur", "hunger", "hungry"), ("places_food_insecurity", "pct_hh_snap")),
    (("housing insecur", "housing-insecur"), ("places_housing_insecurity", "pct_rent_burden_35_plus")),
    (("shut off", "shut-off", "shutoff", "utility", "utilities"), ("places_utility_shutoff_threat",)),
    (("transportation", "transit", "rides"), ("places_lack_transport",)),
    (("public housing",), ("units_public_housing", "sites_public_housing")),
    (("voucher", "section 8"), ("hud_vouchers", "pct_rent_burden_35_plus")),
    (("snap", "food stamp"), ("pct_hh_snap", "hh_snap", "hh_poverty_no_snap")),
)

# Groups of shares answered as one list, largest first: the list's opening and what every share counts.
GROUPS = {
    INDUSTRIES: (
        "Industries where working residents of {place} have jobs, largest share first",
        "These shares count residents by the industry they work in, wherever the job is.",
    ),
    OCCUPATIONS: (
        "Kinds of jobs working residents of {place} do, largest share first",
        "These shares count the kind of work residents do, in any industry.",
    ),
}


def _has(q, *stems):
    """Whether any word in q starts with one of the stems."""
    return any(re.search(r"\b" + re.escape(stem), q) for stem in stems)


def asks_profile(question: str) -> bool:
    """Whether a question asks for the district's standard profile."""
    return _has(question.casefold(), "profile", "socioeconomic", "overview")


def asks_fixed_income(question: str) -> bool:
    """Whether a question asks about kūpuna on fixed incomes: retirement income, or kūpuna income and poverty."""
    q = question.casefold()
    if _has(q, "fixed income", "fixed-income", "social security", "pension", "retire"):
        return True
    return _has(q, *KUPUNA) and _has(q, "income", "poverty", "poor", "ssi", "supplemental security", "afford")


def asks_programs(question: str) -> bool:
    """Whether a question asks which programs or benefits could help, rather than about one measure."""
    q = question.casefold()
    words = ("program", "benefit", "assistance", "support", "help available")
    return any(w in q for w in words) and not any(_has(q, *stems) for stems, _ in TOPICS)


def indicators(con, question):
    """Indicator ids for a question: every profile figure for a profile question, else up to three, with
    topic keywords first and then words shared with labels."""
    q = question.casefold()
    if asks_profile(q):
        return [*PROFILE_INDICATORS, *COUNTY_SECTION[1]]
    for stems, ids in TOPICS:
        if _has(q, *stems):
            return list(ids)
    if _has(q, *KUPUNA):
        return ["pop_65_plus", "pct_65_plus", "pct_65_plus_poverty"]
    if _has(q, "poverty", "low income"):
        return ["pct_people_poverty", "pct_children_poverty", "pct_65_plus_poverty"]
    if _has(q, "eitc", "earned income", "excise", "renters credit", "state tax credit"):
        return ["irs_eitc_returns", "dotax_eitc_claims", "dotax_food_excise_claims", "dotax_renters_claims"]
    if _has(q, "housing", "rent", "cost burden"):
        return ["median_gross_rent", "pct_rent_burden_35_plus", "pct_owner_cost_burden_35_plus"]
    if _has(q, "income", "wage", "earn"):
        return ["median_household_income", "per_capita_income", "pct_people_poverty"]
    if _has(q, "unemploy", "jobs", "work"):
        return ["unemployment_rate", "bls_unemployment_rate", "pct_in_labor_force", "pct_work_from_home"]
    if _has(q, "health", "uninsured", "disabil"):
        return ["pct_uninsured", "pct_disability", "pct_public_coverage"]
    if _has(q, "child", "keiki", "young"):
        return ["pop_under_18", "pct_children_poverty", "pct_hh_with_children"]
    if _has(q, "population", "people", "residents"):
        return ["pop_total", "pop_under_18", "pop_65_plus"]
    terms = set(WORDS.findall(search_plain(question))) - STOP - POINTING
    if not terms - GENERIC:
        return []
    rows = con.execute("SELECT indicator_id, label FROM indicator ORDER BY indicator_id").fetchall()
    ranked = []
    for key, label in rows:
        shared = terms & set(WORDS.findall(search_plain(label)))
        if shared - GENERIC:
            ranked.append((len(shared), key))
    return [key for _, key in sorted(ranked, reverse=True)[:3]]


def _split(con, ids: list[str], geo_type: str) -> tuple[list[str], list[str]]:
    """Indicators published for this kind of district, and county-level ones published only for counties."""
    covered = dict(
        con.execute("SELECT indicator_id, list(geo_type) FROM indicator_coverage GROUP BY 1").fetchall()
    )
    county = [i for i in ids if geo_type not in covered.get(i, []) and "county" in covered.get(i, [])]
    return [i for i in ids if i not in county], county


def program_answer(con, district, shown: str) -> dict:
    """Programs matched to the district's need figures, with the reason for each in words."""
    session = Session(con)
    raw = json.loads(session.run("district_programs", {"district": f"{district[0]}:{district[1]}"}))
    if "error" in raw:
        raise ValueError("Could not match programs to the district")
    laid = matched_programs(con, raw, session)
    names = [f"{p['name']} ({p['level']})" for p in laid["programs"]]
    if names:
        listed = ", ".join(names[:-1]) + (" and " if len(names) > 1 else "") + names[-1]
        lead = (
            f"Programs to explore for residents of {shown}: {listed}. They are grouped by need and ranked by how "
            "far the district's figures sit above the state's, and each group below gives its figure."
        )
    else:
        lead = (
            f"None of {shown}'s need figures sit clearly above the state's, so no program stands out here. "
            "These programs may help residents here."
        )
    return {
        "text": f"{lead}\n\n{laid['note']}",
        "refusal": "none",
        "facts": [],
        "programs": [p | {"match": True} for p in laid["programs"]]
        + [p | {"match": False} for p in laid["others"]],
        "program_groups": laid["groups"],
        "program_sources": laid["sources"],
        "place_caveats": place_caveats(con, [district]),
    } | verified(laid["figures"], len(laid["sources"]))


def _cards(con, facts: list[dict]) -> list[dict]:
    """Figure cards for get_facts entries: short label and what it means, value, year, source, place and
    uncertainty."""
    month, out = build_month(con), []
    universes = dict(con.execute("SELECT indicator_id, universe FROM indicator").fetchall())
    for fact in facts:
        row = con.execute(
            "SELECT s.name,s.page_url,f.moe_status FROM fact f JOIN source s USING(source_id) WHERE f.indicator_id=? AND f.geo_type=? AND f.geo_id=? AND f.source_id=?",
            [fact["indicator_id"], *fact["geo"].split(":"), fact["source_id"]],
        ).fetchone()
        out.append(
            {
                "label": short_label(fact["indicator_id"], fact["label"]),
                "meaning": meaning(fact["indicator_id"], fact["label"], universes.get(fact["indicator_id"])),
                "value": fact["value_text"],
                "year": year_text(fact["vintage"], month),
                "source": row[0],
                "url": row[1],
                "geography": fact["geo_name"],
                "uncertainty": row[2],
                "caution": CAUTION if fact["reliable"] is False else "",
            }
        )
    return out


def _joined(parts: list[str], word: str = "and") -> str:
    return parts[0] if len(parts) == 1 else f"{', '.join(parts[:-1])} {word} {parts[-1]}"


# How a kind of place reads when an answer lists the kinds it shows.
KINDS = {"house": "district", "senate": "district", "county": "county", "state": "state"}


def _kinds(facts: list[dict]) -> str:
    """The kinds of place among get_facts entries, like 'District, county and state'."""
    kinds = list(dict.fromkeys(KINDS.get(f["geo"].split(":")[0], "place") for f in facts))
    order = [*KINDS.values(), "place"]
    kinds.sort(key=order.index)
    return _joined(kinds).capitalize() if len(kinds) > 1 else ""


def modeled_caveat(names) -> str:
    """The caveat for figures that are CDC PLACES modeled estimates, named by their labels."""
    names = list(dict.fromkeys(names))
    one = len(names) == 1
    return (
        f"The {'figure' if one else 'figures'} for {_joined([in_sentence(n) for n in names])} "
        f"{'is a modeled estimate' if one else 'are modeled estimates'} from CDC PLACES survey answers, "
        "not a direct count."
    )


def _comparison(session, fact: dict, others: list[dict], unit: str) -> str:
    """How a district fact relates to each other place, like 'higher than Honolulu County (19.6%)'."""
    ways: dict[str, list[str]] = {}
    for other in others:
        theirs = session.facts[other["ref"]]
        if way := actual_direction(session.facts[fact["ref"]], theirs):
            ways.setdefault(way, []).append(f"{other['geo_name']} ({_plain(theirs, unit)})")
    return ", ".join(f"{DIRECTION_TEXT[w]} {_joined(p)}" for w, p in ways.items())


def _places(con, question: str, district: tuple[str, str]) -> list[tuple[str, str]]:
    """The district's main county and the state when the question asks to compare with them, or names the whole
    state beside the place, as in 'compare with Hawaiʻi as a whole'. None for the state itself."""
    if district == STATE:
        return []
    q = question.casefold()
    out = []
    if "county" in q and (county := main_county(con, district)):
        out.append(county)
    if "compare" in q or "state" in q or "hawaiʻi" in q or "hawaii" in q or STATEWIDE.search(question):
        out.append(STATE)
    return out


def group_answer(con, question: str, district: tuple[str, str], shown: str, ids: tuple[str, ...]) -> dict:
    """A group of shares, such as the industries residents work in, listed largest first, each beside the main
    county and the state when the question asks for them."""
    opening, counts = GROUPS[ids]
    places = _places(con, question, district)
    session = Session(con)
    order = session.largest(ids, district)
    here = json.loads(session.run("get_facts", {"indicator_ids": order, "geos": [":".join(district)]}))
    there = {"facts": []}
    if places and order:
        there = json.loads(
            session.run("get_facts", {"indicator_ids": order, "geos": [":".join(p) for p in places]})
        )
    if "error" in here or "error" in there:
        raise ValueError("Could not read district facts")
    if not here["facts"]:
        return {
            "text": f"I do not have a published figure for that question in {shown}. I will not fill the gap with a guess.",
            "refusal": "not_available",
            "facts": [],
            "programs": [],
        }
    units = dict(con.execute("SELECT indicator_id, unit FROM indicator").fetchall())
    against: dict[str, list[dict]] = {}
    for fact in there["facts"]:
        against.setdefault(fact["indicator_id"], []).append(fact)
    lines, shaky = [], []
    for fact in here["facts"]:
        name = short_label(fact["indicator_id"], fact["label"])
        line = f"- {name}: {fact['value_text']}"
        if fact["reliable"] is False:
            line += ", an unreliable estimate"
            shaky.append(in_sentence(name))
        if said := _comparison(
            session, fact, against.get(fact["indicator_id"], []), units[fact["indicator_id"]]
        ):
            line += f", {said}"
        lines.append(line)
    names = list(dict.fromkeys(f["geo_name"] for f in there["facts"]))
    head = opening.format(place=shown) + (f", compared with {_joined(names)}" if names else "") + "."
    lead = [head + "\n" + "\n".join(lines)]
    caveats = [counts]
    if shaky:
        caveats.append(
            f"The margin of error is large for {_joined(shaky)}, so treat "
            f"{'these estimates' if len(shaky) > 1 else 'this estimate'} as unreliable. "
            "A larger area, such as the county, gives a steadier figure."
        )
    facts = here["facts"] + there["facts"]
    return {
        "text": "\n\n".join(lead + caveats),
        "refusal": "none",
        "facts": _cards(con, facts),
        "programs": [],
        "fact_refs": [session.facts[f["ref"]] for f in facts],
        "lead": lead,
        "caveats": caveats,
    }


def fixed_income(con, district: tuple[str, str], shown: str) -> dict:
    """Kūpuna count and share, income, poverty and household income sources for a district, with the rates and
    medians set against its main county and the state, and the programs for kūpuna on fixed incomes."""
    session = Session(con)
    places = [g for g in (main_county(con, district), ("state", "15")) if g and g != district]
    here = json.loads(
        session.run("get_facts", {"indicator_ids": list(FIXED_INCOME), "geos": [":".join(district)]})
    )
    there = {"facts": []}
    if places:
        there = json.loads(
            session.run(
                "get_facts",
                {"indicator_ids": list(FIXED_INCOME_COMPARED), "geos": [":".join(p) for p in places]},
            )
        )
    if "error" in here or "error" in there:
        raise ValueError("Could not read district facts")
    if not here["facts"]:
        return {
            "text": f"I do not have published kūpuna income figures for {shown}. I will not fill the gap with a guess.",
            "refusal": "not_available",
            "facts": [],
            "programs": [],
        }
    units = dict(con.execute("SELECT indicator_id, unit FROM indicator").fetchall())
    labels = dict(con.execute("SELECT indicator_id, label FROM indicator").fetchall())
    mine = {f["indicator_id"]: f for f in here["facts"]}
    missing = {m["indicator_id"]: m["reason"] for m in here["missing"]}
    against: dict[str, list[dict]] = {}
    for fact in there["facts"]:
        against.setdefault(fact["indicator_id"], []).append(fact)
    lines, notes, shaky = [], {}, []
    for ind in FIXED_INCOME:
        name = short_label(ind, labels.get(ind, ind))
        if ind not in mine:
            if ind in missing:
                lines.append(f"- {name}: not available, {missing[ind]}")
            continue
        fact = mine[ind]
        line = f"- {name}: {fact['value_text']}"
        if fact["reliable"] is False:
            line += ", an unreliable estimate"
            shaky.append(in_sentence(name))
        if said := _comparison(session, fact, against.get(ind, []), units[ind]):
            line += f", {said}"
        lines.append(line)
        if note := fact.get("note"):
            notes.setdefault(note, []).append(name)
    names = list(dict.fromkeys(g["geo_name"] for g in there["facts"]))
    against_text = f" Rates and medians are compared with {_joined(names)}." if names else ""
    lead = [f"Kūpuna and fixed incomes in {shown}.{against_text}\n" + "\n".join(lines)]
    chosen = program_rows(list(FIXED_INCOME_PROGRAMS))
    lead.append(
        "Programs for kūpuna on fixed incomes, with official links below: "
        f"{_joined([p['name'] for p in chosen])}. These district figures cannot establish any person's eligibility."
    )
    caveats = [f"{note} Applies to: {', '.join(where)}." for note, where in notes.items()]
    if shaky:
        caveats.append(
            f"The margin of error is large for {_joined(shaky)}, so treat "
            f"{'these estimates' if len(shaky) > 1 else 'this estimate'} as unreliable. "
            "A larger area, such as the county, gives a steadier figure."
        )
    facts = here["facts"] + there["facts"]
    return {
        "text": "\n\n".join(lead + caveats),
        "refusal": "none",
        "facts": _cards(con, facts),
        "programs": chosen,
        "fact_refs": [session.facts[f["ref"]] for f in facts],
        "lead": lead,
        "caveats": caveats,
    }


# Who holds a district's seat, or which district a named member holds.
ASKS_WHO = re.compile(r"\b(?:who|whom|who's|name of)\b", re.IGNORECASE)
ASKS_WHICH = re.compile(r"\b(?:which|what)\s+(?:house\s+|senate\s+)?district", re.IGNORECASE)
SELECTED = re.compile(r"\b(?:my|our|this|here|selected|current)\b", re.IGNORECASE)
MEMBER_WORDS = {
    "house",
    "senate",
    "state",
    "hawaii",
    "is",
    "are",
    "who",
    "whom",
    "s",
    "name",
    "of",
    "currently",
}
NAME_THE_DISTRICT = (
    "Name the district, such as House District 22, or choose it on the page, and I will give the member the "
    "Legislature lists for it."
)
NO_SUCH_NAME = (
    "I could not find a listed member with that exact name. Names must match as the Legislature lists them, "
    "such as a full last name with the first name or a title such as Senator, and I do not guess near spellings."
)


def _about_selected(question: str) -> bool:
    """Whether a question about a member asks about the chosen district: it says my or this district, or names
    nothing else."""
    if SELECTED.search(question):
        return True
    words = set(WORDS.findall(question.casefold())) - STOP - MEMBER_WORDS
    return all(REPRESENTS.fullmatch(w) for w in words)


def _member_line(m, lead: str) -> str:
    return f"{lead}, {as_listed(m)}."


def _names(listed) -> str:
    return " and ".join(f"{m.title} {m.name}" for m in listed) or "no member in the listing KLEAR holds"


def _part(share: float) -> str:
    return "all" if share >= WHOLE else "most" if share >= 0.5 else "part"


def _seats_text(con, seats: Seats) -> tuple[str, list]:
    """Who holds each seat asked about, with the share of the area's residents each district holds when the area
    is a town or spans the other chamber's districts, and the members named."""
    listed = {g: members_of(con, *g) for g in seats.geos}
    named = [m for g in seats.geos for m in listed[g]]
    lists = {m.listing: m for m in named}
    said = " ".join(f"Names of {m.title.lower()}s are {as_listed(m)}." for m in lists.values())
    if seats.kind in ("district", "selected") and seats.of == seats.geos:
        lines = [
            f"{geo_display(*g, '')} is represented by {_names(listed[g])}"
            if listed[g]
            else f"No member is listed for {geo_display(*g, '')} in the Legislature listing KLEAR holds"
            for g in seats.geos
        ]
        if len(lines) == 1 and named:
            return _member_line(named[0], lines[0]), named
        return "\n".join(f"- {line.rstrip('.')}." for line in lines) + (f"\n\n{said}" if said else ""), named
    area = seats.area or _joined([geo_display(*g, "") for g in seats.of])
    where = {g: geo_display(*g, "") for g in seats.geos}
    if len(seats.geos) == 1:
        (g,) = seats.geos
        share = seats.shares.get(g, 1.0)
        how = "" if share >= WHOLE else "mostly " if share >= 0.5 else "partly "
        lead = f"{area} is {how}in {where[g]}, which is represented by {_names(listed[g])}"
        return (_member_line(named[0], lead) if named else f"{lead}."), named
    kind = f" {seats.chamber.title()}" if seats.chamber else ""
    lines = [
        f"- {where[g]}, with {_part(seats.shares.get(g, 1.0))} of {area}'s residents: {_names(listed[g])}"
        for g in seats.geos
    ]
    text = f"{area} spans more than one{kind} district. The Legislature lists these members for them:\n"
    return text + "\n".join(lines) + (f"\n\n{said}" if said else ""), named


def member_answer(con, question: str, district: tuple[str, str] | None, earlier=()) -> dict | None:
    """Who a Legislature listing names for the districts a question asks about, or the district of a member it
    names exactly, with where and when it was listed and whether the listing may predate the election results.
    A district, town or chamber the question names comes before the chosen district. None when not asked."""
    if not REPRESENTS.search(question):
        return None
    named = named_in(con, question)
    if not named and not ASKS_WHO.search(question):
        return _reply(NO_SUCH_NAME) if ASKS_WHICH.search(question) else None
    if named:
        lines = []
        for m in named:
            where = geo_display(m.geo_type, m.geo_id, "")
            lines.append(_member_line(m, f"{m.title} {m.name} represents {where}"))
        if len(named) > 1:
            text = "Several listed members match that name:\n" + "\n".join(f"- {line}" for line in lines)
        else:
            text = lines[0]
        notes = list(dict.fromkeys(timing(m) for m in named))
        geos = [(m.geo_type, m.geo_id) for m in named]
    else:
        if address_in(question):
            return None
        if STATEWIDE.search(question):
            return _reply(f"Each House and Senate district has its own member. {NAME_THE_DISTRICT}")
        subject = district or next((x.district for x in reversed(earlier) if x.district), None)
        seats = asked_seats(Session(con), question, subject, frozenset(STOP | GENERIC | POINTING))
        if seats is None:
            return _reply(NAME_THE_DISTRICT)
        if seats.kind == "selected" and not _about_selected(question):
            return _reply(f"{NAME_THE_DISTRICT} A town can span more than one district.")
        if seats.choices:
            names = _joined(_match_names(seats.choices), "or")
            return _reply(f"Your question could mean {names}. {NAME_THE_DISTRICT}")
        if seats.note:
            return _reply(f"{seats.note} {NAME_THE_DISTRICT}")
        if not seats.geos:
            return _reply(
                f"KLEAR does not hold a district for {seats.area or 'that place'}. {NAME_THE_DISTRICT}"
            )
        if len(seats.geos) > MAX_SEATS:
            counts = _joined(
                [f"{n} {c.title()} districts" for c in CHAMBERS if (n := sum(g[0] == c for g in seats.geos))]
            )
            area = seats.area or _joined([geo_display(*g, "") for g in seats.of])
            return _reply(f"{area} spans {counts}, too many to list here. {NAME_THE_DISTRICT}")
        text, listed = _seats_text(con, seats)
        notes = list(dict.fromkeys(timing(m) for m in listed))
        geos = list(seats.geos)
    out = {
        "text": "\n\n".join([text, *notes]),
        "refusal": "none",
        "facts": [],
        "programs": [],
        "members": member_cards(con, geos),
    }
    return out | ({"district": geos[0]} if len(set(geos)) == 1 else {})


CHAMBER_WORDS = re.compile(r"\b(?:house|senate|districts?)\b", re.IGNORECASE)


def _same_districts(matches: list[dict]) -> bool:
    return len({tuple(o["geo"] for o in m["overlaps"]) for m in matches}) == 1


def address_answer(con, question: str, address: str, geocoder) -> dict:
    """The districts of a street address from the Census geocoder, then the question's figures for its House
    district (its Senate district when only the Senate is asked about). Never a district guessed from a town."""
    found = Session(con, geocoder).find_address(address)
    matches = found["matches"]
    if not matches:
        text = f"{found['note']} KLEAR does not guess a district from a town name, because a town can span more than one district."
        return {"text": text, "refusal": "none", "facts": [], "programs": []}
    if not _same_districts(matches):
        lines = [f"- {m['name']}: " + _joined([o["name"] for o in m["overlaps"]]) for m in matches]
        text = (
            "The U.S. Census Bureau matched more than one address. Check which one you mean:\n"
            + "\n".join(lines)
        )
        return {"text": text, "refusal": "none", "facts": [], "programs": []}
    places = {o["geo"].split(":")[0]: (o["geo"], o["name"]) for o in matches[0]["overlaps"]}
    where = (
        f"The U.S. Census Bureau matched this address to {matches[0]['name']}. It is in {places['house'][1]} "
        f"and {places['senate'][1]}, in {places['county'][1]}. District lines are the 2022 boundaries in use today."
    )
    rest = question.replace(address, " ")
    words = set(WORDS.findall(rest.casefold()))
    chamber = "senate" if "senate" in words and "house" not in words else "house"
    q = rest.casefold()
    picked = tuple(places[chamber][0].split(":"))
    asks = asks_fixed_income(q) or any(w in q for w in ("program", "benefit", "assistance", "support"))
    if not (asks or indicators(con, CHAMBER_WORDS.sub(" ", rest))):
        return {"text": where, "refusal": "none", "facts": [], "programs": [], "district": picked}
    result = _answer(con, rest, picked)
    result["text"] = f"{where}\n\n{result['text']}"
    return result | {"where": where, "district": picked}


def _reply(text: str, **extra) -> dict:
    """An answer with no figures, such as a request to name a place."""
    return {"text": text, "refusal": "none", "facts": [], "programs": []} | extra


def _match_name(match: dict) -> str:
    return f"ZIP code {match['geo'].split(':')[1]}" if match["kind"] == "zcta" else match["name"]


def _match_names(matches: list[dict]) -> list[str]:
    """Distinct names of candidate places, each with its island when two share a name."""
    names = [_match_name(m) for m in matches]
    return list(
        dict.fromkeys(
            f"{n} on {m['island']}" if names.count(n) > 1 and m.get("island") else n
            for n, m in zip(names, matches, strict=True)
        )
    )


def place_of(con, question: str, selected, earlier=()) -> tuple[tuple[str, str] | None, str, dict | None]:
    """The place to answer for: one the question names, else the whole state for a question only about the state,
    else the selected district, else the place of the last answer. A statewide phrase in a comparison, such as
    'compare with Hawaiʻi as a whole', keeps the subject, and the state is added beside it. Also a line saying
    where a named town lies, or a reply to send instead of an answer: district choices when a named place spans
    several, or a request to name one."""
    session = Session(con)
    statewide = STATEWIDE.search(question)
    rest = STATEWIDE.sub(" ", question) if statewide else question
    found = session.place_named(rest, frozenset(STOP | GENERIC | POINTING))
    if found is None:
        # Programs are matched to one district, so a statewide program question keeps the chosen one.
        if statewide and not COMPARING.search(question) and not (selected and asks_programs(question)):
            return STATE, "", None
        last = next((x.district for x in reversed(earlier) if x.district), None)
        subject = selected or last or (STATE if statewide else None)
        return subject, "", None if subject else _reply(NAME_A_PLACE)
    matches = found.get("matches") or []
    if not matches:
        return None, "", _reply(f"{found.get('note') or 'KLEAR could not find that place.'} {NAME_A_PLACE}")
    first = matches[0]
    if whole := next((m for m in matches if m["kind"] in ("county", "state", "district")), None):
        return tuple(whole["geo"].split(":")), "", None
    if county := _island_county(first) if len(matches) == 1 else None:
        geo, share = county
        name = geo_display(*geo, _county_name(con, geo))
        how = "all" if share >= WHOLE else "almost all"
        return geo, f"{first['name']} holds {how} of {name}'s residents, so these are {name} figures.", None
    words = set(WORDS.findall(question.casefold()))
    chamber = next((c for c in CHAMBERS if c in words), selected[0] if selected else "house")
    shares = {o["geo"]: o.get("share", 1.0) for m in matches for o in m.get("overlaps", [])}
    if selected and (mine := ":".join(selected)) in shares:
        return selected, _lies_in(first, selected, shares[mine]) if len(matches) == 1 else "", None
    picked = [g for g in shares if g.startswith(f"{chamber}:")]
    if len(picked) == 1 and len(matches) == 1:
        geo = tuple(picked[0].split(":"))
        return geo, _lies_in(first, geo, shares[picked[0]]), None
    if not picked:
        return None, "", _reply(NAME_A_PLACE)
    lead = (
        f"{_match_name(first)} spans more than one {chamber.title()} district."
        if len(matches) == 1
        else f"Your question could mean {_joined(_match_names(matches), 'or')}."
    )
    return None, "", _reply(f"{lead} Choose a district below, and KLEAR will answer for it.", choices=matches)


def _lies_in(match: dict, geo: tuple[str, str], share: float) -> str:
    """Where a named place lies, like 'Kāneʻohe is partly in House District 49.'"""
    how = "" if share >= WHOLE else "mostly " if share >= 0.5 else "partly "
    return f"{_match_name(match)} is {how}in {geo_display(*geo, '')}."


def _island_county(match: dict) -> tuple[tuple[str, str], float] | None:
    """The county an island makes up nearly all of, such as Oʻahu and Honolulu County, with its share."""
    if match["kind"] != "island":
        return None
    counties = [o for o in match["overlaps"] if o["geo"].startswith("county:")]
    if len(counties) != 1 or counties[0].get("of_county", 0) < ISLAND_COUNTY:
        return None
    return tuple(counties[0]["geo"].split(":")), counties[0]["of_county"]


def _county_name(con, geo: tuple[str, str]) -> str:
    return con.execute("SELECT name FROM geography WHERE geo_type = ? AND geo_id = ?", list(geo)).fetchone()[
        0
    ]


def _shown(con, geo: tuple[str, str]) -> str:
    """A place's name as answers write it, like House District 22, Honolulu County or Hawaiʻi."""
    if geo == STATE:
        return "Hawaiʻi"
    return geo_display(*geo, _county_name(con, geo))


def _district_number(words: list[str], at: int) -> bool:
    """Whether the word at this position is a district number, not an age, a share or a year."""
    if at >= len(words) or not words[at].isdigit() or len(words[at]) > 2:
        return False
    after = " ".join(words[at + 1 : at + 3])
    return not any(after == w or after.startswith(w + " ") for w in NOT_A_DISTRICT)


def places_in(con, question: str, chamber: str | None = None) -> tuple[list[tuple[str, str]], list[str]]:
    """The districts, counties and the state a question names, in the order it names them, each once, and the
    towns, areas and ZIP codes it names, which can span several districts. A district number with no chamber word,
    as in 'compare this district with district 50', takes the chamber named before it, else chamber. Numbers
    joined to a district, as in House Districts 49 and 50, are districts of that chamber too."""
    words = search_plain(question).split()
    session, skip = Session(con), frozenset(STOP | GENERIC | POINTING)
    found: list[tuple[str, str]] = []
    towns: list[str] = []
    i = 0
    while i < len(words):
        word = words[i]
        span = next(
            (
                n
                for n in range(min(NAME_WORDS, len(words) - i), 0, -1)
                if " ".join(words[i : i + n]) in COUNTY_NAMES
            ),
            0,
        )
        if span:
            found.append(("county", COUNTY_NAMES[" ".join(words[i : i + span])]))
            i += span
            continue
        if word in CHAMBER_NAMES or word in ("district", "districts", "dist"):
            kind = CHAMBER_NAMES.get(word, chamber)
            at = i + 1
            while at < len(words) and words[at] in DISTRICT_NOUNS:
                at += 1
            named = False
            while kind and _district_number(words, at):
                found.append((kind, f"{int(words[at]):03}"))
                chamber, named = kind, True
                at += 1
                more = at + 1
                while more < len(words) and words[more] in DISTRICT_NOUNS:
                    more += 1
                if at < len(words) and words[at] in NUMBER_JOINS and _district_number(words, more):
                    at = more
                    continue
                break
            if named:
                i = at
                continue
        if ZIP_WORD.fullmatch(word):
            towns.append(word)
            i += 1
            continue
        span = next(
            (
                n
                for n in range(min(NAME_WORDS, len(words) - i), 0, -1)
                if not set(words[i : i + n]) <= skip | STATE_WORDS
                and session.is_name(" ".join(words[i : i + n]))
            ),
            0,
        )
        if span:
            towns.append(" ".join(words[i : i + span]))
            i += span
            continue
        before, after = words[i - 1] if i else "", words[i + 1] if i + 1 < len(words) else ""
        if (
            word == "statewide"
            or (word == "state" and (before in STATE_BEFORE or after in STATE_AFTER))
            or word == "hawaii"
        ):
            found.append(STATE)
        i += 1
    known = [g for g in dict.fromkeys(found) if g == STATE or session._name(*g) is not None]
    return known, list(dict.fromkeys(towns))


ZIP_WORD = re.compile(r"96[78]\d{2}")
# A question that asks about counties without naming one, as in 'compare them with the county'.
THE_COUNTY = re.compile(r"\bcount(?:y|ies)\b", re.IGNORECASE)


def comparison_places(
    con, question: str, selected=None, pair=(), earlier=()
) -> tuple[list[tuple[str, str]] | None, list[tuple[str, str]], dict | None]:
    """The places a comparison question sets side by side, in the order it names them: two or more districts or
    counties, the two districts the page compares when the question points at them ('these two districts', or a
    comparison that names no other place), or the chosen district beside one the question names. Then the places
    to show for context: the state, or the counties, when the question asks for them. None when the question is no
    such comparison. A reply instead when it compares a town or ZIP code, which can span several districts, so
    the basic lookup never answers for a place it was not asked about."""
    hint = selected[0] if selected and selected[0] in CHAMBERS else None
    found, towns = places_in(con, question, hint)
    subjects = [g for g in found if g != STATE]
    both = BOTH.search(question)
    if not (both or COMPARING.search(question) or len(subjects) >= 2):
        return None, [], None
    pair = [tuple(g) for g in pair or ()]
    named_county = any(g[0] == "county" for g in subjects)
    points_at_pair = both or (set(subjects) <= set(pair) and STATE not in found and not named_county)
    if len(subjects) < 2 and len(pair) == 2 and not towns and points_at_pair:
        subjects = list(dict.fromkeys([*subjects, *pair]))
    if len(subjects) < 2 and both and earlier:
        before = [g for g in places_in(con, earlier[-1].question, hint)[0] if g != STATE]
        if len(before) >= 2:
            subjects = before
    # "This district" or "here" is the chosen district, set beside the place the question names.
    here = [tuple(selected)] if selected and HERE.search(question) and tuple(selected) not in subjects else []
    if len(subjects) == 1 and here:
        subjects = [*here, subjects[0]]
    asked = subjects or here
    if towns and len(asked) + len(towns) >= 2:
        return None, [], _town_reply(con, asked, towns)
    # One district beside its own main county is the standard answer, which sets the district, its county and the
    # state side by side; another county is compared as a place of its own.
    districts = [g for g in subjects if g[0] in CHAMBERS]
    counties = [g for g in subjects if g[0] == "county"]
    if len(districts) == 1 and counties and set(counties) == {main_county(con, districts[0])}:
        return None, [], None
    if len(subjects) < 2:
        return None, [], None
    context = [STATE] if STATE in found else []
    if THE_COUNTY.search(question) and not named_county:
        counties = [c for g in subjects if (c := main_county(con, g))]
        context = list(dict.fromkeys(counties)) + context
    return subjects, context, None


def _town_reply(con, subjects, towns: list[str]) -> dict:
    """Why the basic lookup does not compare a town or ZIP code: it can span several districts."""
    session = Session(con)
    names = []
    for town in towns:
        if ZIP_WORD.fullmatch(town):
            names.append(f"ZIP code {town}")
            continue
        found = session.resolve_name(town)
        names.append(_match_names(found["matches"][:1])[0] if found.get("matches") else town)
    zips = sum(ZIP_WORD.fullmatch(t) is not None for t in towns)
    one = len(names) == 1
    kind = (
        ("a ZIP code" if zips else "a town")
        if one
        else "ZIP codes"
        if zips == len(names)
        else "towns"
        if not zips
        else "towns and ZIP codes"
    )
    text = (
        f"The basic lookup compares House and Senate districts, counties and the state. {_joined(names)} "
        f"{'is' if one else 'are'} {kind}, which can span more than one district, so I will not pick a district "
        f"for {'it' if one else 'them'}. Search for each place to find its districts, then compare the districts."
    )
    districts = [g for g in subjects if g[0] in CHAMBERS]
    return _reply(text, actions=[compare_action(con, districts)])


def compare_action(con, districts) -> dict:
    """A link that opens Compare with these districts, or with empty searches when none is known."""
    geos = [":".join(g) for g in districts[:2]]
    if len(geos) == 2:
        text = f"See {_shown(con, districts[0])} and {_shown(con, districts[1])} side by side in Compare"
    else:
        text = "Open Compare to set two districts side by side"
    return {"text": text, "url": "/compare" + (f"?d={','.join(geos)}" if geos else "")}


def _pair_value(f, unit: str) -> str:
    return f"{_plain(f, unit)} {_spread(f, unit)}".strip()


def _difference(a_name: str, b_name: str, way: str | None) -> str:
    """What the margins of error say about two places' figures, in words."""
    if way == "within_margin":
        return f"{a_name} and {b_name} are within each other's margins of error, so the data show no clear difference."
    if way in ("higher", "lower"):
        return f"{a_name} is {way} than {b_name}, beyond the margins of error."
    return ""


def compare_answer(con, question: str, subjects, context, earlier=()) -> dict:
    """Two or more districts or counties side by side on the question's measures, each figure with its margin of
    error, and whether the first place differs from each other one beyond the margins. The state or counties the
    question asks for come after them. A measure published only for counties cannot tell districts apart, so it is
    named and left out rather than shown as the districts' own."""
    names = {g: _shown(con, g) for g in [*subjects, *context]}
    districts = [g for g in subjects if g[0] in CHAMBERS]
    actions = [compare_action(con, districts)] if len(districts) >= 2 else []
    q = question.casefold()
    if asks_profile(q) or asks_programs(q) or asks_fixed_income(q):
        return _reply(ONE_AT_A_TIME, actions=actions or [compare_action(con, [])], compared=subjects)
    ids = indicators(con, question)
    if not ids and earlier:
        ids = list(earlier[-1].indicators)
    if not ids:
        return _reply(NO_PAIR_MEASURE, refusal="not_available", actions=actions, compared=subjects)
    covered = dict(
        con.execute("SELECT indicator_id, list(geo_type) FROM indicator_coverage GROUP BY 1").fetchall()
    )
    usable = [i for i in ids if all(g[0] in covered.get(i, []) for g in subjects)]
    county_only = [i for i in ids if i not in usable]
    labels = dict(con.execute("SELECT indicator_id, label FROM indicator").fetchall())
    units = dict(con.execute("SELECT indicator_id, unit FROM indicator").fetchall())
    shown = _joined([names[g] for g in subjects])
    notes = []
    if county_only:
        what = _joined([in_sentence(short_label(i, labels.get(i, i))) for i in county_only])
        notes.append(
            f"The figures for {what} are published only for counties and the state, so they cannot tell "
            f"{shown} apart, and they are left out."
        )
    session = Session(con)
    result = {"facts": [], "missing": []}
    if usable:
        result = json.loads(
            session.run(
                "get_facts", {"indicator_ids": usable, "geos": [":".join(g) for g in [*subjects, *context]]}
            )
        )
    if "error" in result:
        raise ValueError("Could not read the compared places' facts")
    if not result["facts"]:
        text = " ".join(
            notes
            or [
                f"I do not have published figures for that question in {shown}. I will not fill the gap with a guess."
            ]
        )
        return _reply(text, refusal="not_available", actions=actions, compared=subjects)
    facts = {(f["indicator_id"], tuple(f["geo"].split(":"))): f for f in result["facts"]}
    missing = {(m["indicator_id"], tuple(m["geo"].split(":"))): m["reason"] for m in result["missing"]}
    lines, shaky, modeled, shown_facts = [], [], {}, []
    for ind in usable:
        if not any((ind, g) in facts for g in subjects):
            continue
        unit = units.get(ind, "count")
        label = short_label(ind, labels.get(ind, ind))
        parts = []
        for g in [*subjects, *context]:
            fact = facts.get((ind, g))
            if fact is None:
                if g in subjects:
                    parts.append(
                        f"{names[g]}, not available ({missing.get((ind, g), 'no published figure')})"
                    )
                continue
            shown_facts.append(fact)
            f = session.facts[fact["ref"]]
            parts.append(f"{names[g]}, {_pair_value(f, unit)}")
            if fact["reliable"] is False:
                shaky.append(names[g])
            if session.tiers.get(fact["ref"]) == "M":
                modeled[fact["ref"]] = label
        line = f"- {label}: {'; '.join(parts)}."
        first = facts.get((ind, subjects[0]))
        for other in subjects[1:]:
            theirs = facts.get((ind, other))
            if first and theirs:
                way = actual_direction(session.facts[first["ref"]], session.facts[theirs["ref"]])
                if said := _difference(names[subjects[0]], names[other], way):
                    line += f" {said}"
        lines.append(line)
    against = f", with {_joined([names[g] for g in context])} for context" if context else ""
    lead = [f"{shown}, side by side{against}:\n" + "\n".join(lines)]
    caveats = []
    if modeled:
        caveats.append(modeled_caveat(modeled.values()))
    if shaky:
        caveats.append(
            f"The margin of error is large for the {'estimate' if len(set(shaky)) == 1 else 'estimates'} for "
            f"{_joined(list(dict.fromkeys(shaky)))}, so use {'it' if len(set(shaky)) == 1 else 'them'} with care."
        )
    caveats.append(
        "A difference counts only when the margins of error do not overlap. Within the margins, the data cannot say "
        "which place is higher."
    )
    refs = [session.facts[f["ref"]] for f in shown_facts]
    return {
        "text": "\n\n".join(lead + notes + caveats),
        "refusal": "none",
        "facts": _cards(con, shown_facts),
        "programs": [],
        "fact_refs": refs,
        "lead": lead + notes,
        "caveats": caveats,
        "modeled": modeled,
        "district": subjects[0],
        "compared": subjects,
        "actions": actions,
    }


# Ranking questions: whether the lowest come first, and words that ask for a share or a count.
LOWEST = re.compile(r"\b(?:lowest|fewest|smallest|least)\b", re.IGNORECASE)
ASKS_SHARE = re.compile(r"\b(?:share|percent|percentage|rate|proportion)\b|%", re.IGNORECASE)
ASKS_COUNT = re.compile(r"\b(?:most|fewest|number|how many|count|total)\b", re.IGNORECASE)
RANK_WORDS = re.compile(
    r"\b(?:which|what|districts?|house|senate|highest|lowest|most|fewest|largest|smallest|top|rank\w*)\b",
    re.IGNORECASE,
)
# The most districts a ranking answer lists by name, and how many groups it reads out.
RANK_SHOWN = 10
RANK_GROUPS = 3


def _rank_measure(con, question: str) -> str | None:
    """The measure a ranking question asks about: a rate or median unless it asks for a count."""
    units = dict(con.execute("SELECT indicator_id, unit FROM indicator").fetchall())
    ids = [i for i in indicators(con, RANK_WORDS.sub(" ", question)) if i in units]
    if not ids:
        return None
    additive = {r[0] for r in con.execute("SELECT indicator_id FROM indicator WHERE additive").fetchall()}
    shares = [i for i in ids if units.get(i) == "percent"]
    rates = [i for i in ids if i not in additive]
    counts = [i for i in ids if i in additive]
    if ASKS_SHARE.search(question):
        # A share is never answered with a count, which would rank districts by their size.
        return shares[0] if shares else None
    if ASKS_COUNT.search(question) and not ASKS_SHARE.search(question) and counts:
        return counts[0]
    return (rates or ids)[0]


def rankings_action(indicator: str, chamber: str, lowest: bool = False) -> dict | None:
    """A link to the Rankings page set to this measure, chamber and order, when the page has the measure."""
    from klear.web.shade import MEASURES  # imported here: shade imports the display module that this one does

    if indicator not in MEASURES:
        return None
    word = "House" if chamber == "house" else "Senate"
    order = "&order=low" if lowest else ""
    return {
        "text": f"See every {word} district ranked and on the map",
        "url": f"/rankings?measure={indicator}&chamber={chamber}{order}",
    }


def rank_answer(con, question: str, selected) -> dict:
    """Every district of a chamber ranked on one measure from the store, highest or lowest first, in groups whose
    ranges overlap, so the data cannot order the districts inside a group. Read with no model call."""
    words = set(WORDS.findall(question.casefold()))
    chamber = (
        "senate"
        if "senate" in words and "house" not in words
        else "house"
        if "house" in words
        else selected[0]
        if selected and selected[0] in CHAMBERS
        else "house"
    )
    indicator = _rank_measure(con, question)
    if indicator is None:
        return {
            "text": (
                "I could not match that ranking to a published district measure. Try a measure such as poverty, "
                "kūpuna, household income, rent burden or health insurance, or open Rankings to color every "
                "district by one."
            ),
            "refusal": "not_available",
            "facts": [],
            "programs": [],
            "actions": [{"text": "Open Rankings", "url": "/rankings"}],
        }
    counties = [g for g in places_in(con, question, chamber)[0] if g[0] == "county"]
    county = counties[0] if counties else None
    lowest = bool(LOWEST.search(question))
    session = Session(con)
    args = {"indicator": indicator, "chamber": chamber} | ({"county": ":".join(county)} if county else {})
    try:
        ranked = session._rank_districts(args, lowest=lowest)
    except ToolError:
        return _reply(f"KLEAR has no {chamber.title()} districts to rank there.", refusal="not_available")
    label = short_label(indicator, ranked["label"])
    unit = ranked["unit"]
    word = chamber.title()
    where = f" in {_shown(con, county)}" if county else ""
    if not ranked["tiers"]:
        return _reply(
            f"No {word} district{where} has a published figure for {in_sentence(label)} with a margin of error, so "
            "KLEAR cannot rank them. I will not fill the gap with a guess.",
            refusal="not_available",
        )
    shown_geos, lines, listed = [], [], 0
    for tier in ranked["tiers"][:RANK_GROUPS]:
        if listed >= RANK_SHOWN:
            break
        names = []
        for d in tier["districts"][: RANK_SHOWN - listed]:
            f = session.facts[d["ref"]]
            names.append(f"{d['geo_name']} ({_pair_value(f, unit)})")
            shown_geos.append(d["geo"])
            listed += 1
        rest = len(tier["districts"]) - len(names)
        listed_names = f"{', '.join(names)} and {rest} more" if rest > 0 else _joined(names)
        lead = "First group" if tier["tier"] == 1 else "Next group"
        lines.append(f"- {lead}: {listed_names}.")
    total = sum(len(t["districts"]) for t in ranked["tiers"])
    additive = con.execute("SELECT additive FROM indicator WHERE indicator_id = ?", [indicator]).fetchone()[0]
    order = ("fewest" if lowest else "most") if additive else ("lowest" if lowest else "highest")
    head = f"{word} districts{where} with the {order} {in_sentence(label)}, from KLEAR's data store:"
    how = (
        f"Districts in one group have overlapping margins of error, so the data cannot say which of them is "
        f"{'lower' if lowest else 'higher'}. Every district in a later group is {'higher' if lowest else 'lower'} "
        f"than the first district of each earlier group. {total} of the {word}'s districts{where} "
        f"{'has' if total == 1 else 'have'} a figure."
    )
    caveats = [how]
    if ranked["unreliable"]:
        caveats.append(
            f"{ranked['unreliable']} of these estimates have a large margin of error, so use them with care."
        )
    if note := ranked.get("note"):
        caveats.append(note)
    actions = [a for a in [rankings_action(indicator, chamber, lowest)] if a]
    # The listed districts' figures in full, in ranked order, for the figure cards and the chart.
    got = json.loads(session.run("get_facts", {"indicator_ids": [indicator], "geos": shown_geos}))
    if "error" in got:
        raise ValueError("Could not read the ranked districts' facts")
    return {
        "text": "\n\n".join([head + "\n" + "\n".join(lines), *caveats]),
        "refusal": "none",
        "facts": _cards(con, got["facts"]),
        "programs": [],
        "fact_refs": [session.facts[f["ref"]] for f in got["facts"]],
        "lead": [head + "\n" + "\n".join(lines)],
        "caveats": caveats,
        "district": None,
        "actions": actions,
    }


def ask(con, question, district, geocoder=None, earlier=(), pair=()):
    """Free answer for the place a question names, the selected district (None when none is chosen), or the
    district of a street address in the question; geocoder finds that address's Census blocks, or is None when
    address lookup is off. earlier holds the reader's last questions in this tab, and pair the two districts the
    Compare page sets side by side, if any. A comparison keeps every place it names: it is never answered for one
    of them, or for another place."""
    if PERSONAL.search(question):
        return {
            "text": "I can describe districts from public data, but I cannot find or judge any one person.",
            "refusal": "decline_individual",
            "facts": [],
            "programs": [],
        }
    if PARTISAN.search(question):
        return {
            "text": "I can share neutral data about districts, but I cannot help with politics or campaigns.",
            "refusal": "decline_partisan",
            "facts": [],
            "programs": [],
        }
    if (member := member_answer(con, question, district, earlier)) is not None:
        return member
    if address := address_in(question):
        return address_answer(con, question, address, geocoder)
    if RANKING.search(question):
        return rank_answer(con, question, district)
    subjects, context, reply = comparison_places(con, question, district, pair, earlier)
    if reply is not None:
        return reply
    if subjects:
        return compare_answer(con, question, subjects, context, earlier)
    subject, where, reply = place_of(con, question, district, earlier)
    if reply is not None:
        return reply
    result = _answer(con, question, subject, earlier)
    if where:
        result["text"] = f"{where}\n\n{result['text']}"
        result["where"] = where
    return result | {"district": subject}


def _answer(con, question, district, earlier=()):
    """Free answer for one place: a House or Senate district, a county or the state."""
    name = con.execute("SELECT name FROM geography WHERE geo_type=? AND geo_id=?", list(district)).fetchone()[
        0
    ]
    shown = geo_display(*district, name)
    q = question.casefold()
    if asks_fixed_income(q):
        return fixed_income(con, district, shown)
    if asks_programs(q):
        if district[0] not in CHAMBERS:
            return _reply(PROGRAMS_NEED_DISTRICT)
        return program_answer(con, district, shown)
    ids = indicators(con, question)
    if not ids and earlier:
        ids = list(earlier[-1].indicators)
    if tuple(ids) in GROUPS:
        return group_answer(con, question, district, shown, tuple(ids))
    if not ids:
        return {
            "text": "I could not match that question to a published district measure. Try asking about population, kūpuna, income, poverty, housing, work, or health.",
            "refusal": "not_available",
            "facts": [],
            "programs": [],
        }
    statewide = district == STATE
    geos = [":".join(district)]
    if not statewide:
        geos += [":".join(p) for p in _places(con, question, district)]
    session = Session(con)
    if asks_profile(q):
        for group, n in PROFILE_LARGEST.values():
            ids += [i for i in session.largest(group, district)[:n]]
    here, county_ids = (ids, []) if statewide else _split(con, ids, district[0])
    result = {"facts": []}
    if here:
        result = json.loads(session.run("get_facts", {"indicator_ids": here, "geos": geos}))
    # county-level figures come from the district's main county, labeled as that county's
    if county_ids and "error" not in result and (county := main_county(con, district)):
        theirs = [":".join(county), *(g for g in geos[1:] if g != ":".join(county))]
        more = json.loads(session.run("get_facts", {"indicator_ids": county_ids, "geos": theirs}))
        if "error" in more:
            raise ValueError("Could not read county facts")
        result["facts"] += more["facts"]
    if "error" in result:
        raise ValueError("Could not read district facts")
    if statewide:
        shown = "Hawaiʻi"
    found = result["facts"][: max(8, len(ids) * len(geos))]
    facts = _cards(con, found)
    if not facts:
        return {
            "text": f"I do not have a published figure for that question in {shown}. I will not fill the gap with a guess.",
            "refusal": "not_available",
            "facts": [],
            "programs": [],
        }
    modeled = {f["ref"] for f in found if session.tiers.get(f["ref"]) == "M"}
    county_level = {f["ref"] for f in found if session.tiers.get(f["ref"]) == "C"}
    text = "\n\n".join(
        f"{c['label']} in {c['geography']}: {c['value']}"
        + (", a modeled estimate" if f["ref"] in modeled else "")
        + (", a county-level figure not published for districts" if f["ref"] in county_level else "")
        + f". Source: {c['source']}, {c['year']}."
        for c, f in zip(facts, found, strict=True)
    )
    if kinds := _kinds(found):
        text += f"\n\n{kinds} figures are shown side by side. Consider their margins of error before drawing a conclusion."
    refs = [session.facts[f["ref"]] for f in found]
    notes = []
    labels = dict(con.execute("SELECT indicator_id, label FROM indicator").fetchall())
    missed = unpublished_years(
        question, [r.vintage for r in refs], [labels.get(r.indicator_id, "") for r in refs], []
    )
    if missed:
        years = " and ".join(str(y) for y in missed)
        vintages = " and ".join(dict.fromkeys(r.vintage for r in refs))
        notes.append(
            f"Figures for {years} are not published for {shown} yet. These are the latest available, for {vintages}."
        )
    modeled_names = {f["ref"]: c["label"] for c, f in zip(facts, found, strict=True) if f["ref"] in modeled}
    caveats = [modeled_caveat(modeled_names.values())] if modeled_names else []
    programs = program_rows(tied_programs(dict.fromkeys(r.indicator_id for r in refs), []))
    if programs:
        notes.append(
            f"Related programs, with official links below: {_joined([p['name'] for p in programs])}."
        )
    return {
        "text": "\n\n".join([*notes, text, *caveats]),
        "refusal": "not_available" if missed else "none",
        "facts": facts,
        "programs": programs,
        "fact_refs": refs,
        "notes": notes,
        "caveats": caveats,
        "modeled": modeled_names,
    }
