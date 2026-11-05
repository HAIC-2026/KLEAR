"""Free, deterministic answers from the validated local fact store."""

import json
import re

from klear.agent.tools import Session
from klear.eval.contract import geo_display
from klear.rules.registry import load_rules

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
}


def indicators(con, question):
    q = question.casefold()
    if "profile" in q or "socioeconomic" in q or "overview" in q:
        return ["pop_total", "median_household_income", "pct_people_poverty", "pct_65_plus"]
    if "kūpuna" in q or "kupuna" in q or "senior" in q or "older adult" in q or "65" in q:
        return ["pop_65_plus", "pct_65_plus", "pct_65_plus_poverty"]
    if "poverty" in q or "low income" in q:
        return ["pct_people_poverty", "pct_children_poverty", "pct_65_plus_poverty"]
    if "housing" in q or "rent" in q or "cost burden" in q:
        return ["median_gross_rent", "pct_rent_burden_35_plus", "pct_owner_cost_burden_35_plus"]
    if "income" in q or "wage" in q or "earn" in q:
        return ["median_household_income", "per_capita_income", "pct_people_poverty"]
    if "unemploy" in q or "jobs" in q or "work" in q:
        return ["unemployment_rate", "pct_in_labor_force", "pct_work_from_home"]
    if "health" in q or "uninsured" in q or "disabil" in q:
        return ["pct_uninsured", "pct_disability", "pct_public_coverage"]
    if "child" in q or "keiki" in q or "young" in q:
        return ["pop_under_18", "pct_children_poverty", "pct_hh_with_children"]
    if "population" in q or "people" in q or "residents" in q:
        return ["pop_total", "pop_under_18", "pop_65_plus"]
    terms = set(WORDS.findall(q)) - STOP
    if not terms:
        return []
    rows = con.execute("SELECT indicator_id, label FROM indicator ORDER BY indicator_id").fetchall()
    ranked = sorted(
        ((len(terms & set(WORDS.findall(label.casefold()))), key) for key, label in rows), reverse=True
    )
    return [key for score, key in ranked if score > 0][:3]


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
    facts = []
    for fact in result["facts"]:
        if len(facts) >= 8:
            break
        row = con.execute(
            "SELECT s.name,s.url,f.moe_status FROM fact f JOIN source s USING(source_id) WHERE f.indicator_id=? AND f.geo_type=? AND f.geo_id=? AND f.source_id=?",
            [fact["indicator_id"], *fact["geo"].split(":"), fact["source_id"]],
        ).fetchone()
        facts.append(
            {
                "label": fact["label"],
                "value": fact["value_text"],
                "year": fact["vintage"],
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
