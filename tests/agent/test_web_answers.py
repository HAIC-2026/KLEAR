"""The free lookup matches questions to district measures by topic and declines what it cannot match."""

import duckdb
import pytest

from klear.agent.answer import main_county
from klear.agent.config import (
    EDUCATION,
    FIXED_INCOME,
    FIXED_INCOME_COMPARED,
    FIXED_INCOME_PROGRAMS,
    INDUSTRIES,
    OCCUPATIONS,
)
from klear.labels import plain_labels
from klear.programs import by_rule
from klear.web.answers import ask, asks_fixed_income, indicators
from klear.web.assistant import basic
from tests.fixtures.small_store import add_county_context

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
            i: next(x for x in lead.split("\n") if x.startswith(f"- {plain_labels()[i].short}: "))
            for i in FIXED_INCOME
        }
        for i in FIXED_INCOME_COMPARED:
            assert " County (" in lines[i] and "Hawaiʻi (" in lines[i], (district, lines[i])
        assert [p["name"] for p in answer["programs"]] == names and all(
            p["url"] and p["phone"] for p in answer["programs"]
        )
        shown = sum(len(c["bars"]) for c in answer["charts"]) + len(answer["tiles"])
        assert answer["verified"]["figures"] == shown, district
        assert any(c.startswith("Covers all households") for c in answer["caveats"]), district
        assert len(answer["charts"]) == len(FIXED_INCOME_COMPARED), district
        assert all(len(c["bars"]) == 3 for c in answer["charts"]), district


@pytest.mark.parametrize(("question", "first"), [
    ("How common is housing insecurity here?", "places_housing_insecurity"),
    ("Are people housing-insecure in this district?", "places_housing_insecurity"),
    ("How many adults face food insecurity?", "places_food_insecurity"),
    ("Do families go hungry here?", "places_food_insecurity"),
    ("How often are utilities threatened with a shut-off?", "places_utility_shutoff_threat"),
    ("Do people lack transportation here?", "places_lack_transport"),
    ("How many public housing units are in this district?", "units_public_housing"),
    ("How many households use housing vouchers?", "hud_vouchers"),
    ("How many households get SNAP?", "pct_hh_snap"),
    ("What is the median rent?", "median_gross_rent"),
])  # fmt: skip
def test_measure_topics_come_before_broad_topics(con, question, first):
    assert indicators(con, question)[0] == first


def _basic(real_con, question, district=("house", "029")):
    return basic(real_con, question, district, "Basic lookup.")


def test_basic_housing_insecurity_is_the_modeled_measure(real_con):
    answer = _basic(real_con, "How common is housing insecurity here?")
    assert answer["refusal"] == "none" and answer["tiles"][0]["label"] == "Adults facing housing insecurity"
    caveat = (
        "The figure for adults facing housing insecurity is a modeled estimate from CDC PLACES survey answers, "
        "not a direct count."
    )
    assert answer["caveats"] == [caveat]


def test_basic_public_housing_gives_units(real_con):
    answer = _basic(real_con, "How many public housing units are in this district?")
    assert [t["label"] for t in answer["tiles"]][:2] == [
        "Public housing units",
        "Public housing developments",
    ]
    assert [p["id"] for p in answer["programs"]] == ["public_housing"]
    assert answer["lead"][-1].startswith("Related programs, with official links below: Public housing")


def test_basic_snap_figures_name_snap(real_con):
    answer = _basic(real_con, "What share of households get SNAP?")
    assert [p["id"] for p in answer["programs"]] == ["snap"]
    assert answer["lead"] == [
        "Basic lookup.",
        f"Related programs, with official links below: {answer['programs'][0]['name']}.",
    ]


def test_basic_unpublished_year_is_not_available(real_con):
    answer = _basic(real_con, "What is the poverty rate for 2025?")
    assert answer["refusal"] == "not_available" and answer["tiles"] and "elsewhere" not in answer
    assert answer["lead"][0] == "Basic lookup." and answer["lead"][1] == (
        "Figures for 2025 are not published for House District 29 yet. These are the latest available, for "
        "2020-2024 ACS 5-year."
    )
    assert _basic(real_con, "What is the poverty rate?")["refusal"] == "none"


