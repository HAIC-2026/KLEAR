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
from klear.agent.config import FIXED_INCOME, FIXED_INCOME_COMPARED, FIXED_INCOME_PROGRAMS, PROFILE_INDICATORS
from klear.agent.tools import Session
from klear.eval.contract import geo_display
from klear.labels import in_sentence, meaning, short_label
from klear.web.display import _plain, build_month, matched_programs, program_rows, year_text
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
CHAMBERS = ("house", "senate")
# A share of residents at or above this reads as all of them, and an island holding this much of a county's
# residents is answered with the county's figures.
WHOLE = 0.995
ISLAND_COUNTY = 0.99
NEEDS_ASSISTANT = (
    "This question compares every district, which needs the KLEAR assistant. The basic lookup answers for "
    "one district, town, ZIP code or county at a time."
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
    (("food insecur", "food-insecur", "hunger", "hungry"), ("places_food_insecurity", "pct_hh_snap")),
    (("housing insecur", "housing-insecur"), ("places_housing_insecurity", "pct_rent_burden_35_plus")),
    (("shut off", "shut-off", "shutoff", "utility", "utilities"), ("places_utility_shutoff_threat",)),
    (("transportation", "transit", "rides"), ("places_lack_transport",)),
    (("public housing",), ("units_public_housing", "sites_public_housing")),
    (("voucher", "section 8"), ("hud_vouchers", "pct_rent_burden_35_plus")),
    (("snap", "food stamp"), ("pct_hh_snap", "hh_snap", "hh_poverty_no_snap")),
)


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


def indicators(con, question):
    """Indicator ids for a question: every profile figure for a profile question, else up to three, with
    topic keywords first and then words shared with labels."""
    q = question.casefold()
    if asks_profile(q):
        return list(PROFILE_INDICATORS)
    for stems, ids in TOPICS:
        if _has(q, *stems):
            return list(ids)
    if _has(q, *KUPUNA):
        return ["pop_65_plus", "pct_65_plus", "pct_65_plus_poverty"]
    if _has(q, "poverty", "low income"):
        return ["pct_people_poverty", "pct_children_poverty", "pct_65_plus_poverty"]
    if _has(q, "housing", "rent", "cost burden"):
        return ["median_gross_rent", "pct_rent_burden_35_plus", "pct_owner_cost_burden_35_plus"]
    if _has(q, "income", "wage", "earn"):
        return ["median_household_income", "per_capita_income", "pct_people_poverty"]
    if _has(q, "unemploy", "jobs", "work"):
        return ["unemployment_rate", "pct_in_labor_force", "pct_work_from_home"]
    if _has(q, "health", "uninsured", "disabil"):
        return ["pct_uninsured", "pct_disability", "pct_public_coverage"]
    if _has(q, "child", "keiki", "young"):
        return ["pop_under_18", "pct_children_poverty", "pct_hh_with_children"]
    if _has(q, "population", "people", "residents"):
        return ["pop_total", "pop_under_18", "pop_65_plus"]
    terms = set(WORDS.findall(q)) - STOP - POINTING
    if not terms - GENERIC:
        return []
    rows = con.execute("SELECT indicator_id, label FROM indicator ORDER BY indicator_id").fetchall()
    ranked = []
    for key, label in rows:
        shared = terms & set(WORDS.findall(label.casefold()))
        if shared - GENERIC:
            ranked.append((len(shared), key))
    return [key for _, key in sorted(ranked, reverse=True)[:3]]


def program_answer(con, district, shown: str) -> dict:
    """Programs matched to the district's need figures, with the reason for each in words."""
    session = Session(con)
    raw = json.loads(session.run("district_programs", {"district": f"{district[0]}:{district[1]}"}))
    if "error" in raw:
        raise ValueError("Could not match programs to the district")
    laid = matched_programs(con, raw, session)
    names = [p["name"] for p in laid["programs"]]
    if names:
        listed = ", ".join(names[:-1]) + (" and " if len(names) > 1 else "") + names[-1]
        lead = (
            f"Programs to explore for residents of {shown}: {listed}. They are ranked by how far the district's "
            "figures sit above the state's, and each one below gives its reason."
        )
    else:
        lead = (
            f"None of {shown}'s need figures sit clearly above the state's, so no program stands out here. "
            "These programs serve residents statewide."
        )
    return {
        "text": f"{lead}\n\n{laid['note']}",
        "refusal": "none",
        "facts": [],
        "programs": [p | {"match": True} for p in laid["programs"]]
        + [p | {"match": False} for p in laid["others"]],
        "program_sources": laid["sources"],
    }


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
            }
        )
    return out


