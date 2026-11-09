"""Free, deterministic answers from the validated local fact store."""

import json
import re

from klear.agent.config import PROFILE_INDICATORS
from klear.agent.tools import Session
from klear.eval.contract import geo_display
from klear.rules.registry import load_rules
from klear.web.display import build_month, year_text

PERSONAL = re.compile(
    r"\b(?:a specific person|an individual|named resident|home address|phone number|personal information|who lives at|identify a resident|my neighbor|specific resident)\b",
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


def _has(q, *stems):
    """Whether any word in q starts with one of the stems."""
    return any(re.search(r"\b" + re.escape(stem), q) for stem in stems)


def asks_profile(question: str) -> bool:
    """Whether a question asks for the district's standard profile."""
    return _has(question.casefold(), "profile", "socioeconomic", "overview")


def indicators(con, question):
    """Indicator ids for a question: every profile figure for a profile question, else up to three, with
    topic keywords first and then words shared with labels."""
    q = question.casefold()
    if asks_profile(q):
        return list(PROFILE_INDICATORS)
    if _has(q, "kūpuna", "kupuna", "senior", "older adult", "65"):
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
    terms = set(WORDS.findall(q)) - STOP
    if not terms - GENERIC:
        return []
    rows = con.execute("SELECT indicator_id, label FROM indicator ORDER BY indicator_id").fetchall()
    ranked = []
    for key, label in rows:
        shared = terms & set(WORDS.findall(label.casefold()))
        if shared - GENERIC:
            ranked.append((len(shared), key))
    return [key for _, key in sorted(ranked, reverse=True)[:3]]


def ask(con, question, district):
    if PERSONAL.search(question):
        return {
            "text": "I can describe published district data, but I cannot identify or assess an individual person.",
            "refusal": "decline_individual",
            "facts": [],
            "programs": [],
        }
    if PARTISAN.search(question):
        return {
            "text": "I can provide nonpartisan district data, but I cannot help with electoral or partisan strategy.",
            "refusal": "decline_partisan",
            "facts": [],
            "programs": [],
        }
    name = con.execute("SELECT name FROM geography WHERE geo_type=? AND geo_id=?", list(district)).fetchone()[
        0
    ]
    shown = geo_display(*district, name)
    q = question.casefold()
    if any(word in q for word in ("program", "benefit", "assistance", "support", "help available")):
        chosen = [
            r
            for r in load_rules()
            if r.status == "confirmed" and r.rule_id not in {"fpg_2026_hi", "hud_il_fy2026_hi", "hpha_limits"}
        ][:6]
        return {
            "text": f"These published programs may be worth exploring for residents of {shown}. This district-level view cannot establish any person's eligibility; consult each program's official guidance.",
            "refusal": "none",
            "facts": [],
            "programs": [{"name": r.program, "url": r.url, "year": r.effective} for r in chosen],
        }
    ids = indicators(con, question)
    if not ids:
        return {
            "text": "I could not match that question to a published district measure. Try asking about population, kūpuna, income, poverty, housing, work, or health.",
            "refusal": "not_available",
            "facts": [],
            "programs": [],
        }
    geos = [f"{district[0]}:{district[1]}"]
    if "compare" in q or "state" in q or "hawaiʻi" in q or "hawaii" in q:
        geos.append("state:15")
    session = Session(con)
    result = json.loads(session.run("get_facts", {"indicator_ids": ids, "geos": geos}))
    if "error" in result:
        raise ValueError("Could not read district facts")
    facts, month = [], build_month(con)
    for fact in result["facts"]:
        if len(facts) >= max(8, len(ids)):
            break
        row = con.execute(
            "SELECT s.name,s.url,f.moe_status FROM fact f JOIN source s USING(source_id) WHERE f.indicator_id=? AND f.geo_type=? AND f.geo_id=? AND f.source_id=?",
            [fact["indicator_id"], *fact["geo"].split(":"), fact["source_id"]],
        ).fetchone()
        facts.append(
            {
                "label": fact["label"],
                "value": fact["value_text"],
                "year": year_text(fact["vintage"], month),
                "source": row[0],
                "url": row[1],
                "geography": fact["geo_name"],
                "uncertainty": row[2],
            }
        )
    if not facts:
        return {
            "text": f"I do not have a published figure for that question in {shown}. I will not fill the gap with a guess.",
            "refusal": "not_available",
            "facts": [],
            "programs": [],
        }
    text = "\n\n".join(
        f"{f['label']} in {f['geography']}: {f['value']}. Source: {f['source']}, {f['year']}." for f in facts
    )
    if len(geos) > 1:
        text += "\n\nDistrict and state figures are shown side by side. Consider their margins of error before drawing a conclusion."
    shown = [session.facts[f["ref"]] for f in result["facts"][: len(facts)]]
    return {"text": text, "refusal": "none", "facts": facts, "programs": [], "fact_refs": shown}