@pytest.mark.parametrize(
    "question",
    ["What is the median household income statewide?", "Median household income in Hawaiʻi overall?"],
)
def test_basic_statewide_question_shows_the_state(real_con, question):
    answer = _basic(real_con, question)
    assert {f["geography"] for f in answer["facts"]} == {"Hawaiʻi"}
    assert answer["subject"]["geo"] == "state:15" and answer["subject"]["name"] == "Hawaiʻi"
    assert "district" not in answer


def test_tax_credit_and_unemployment_questions_include_county_level_figures(con):
    assert "dotax_eitc_claims" in indicators(con, "How many people claim the earned income tax credit?")
    assert "bls_unemployment_rate" in indicators(con, "What is unemployment like here?")


def test_county_level_figures_come_from_the_main_county_and_say_so(small_con):
    add_county_context(small_con)
    out = ask(small_con, "Compare unemployment with the state", ("house", "022"))
    county = [f for f in out["facts"] if f["label"] == "Unemployment rate (county-level)"]
    assert [(f["geography"], f["value"]) for f in county] == [
        ("Honolulu County", "3.0%"),
        ("Hawaiʻi", "3.2%"),
    ]
    assert (
        "Unemployment rate (county-level) in Honolulu County: 3.0%, a county-level figure not published for "
        "districts." in out["text"]
    )
    assert not any(
        f["geography"] == "House District 22" and "county-level" in f["label"] for f in out["facts"]
    )


def test_basic_figures_that_match_dbedt_link_its_profile(small_con):
    add_county_context(small_con)
    answer = basic(small_con, "How many kūpuna live here?", ("house", "022"))
    (acs,) = [s for s in answer["sources"] if s["name"] == "American Community Survey 5-year"]
    assert acs["match"] == {
        "text": "Matches DBEDT's published district profile",
        "places": [{"text": "House District 22", "url": "https://example.org/dbedt/house.xlsx"}],
    }


@pytest.mark.parametrize(("question", "ids"), [
    ("What industries do residents of this district work in?", INDUSTRIES),
    ("How many residents work in tourism or hotels?", INDUSTRIES),
    ("Do many people here work in construction?", INDUSTRIES),
    ("What kinds of jobs do residents have?", OCCUPATIONS),
    ("What share of adults here have a college degree?", EDUCATION),
    ("How many adults finished high school?", EDUCATION),
    (
        "How many people are unemployed?",
        ("unemployment_rate", "bls_unemployment_rate", "pct_in_labor_force", "pct_work_from_home"),
    ),
])  # fmt: skip
def test_work_and_education_questions_get_their_measures(con, question, ids):
    assert tuple(indicators(con, question)) == ids


def test_basic_industries_are_listed_largest_first(real_con):
    answer = _basic(real_con, "What industries do residents of this district work in?", ("house", "022"))
    lines = answer["lead"][1].split("\n")
    assert answer["refusal"] == "none" and lines[0].startswith("Industries where working residents of House")
    shares = [float(x.split(": ")[1].split("%")[0]) for x in lines[1:]]
    assert len(shares) == len(INDUSTRIES) and shares == sorted(shares, reverse=True)
    assert (
        answer["caveats"][0]
        == "These shares count residents by the industry they work in, wherever the job is."
    )
    assert answer["tiles"][0]["label"] == "Work in education, health or social services"


def test_basic_industries_compare_with_county_and_state_when_asked(real_con):
    answer = _basic(
        real_con, "Compare the largest industries in House District 22 with the county and the state."
    )
    lines = answer["lead"][1].split("\n")
    assert lines[0].endswith("compared with Honolulu County and Hawaiʻi.")
    assert all(" County (" in x and "Hawaiʻi (" in x for x in lines[1:]), lines