def _joined(parts: list[str], word: str = "and") -> str:
    return parts[0] if len(parts) == 1 else f"{', '.join(parts[:-1])} {word} {parts[-1]}"


def _comparison(session, fact: dict, others: list[dict], unit: str) -> str:
    """How a district fact relates to each other place, like 'higher than Honolulu County (19.6%)'."""
    ways: dict[str, list[str]] = {}
    for other in others:
        theirs = session.facts[other["ref"]]
        if way := actual_direction(session.facts[fact["ref"]], theirs):
            ways.setdefault(way, []).append(f"{other['geo_name']} ({_plain(theirs, unit)})")
    return ", ".join(f"{DIRECTION_TEXT[w]} {_joined(p)}" for w, p in ways.items())


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


def place_of(con, question: str, selected, earlier=()) -> tuple[tuple[str, str] | None, str, dict | None]:
    """The place to answer for: one the question names, else the selected district, else the whole state for a
    statewide question, else the place of the last answer. Also a line saying where a named town lies, or a reply
    to send instead of an answer: district choices when a named place spans several, or a request to name one."""
    session = Session(con)
    statewide = bool(STATEWIDE.search(question))
    found = None if statewide else session.place_named(question, frozenset(STOP | GENERIC | POINTING))
    if found is None:
        last = next((x.district for x in reversed(earlier) if x.district), None)
        subject = selected or (("state", "15") if statewide else last)
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
        else f"Your question could mean {_joined(list(dict.fromkeys(_match_name(m) for m in matches)), 'or')}."
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


def ask(con, question, district, geocoder=None, earlier=()):
    """Free answer for the place a question names, the selected district (None when none is chosen), or the
    district of a street address in the question; geocoder finds that address's Census blocks, or is None when
    address lookup is off. earlier holds the reader's last questions in this tab."""
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
    if address := address_in(question):
        return address_answer(con, question, address, geocoder)
    if RANKING.search(question):
        return _reply(NEEDS_ASSISTANT)
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
    if any(
        word in q for word in ("program", "benefit", "assistance", "support", "help available")
    ) and not any(_has(q, *stems) for stems, _ in TOPICS):
        if district[0] not in CHAMBERS:
            return _reply(PROGRAMS_NEED_DISTRICT)
        return program_answer(con, district, shown)
    ids = indicators(con, question)
    if not ids and earlier:
        ids = list(earlier[-1].indicators)
    if not ids:
        return {
            "text": "I could not match that question to a published district measure. Try asking about population, kūpuna, income, poverty, housing, work, or health.",
            "refusal": "not_available",
            "facts": [],
            "programs": [],
        }
    statewide = bool(STATEWIDE.search(question)) or district == ("state", "15")
    geos = ["state:15"] if statewide else [f"{district[0]}:{district[1]}"]
    if not statewide and "county" in q and (county := main_county(con, district)):
        geos.append(":".join(county))
    if not statewide and ("compare" in q or "state" in q or "hawaiʻi" in q or "hawaii" in q):
        geos.append("state:15")
    session = Session(con)
    result = json.loads(session.run("get_facts", {"indicator_ids": ids, "geos": geos}))
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
    text = "\n\n".join(
        f"{c['label']} in {c['geography']}: {c['value']}"
        + (", a modeled estimate" if f["ref"] in modeled else "")
        + f". Source: {c['source']}, {c['year']}."
        for c, f in zip(facts, found, strict=True)
    )
    if len(geos) > 1:
        places = "District, county and state" if len(geos) == 3 else "District and " + geos[1].split(":")[0]
        text += f"\n\n{places} figures are shown side by side. Consider their margins of error before drawing a conclusion."
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
    caveats = []
    if modeled:
        names = list(
            dict.fromkeys(c["label"] for c, f in zip(facts, found, strict=True) if f["ref"] in modeled)
        )
        one = len(names) == 1
        caveats.append(
            f"The {'figure' if one else 'figures'} for {_joined([in_sentence(n) for n in names])} "
            f"{'is a modeled estimate' if one else 'are modeled estimates'} from CDC PLACES survey answers, "
            "not a direct count."
        )
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
    }
