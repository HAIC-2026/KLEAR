"""The free lookup matches questions to district measures by topic and declines what it cannot match."""

import duckdb
import pytest

from klear.agent.answer import main_county
from klear.agent.config import FIXED_INCOME, FIXED_INCOME_COMPARED, FIXED_INCOME_PROGRAMS
from klear.programs import by_rule
from klear.web.answers import asks_fixed_income, indicators
from klear.web.assistant import basic
from klear.web.display import PROFILE_LABELS

LABELS = {
    "poverty_universe": "Population for whom poverty status is determined",
    "unemployment_rate": "Unemployment rate",
    "irs_eitc_dollars": "Earned income tax credit amount",
    "sites_lihtc": "Low-income housing tax credit projects",
    "civilian_veterans": "Civilian veterans",
    "grandparents_responsible": "Grandparents responsible for own grandchildren under 18 years",
    "pop_hispanic": "Hispanic or Latino (of any race)",
    "pct_disability": "Percent with a disability",
}


@pytest.fixture
def con():
    con = duckdb.connect()
    con.execute("CREATE TABLE indicator (indicator_id VARCHAR, label VARCHAR)")
    con.executemany("INSERT INTO indicator VALUES (?, ?)", list(LABELS.items()))
    yield con
    con.close()


@pytest.mark.parametrize(
    "question",
    [
        "What is the capital of France?",
        "Tell me a joke",
        "What time is it?",
        "What is the crime rate in this district?",
        "How many dogs live in this district?",
    ],
)
def test_off_topic_questions_match_nothing(con, question):
    assert indicators(con, question) == []


def test_topic_words_match_at_word_starts(con):
    assert indicators(con, "How many grandparents are raising grandchildren?") == ["grandparents_responsible"]
    assert indicators(con, "What is the current unemployment rate?")[0] == "unemployment_rate"


def test_label_words_still_match(con):
    assert indicators(con, "What is the tax credit amount?")[0] == "irs_eitc_dollars"
    assert indicators(con, "How many veterans live here?") == ["civilian_veterans"]


def test_main_county_holds_most_of_the_district(small_con):
    assert main_county(small_con, ("house", "022")) == ("county", "15003")
    assert main_county(small_con, ("senate", "012")) == ("county", "15003")
    assert main_county(small_con, ("house", "099")) is None


@pytest.mark.parametrize(
    ("question", "fixed"),
    [
        ("How many kūpuna in my district live on fixed incomes?", True),
        ("how many kupuna live on a fixed-income here", True),
        ("How many households get Social Security?", True),
        ("Do retirees here have pensions?", True),
        ("What is the poverty rate for seniors?", True),
        ("How many kūpuna live in this district?", False),
        ("What is the median household income?", False),
        ("How does poverty here compare with the state?", False),
    ],
)
def test_fixed_income_questions_are_recognized(question, fixed):
    assert asks_fixed_income(question) == fixed


def test_fixed_income_answer_for_every_district(real_con):
    owners = by_rule()
    names = [owners[p].name for p in FIXED_INCOME_PROGRAMS]
    districts = real_con.execute(
        "SELECT geo_type, geo_id FROM geography WHERE geo_type IN ('house', 'senate') ORDER BY 1, 2"
    ).fetchall()
    assert len(districts) == 76
    for district in districts:
        answer = basic(real_con, "How many kūpuna in my district live on fixed incomes?", district)
        lead = "\n".join(answer["lead"])
        assert answer["refusal"] == "none" and answer["mode"] == "basic", district
        lines = {
            i: next(x for x in lead.split("\n") if x.startswith(f"- {PROFILE_LABELS[i]}: "))
            for i in FIXED_INCOME
        }
        for i in FIXED_INCOME_COMPARED:
            assert " County (" in lines[i] and "Hawaiʻi (" in lines[i], (district, lines[i])
        assert [p["name"] for p in answer["programs"]] == names and all(
            p["url"] and p["phone"] for p in answer["programs"]
        )
        assert any(c.startswith("Covers all households") for c in answer["caveats"]), district
        assert len(answer["charts"]) == len(FIXED_INCOME_COMPARED), district
        assert all(len(c["bars"]) == 3 for c in answer["charts"]), district