def test_basic_industries_count_every_figure_written_in_the_list(real_con):
    answer = _basic(real_con, "What industries do residents of this district work in?", ("house", "022"))
    written = [x for x in answer["lead"][1].split("\n")[1:] if "%" in x]
    assert len(written) == len(INDUSTRIES)
    assert answer["verified"]["figures"] == len(INDUSTRIES)
    assert answer["verified"]["text"].startswith(f"All {len(INDUSTRIES)} figures in this answer")
    compared = _basic(
        real_con, "Compare the largest industries in House District 22 with the county and the state."
    )
    assert compared["verified"]["figures"] == 3 * len(INDUSTRIES)


def test_basic_college_degree_question_gives_education(real_con):
    answer = _basic(real_con, "What share of adults here have a college degree?")
    assert [t["label"] for t in answer["tiles"]] == [
        "College graduates (adults 25 and over)",
        "High school graduates (adults 25 and over)",
    ]


def test_basic_profile_adds_the_largest_industries(real_con):
    answer = ask(real_con, "Give me a socioeconomic profile of House District 22.", ("house", "022"))
    shown = {f.indicator_id for f in answer["fact_refs"]}
    assert len(shown & set(INDUSTRIES)) == 3 and "pct_bachelors_plus" in shown


def test_basic_county_total_against_the_state_reads_as_the_county_share(real_con):
    answer = basic(real_con, "Compare labor force in House District 22 with Hawaiʻi", ("house", "022"))
    tile = next(t for t in answer["tiles"] if t["label"] == "Labor force (county-level)")
    assert tile["geography"] == "Honolulu County" and tile["share"].endswith("% of the Hawaiʻi total")
    assert [p["geography"] for p in tile["places"]] == ["Hawaiʻi"]


def test_basic_profile_shows_the_county_figures_and_cites_only_what_it_shows(real_con):
    answer = basic(real_con, "Give me a profile of House District 22", ("house", "022"))
    county = [t["label"] for t in answer["tiles"] if t["geography"] == "Honolulu County"]
    assert county and all("(county-level)" in label for label in county)
    assert answer["tiles"][0]["label"] == "Total population"
    assert answer["verified"]["sources"] == len(answer["sources"])
    assert {"Local Area Unemployment Statistics, monthly county and state estimates (county-level)"} <= {
        s["name"] for s in answer["sources"]
    }
    # the modeled figures shown in the table of more figures get the modeled-estimate caveat
    modeled = [m for m in answer["more"] if m["label"].startswith("Adults facing")]
    assert modeled and "modeled estimates" in answer["caveats"][0]


def test_basic_mixed_district_and_county_question_shows_both(real_con):
    answer = basic(real_con, "What is unemployment like here?", ("house", "022"))
    places = {t["geography"] for t in answer["tiles"]}
    assert places == {"", "Honolulu County"}
    assert answer["verified"]["sources"] == len(answer["sources"]) == 2


@pytest.mark.parametrize(
    ("question", "district", "places"),
    [
        (
            "Compare unemployment in House District 22 with the state",
            ("house", "022"),
            "District, county and state",
        ),
        (
            "Compare labor force in House District 22 with Hawaiʻi",
            ("house", "022"),
            "District, county and state",
        ),
        ("Compare poverty with the state", ("county", "15003"), "County and state"),
        ("Compare poverty with the state", ("house", "022"), "District and state"),
    ],
)
def test_side_by_side_line_names_the_places_shown(real_con, question, district, places):
    text = ask(real_con, question, district)["text"]
    assert f"\n\n{places} figures are shown side by side." in text


def test_side_by_side_line_for_county_figures_alone(small_con):
    add_county_context(small_con)
    text = ask(small_con, "Compare unemployment with the state", ("house", "022"))["text"]
    assert "District and state figures" not in text
    assert "County and state figures are shown side by side." in text


def test_basic_industries_keep_the_place_when_compared_with_the_whole_state(real_con):
    compared = basic(real_con, "How do industries in Maui County compare with Hawaiʻi as a whole?", None)
    assert compared["lead"][0].split("\n")[0] == (
        "Industries where working residents of Maui County have jobs, largest share first, compared with Hawaiʻi."
    )
    statewide = _basic(real_con, "What industries do people work in statewide?")
    assert statewide["lead"][1].split("\n")[0] == (
        "Industries where working residents of Hawaiʻi have jobs, largest share first."
    )
