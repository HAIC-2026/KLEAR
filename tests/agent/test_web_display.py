"""Website answers show a plain lead, number tiles, comparison charts and one source list."""

import json
from types import SimpleNamespace

import pandas as pd

from klear.agent.config import PROFILE_BESIDE
from klear.agent.tools import Session
from klear.config import CURRENT_LAYER
from klear.etl import bls, dotax
from klear.eval.contract import Answer, FactRef
from klear.labels import plain_labels
from klear.store.db import add_facts, add_indicators, add_source
from klear.web.display import CAUTION, METHODS_PAGE, build, figure_links, profile, profile_csv
from tests.fixtures.small_store import _fact, add_county_context


def fact(ref, geo_id, value, moe, low=None, high=None):
    return FactRef(ref, "pop_65_plus", "house", geo_id, "acs", value, low, high, moe, "2020-2024")


def add_rate(con):
    """A percent indicator, so charts have a rate to draw."""
    add_indicators(
        con,
        pd.DataFrame(
            [("pct_65_plus", "Percent 65 years and over", "percent", "persons", None, False)],
            columns=["indicator_id", "label", "unit", "universe", "acs_var", "additive"],
        ),
    )


def rate(ref, geo_type, geo_id, value, moe, low=None, high=None):
    return FactRef(ref, "pct_65_plus", geo_type, geo_id, "acs", value, low, high, moe, "2020-2024")


def answer(facts, blocks, caveats=()):
    return Answer("1", ("house", "022"), "none", blocks, facts, [], list(caveats))


def test_single_place_gets_plain_lead_and_tiles(small_con):
    out = build(
        small_con, answer([fact("f1", "022", 1000.0, 100.0)], ["{geo:house:022} has {fact:f1} kūpuna."])
    )
    assert out["lead"] == ["House District 22 has 1,000 kūpuna."]
    assert out["tiles"] == [
        {
            "value": "1,000",
            "label": "Kūpuna (65 and over)",
            "spread": "± 100",
            "geography": "",
            "meaning": {
                "plain": "People age 65 or older.",
                "official": "Population 65 years and over",
                "universe": "persons",
            },
            "share": "",
            "caution": "",
            "method": None,
        }
    ]
    assert out["charts"] == []
    assert out["sources"] == [
        {"name": "American Community Survey 5-year", "url": "https://example.org", "year": "2020-2024"}
    ]


def test_two_places_become_a_chart_with_a_margin_tag(small_con):
    add_rate(small_con)
    facts = [rate("f2", "house", "023", 10.0, 8.0), rate("f1", "house", "022", 30.0, 4.0)]
    out = build(small_con, answer(facts, ["{geo:house:022} has more than {geo:house:023}."]))
    (chart,) = out["charts"]
    assert [b["geography"] for b in chart["bars"]] == ["House District 22", "House District 23"]
    assert chart["tag"] == "Higher than House District 23" and chart["lower"] is True
    assert chart["bars"][1]["low"] == 2.0 and chart["bars"][1]["high"] == 18.0
    assert chart["note"] == "Bars show the estimate. Lines show the 90% margin of error."
    assert out["tiles"] == []


def test_overlapping_margins_tag_within_margin(small_con):
    add_rate(small_con)
    facts = [rate("f1", "house", "022", 30.0, 4.0), rate("f2", "house", "023", 28.0, 3.0)]
    (chart,) = build(small_con, answer(facts, ["Close."]))["charts"]
    assert chart["tag"] == "Within the margin of error"


def test_counts_stay_tiles_and_are_never_charted_against_the_county_or_state(small_con):
    county = FactRef("f2", "pop_65_plus", "county", "15003", "acs", 5000.0, None, None, 50.0, "2020-2024")
    state = FactRef("f3", "pop_65_plus", "state", "15", "acs", 50000.0, None, None, 500.0, "2020-2024")
    others = fact("f4", "023", 100.0, 80.0)
    out = build(small_con, answer([fact("f1", "022", 1000.0, 100.0), county, state, others], ["x"]))
    assert out["charts"] == []
    assert [t["geography"] for t in out["tiles"]] == ["", "Honolulu County", "Hawaiʻi", "House District 23"]
    assert out["tiles"][0]["share"] == "2.0% of the Hawaiʻi total"
    assert [t["share"] for t in out["tiles"][1:]] == ["", "", ""]


def test_unreliable_estimates_carry_a_caution_on_tiles_bars_and_chart_sentences(small_con):
    add_rate(small_con)
    shaky = rate("f1", "house", "022", 4.0, 3.0)
    out = build(small_con, answer([shaky, rate("f2", "state", "15", 9.8, 0.4)], ["x"]))
    (chart,) = out["charts"]
    assert [b["caution"] for b in chart["bars"]] == [CAUTION, ""]
    assert chart["summary"].endswith(
        "Use the estimate for House District 22 with caution: the margin of error is large."
    )
    # A zero with a margin of error is unreliable too; a figure without a margin cannot be judged.
    zero, plain = fact("f3", "023", 0.0, 12.0), fact("f4", "022", 7.0, None)
    tiles = build(small_con, answer([zero, plain, fact("f5", "022", 1000.0, 100.0)], ["x"]))["tiles"]
    assert [t["caution"] for t in tiles] == [CAUTION, "", ""]


def test_apportioned_and_modeled_figures_say_how_they_were_estimated(small_con):
    add_rate(small_con)
    rows = [
        _fact("house", "022", "pct_65_plus", 30.0, None, tier="M", moe_status="range", value_low=20.0,
              value_high=40.0),
        _fact("state", "15", "pct_65_plus", 20.0, 1.0),
        _fact("house", "023", "pop_65_plus", 100.0, None, tier="A", moe_status="range", value_low=80.0,
              value_high=120.0),
    ]  # fmt: skip
    small_con.execute("DELETE FROM fact WHERE indicator_id = 'pop_65_plus' AND geo_id = '023'")
    add_facts(small_con, pd.DataFrame(rows))
    modeled = rate("f1", "house", "022", 30.0, None, 20.0, 40.0)
    out = build(
        small_con,
        answer(
            [modeled, rate("f2", "state", "15", 20.0, 1.0), fact("f3", "023", 100.0, None, 80.0, 120.0)],
            ["x"],
        ),
    )
    (chart,) = out["charts"]
    assert chart["method"] == {"text": "a modeled estimate, not a direct count", "url": f"{METHODS_PAGE}#acs"}
    assert chart["note"] == "Bars show the estimate. Lines show the 90% margin of error or range."
    (tile,) = out["tiles"]
    assert tile["method"] == {
        "text": "apportioned to the district from ZIP code or census tract data",
        "url": f"{METHODS_PAGE}#acs",
    }


def test_range_facts_read_as_about_and_caveats_fill_markers(small_con):
    f = fact("f1", "022", 1000.0, None, 900.0, 1100.0)
    out = build(small_con, answer([f], ["{fact:f1} people."], ["{label:f1} is apportioned."]))
    assert out["lead"] == ["about 1,000 people."]
    assert out["tiles"][0]["spread"] == "range 900 to 1,100"
    assert out["caveats"] == ["Kūpuna (65 and over) is apportioned."]


def test_tiles_name_other_places(small_con):
    out = build(small_con, answer([fact("f1", "023", 100.0, 80.0)], ["x"]))
    assert out["tiles"][0]["geography"] == "House District 23"


def test_lead_drops_inline_vintages(small_con):
    f = fact("f1", "022", 1000.0, 100.0)
    out = build(small_con, answer([f], ["* {label:f1}: {fact:f1} ({vintage:f1})"]))
    assert out["lead"] == ["* Kūpuna (65 and over): 1,000"]


def test_district_total_against_the_state_reads_as_a_share(small_con):
    state = FactRef("f2", "pop_65_plus", "state", "15", "acs", 50000.0, None, None, 500.0, "2020-2024")
    out = build(small_con, answer([fact("f1", "022", 1000.0, 100.0), state], ["Kūpuna here."]))
    assert out["charts"] == [] and out["tiles"][0]["share"] == "2.0% of the Hawaiʻi total"


def test_layers_downloaded_by_the_build_read_as_a_current_list(small_con):
    add_source(
        small_con,
        "lihtc",
        "Low-Income Housing Tax Credit projects",
        "HUD",
        "https://example.org",
        CURRENT_LAYER,
        "pd",
    )
    f = FactRef("f1", "sites_lihtc", "house", "022", "lihtc", 3.0, None, None, None, CURRENT_LAYER)
    assert build(small_con, answer([f], ["x"]))["sources"][0]["year"] == "Current list"
    small_con.execute(
        "INSERT INTO build_info VALUES ('2026-10-30 19:39:09 HST (2026-10-31T05:39:09Z UTC)', 'x', 0, 'ok', '[]', '{}')"
    )
    assert build(small_con, answer([f], ["x"]))["sources"][0]["year"] == "Current list as of October 2026"


def _profile(con):
    session = Session(con)
    return profile(con, json.loads(session.run("district_profile", {"district": "house:022"})), session)


def _profile_rows(con):
    return {r["id"]: r for s in _profile(con)["sections"] for r in s["rows"]}


def test_profile_names_the_reason_a_place_has_no_figure(small_con):
    row = _profile_rows(small_con)["sites_lihtc"]
    assert [a["lead"] + " " + a["rest"] for a in row["against"]] == [
        "No figure for Honolulu County: not published for county geographies",
        "No figure for Hawaiʻi: not published for state geographies",
    ]


def test_profile_notes_a_state_value_derived_from_counties(small_con):
    add_source(
        small_con,
        "cdc_places_2025_county",
        "PLACES county data",
        "CDC",
        "https://example.org/cdc",
        "2025",
        "pd",
    )
    small_con.execute(
        "INSERT INTO indicator VALUES "
        "('places_food_insecurity', 'Food insecurity', 'percent', NULL, NULL, FALSE, NULL)"
    )
    small_con.execute(
        "INSERT INTO fact VALUES "
        "('house', '022', 'places_food_insecurity', 20.0, NULL, 'ok', 'range', 'M', 'acs', NULL, 18.0, 22.0), "
        "('county', '15003', 'places_food_insecurity', 16.9, NULL, 'ok', 'range', 'M', 'cdc_places_2025_county', NULL, 14.4, 19.4), "
        "('state', '15', 'places_food_insecurity', 17.4, NULL, 'ok', 'range', 'M', 'cdc_places_2025_county', NULL, 15.0, 20.1)"
    )  # fmt: skip
    row = _profile_rows(small_con)["places_food_insecurity"]
    assert [a["rest"] for a in row["against"]] == [
        "the rate in Honolulu County: about 16.9%",
        "the rate in Hawaiʻi: about 17.4% (average of the CDC county estimates)",
    ]
    # the county data the comparisons come from is cited after the section's own sources
    needs = next(s for s in _profile(small_con)["sections"] if s["title"] == "Modeled needs")
    assert [c["name"] for c in needs["compared"]] == ["PLACES county data"]
    assert all(set(r) >= {"against", "note"} and "compared" not in r for r in needs["rows"])


def test_charts_say_what_they_show_in_a_sentence_and_keep_table_figures(small_con):
    add_rate(small_con)
    facts = [rate("f1", "house", "022", 30.0, 4.0), rate("f2", "house", "023", 10.0, 3.0)]
    (chart,) = build(small_con, answer(facts, ["x"]))["charts"]
    assert chart["summary"] == (
        "House District 22, at 30.0% ± 4.0 points, is higher than House District 23 (10.0% ± 3.0 points)."
    )
    assert [(b["number"], b["spread"]) for b in chart["bars"]] == [
        ("30.0%", "± 4.0 points"),
        ("10.0%", "± 3.0 points"),
    ]


def test_chart_sentences_name_ranges_and_overlapping_margins(small_con):
    add_rate(small_con)
    state = rate("f3", "state", "15", 20.0, None)
    county = rate("f4", "county", "15003", 22.0, 0.5)
    ranged = rate("f1", "house", "022", 30.0, None, 25.0, 35.0)
    (chart,) = build(small_con, answer([ranged, county, state], ["x"]))["charts"]
    assert chart["summary"] == (
        "House District 22, at about 30.0% (range 25.0% to 35.0%), is higher than Honolulu County (22.0% ± 0.5 points)"
        " and is higher than Hawaiʻi (20.0%)."
    )
    assert chart["note"] == "Bars show the estimate. Lines show the 90% margin of error or range."
    close = [rate("f1", "house", "022", 30.0, 4.0), rate("f2", "house", "023", 28.0, 3.0)]
    (chart,) = build(small_con, answer(close, ["x"]))["charts"]
    assert chart["summary"].endswith(
        "is within the margin of error of House District 23 (28.0% ± 3.0 points)."
    )


def test_who_may_qualify_section_states_its_caveat_once_and_shows_figures_beside(real_con):
    session = Session(real_con)
    raw = json.loads(session.run("district_profile", {"district": "house:022"}))
    tool_rows = {r["fact"]["indicator_id"]: r for s in raw["sections"] for r in s["rows"]}
    assert tool_rows["hh_poverty_no_snap"]["beside"]["fact"]["indicator_id"] == "hh_snap"
    assert tool_rows["hh_poverty_no_snap"]["fact"]["note"].startswith("Meets the income test only.")
    out = profile(real_con, raw, session)
    sec = next(s for s in out["sections"] if s["title"] == "Who may qualify")
    assert sec["caveat"].startswith("Meets the income test only.") and all(
        r["caveat"] == "" for r in sec["rows"]
    )
    rows = {r["id"]: r for r in sec["rows"]}
    assert set(rows) >= set(PROFILE_BESIDE) and all(
        r["label"] == plain_labels()[i].short for i, r in rows.items()
    )
    eitc = rows["working_below_poverty"]["beside"]
    assert (
        eitc["value"].startswith("about ")
        and eitc["spread"].startswith("range ")
        and "tax returns" in eitc["text"]
    )
    assert eitc["source"]["year"] != rows["working_below_poverty"]["source"]["year"] and eitc["note"] > 0
    assert (
        rows["pop_under_150pct_poverty"]["beside"]["value"] == ""
        and "130" in rows["pop_under_150pct_poverty"]["beside"]["text"]
    )
    names = [x["name"] for x in sec["sources"]]
    assert any("Statistics of Income" in n for n in names) and len(names) == len(set(names))
    text = json.dumps(out, ensure_ascii=False)
    assert "·" not in text and "—" not in text
    other = next(s for s in out["sections"] if s["title"] == "People")
    assert other["caveat"] == ""


def test_profile_download_keeps_a_place_with_no_figure_and_says_why(small_con):
    session = Session(small_con)
    raw = json.loads(session.run("district_profile", {"district": "house:022"}))
    lines = profile_csv(small_con, raw, session).splitlines()
    rows = [ln for ln in lines if "Low-income housing tax credit projects" in ln]
    assert len(rows) == 3 and rows[1].startswith(
        "Help sites,Low-income housing tax credit projects,Honolulu County,,"
    )
    assert rows[1].endswith("not published for county geographies")
    assert rows[2].endswith("not published for state geographies")


def test_figures_that_match_dbedt_say_so_in_their_source_and_link_its_profile(small_con):
    add_county_context(small_con)
    district = {"text": "House District 22", "url": "https://example.org/dbedt/house.xlsx"}
    (acs,) = build(small_con, answer([fact("f1", "022", 1000.0, 100.0)], ["x"]))["sources"]
    assert acs["match"] == {"text": "Matches DBEDT's published district profile", "places": [district]}
    county = FactRef("f3", "pop_65_plus", "county", "15003", "acs", 5000.0, None, None, 50.0, "2020-2024")
    (acs,) = build(small_con, answer([fact("f1", "022", 1000.0, 100.0), county], ["x"]))["sources"]
    assert acs["match"]["text"] == "Matches DBEDT's published district and county profiles"
    assert [p["text"] for p in acs["match"]["places"]] == ["House District 22", "Honolulu County"]
    # House 23 was not compared, so it is never named
    (acs,) = build(
        small_con, answer([fact("f1", "022", 1000.0, 100.0), fact("f2", "023", 100.0, 80.0)], ["x"])
    )["sources"]
    assert acs["match"]["places"] == [district]
    (acs,) = build(small_con, answer([fact("f2", "023", 100.0, 80.0)], ["x"]))["sources"]
    assert "match" not in acs


def test_profile_match_names_only_places_where_every_figure_matched(small_con):
    add_county_context(small_con)
    facts = {
        "f1": fact("f1", "022", 1000.0, 100.0),
        "f2": FactRef("f2", "pop_65_plus", "county", "15003", "acs", 5000.0, None, None, 50.0, "2020-2024"),
        "f3": FactRef("f3", "pop_65_plus", "state", "15", "acs", 9000.0, None, None, None, "2020-2024"),
        "f4": FactRef("f4", "pop_under_18", "house", "022", "acs", 10.0, None, None, 1.0, "2020-2024"),
    }

    def row(ref, against=()):
        shown = {"ref": ref, "indicator_id": facts[ref].indicator_id, "label": "x", "vintage": "2020-2024",
                 "source": "ACS", "reliable": None}  # fmt: skip
        return {
            "fact": shown,
            "against": [
                {"ref": r, "geo": f"{facts[r].geo_type}:{facts[r].geo_id}", "geo_name": "x", "share": "1%"}
                for r in against
            ],
        }

    raw = {
        "district": {"geo": "house:022", "name": "House District 22"},
        "counties": [], "places": [], "overlaps": [],
        "sections": [{"title": "People", "rows": [row("f1", ["f2", "f3"])]},
                     {"title": "Mixed", "rows": [row("f1", ["f2", "f3"]), row("f4")]}],
    }  # fmt: skip
    people, mixed = profile(small_con, raw, SimpleNamespace(facts=facts))["sections"]
    match = people["sources"][0]["match"]
    assert match["text"] == "Matches DBEDT's published district and county profiles"
    assert [p["text"] for p in match["places"]] == ["House District 22", "Honolulu County"]
    # a district figure in the same citation that DBEDT did not match leaves only the county
    assert [p["text"] for p in mixed["sources"][0]["match"]["places"]] == ["Honolulu County"]


def test_county_level_figures_link_their_series_and_table_and_say_they_cover_the_county(small_con):
    add_county_context(small_con)
    rate = [
        FactRef(
            "f1", "bls_unemployment_rate", "county", "15003", bls.SOURCE_ID, 3.0, None, None, None, "Sep"
        ),
        FactRef("f2", "bls_unemployment_rate", "state", "15", bls.SOURCE_ID, 3.2, None, None, None, "Sep"),
    ]
    out = build(small_con, answer(rate, ["x"]))
    (chart,) = out["charts"]
    assert chart["label"] == "Unemployment rate (county-level)"
    (source,) = out["sources"]
    assert source["links"] == [
        {
            "text": "BLS data series for Honolulu County",
            "url": "https://data.bls.gov/timeseries/LAUCN150030000000003",
        },
        {
            "text": "BLS data series for Hawaiʻi",
            "url": "https://data.bls.gov/timeseries/LAUST150000000000003",
        },
    ]
    assert "match" not in source
    assert figure_links(dotax.SOURCE_ID, "C", "A-4", "county", "15003", "Honolulu County") == [
        {"text": "Department of Taxation table A\u2060-\u20604", "url": dotax.REPORT_URL}
    ]
    raw = {
        "district": {"geo": "house:022", "name": "House District 22"},
        "counties": [], "places": [], "overlaps": [],
        "sections": [{"title": "County-level figures", "geo": "county:15003", "rows": [
            {"fact": {"ref": "f1", "indicator_id": "bls_unemployment_rate", "label": "x", "vintage": "Sep",
                      "source": "BLS", "reliable": None},
             "against": [{"ref": "f2", "geo": "state:15", "geo_name": "Hawaiʻi", "direction": "lower"}]}]}],
    }  # fmt: skip
    (section,) = profile(small_con, raw, SimpleNamespace(facts={f.ref: f for f in rate}))["sections"]
    assert (
        section["scope"]
        == "These figures cover all of Honolulu County. They are not published for House District 22."
    )
    (row,) = section["rows"]
    assert row["label"] == "Unemployment rate (county-level)" and row["value"] == "3.0%"
    assert row["against"][0]["lead"] == "a little lower than"
    assert row["against"][0]["rest"] == "the rate in Hawaiʻi: 3.2%"


def test_profile_rows_flag_unreliable_estimates_and_sections_share_their_method(real_con):
    session = Session(real_con)
    raw = json.loads(session.run("district_profile", {"district": "house:022"}))
    out = profile(real_con, raw, session)
    rows = [r for s in out["sections"] for r in s["rows"]]
    shaky = {
        r["fact"]["indicator_id"]
        for s in raw["sections"]
        for r in s["rows"]
        if r["fact"]["reliable"] is False
    }
    assert shaky and {r["id"] for r in rows if r["caution"] == CAUTION} == shaky
    modeled = next(s for s in out["sections"] if s["title"] == "Modeled needs")
    assert modeled["method"] == {
        "text": "a modeled estimate, not a direct count",
        "url": f"{METHODS_PAGE}#cdc_places_2025",
    }
    assert all(r["method"] is None for r in modeled["rows"])
    people = next(s for s in out["sections"] if s["title"] == "People")
    assert people["method"] is None and all(r["method"] is None for r in people["rows"])


def test_figure_line_counts_each_figure_the_answer_shows(small_con):
    facts = [fact("f1", "022", 1000.0, 100.0), fact("f2", "023", 900.0, 80.0)]
    facts.append(FactRef("f3", "pct_65_plus", "house", "022", "acs", 20.0, None, None, 1.0, "2020-2024"))
    out = build(small_con, answer(facts, ["{fact:f1} kūpuna."]))
    assert out["verified"] == {
        "figures": 3,
        "sources": 1,
        "text": "All 3 figures in this answer come from the cited source below.",
    }
    one = build(small_con, answer([fact("f1", "022", 1000.0, 100.0)], ["Kūpuna."]))
    assert one["verified"]["text"] == "The 1 figure in this answer comes from the cited source below."


def test_figure_line_skips_missing_values_and_unshown_facts(small_con):
    missing = fact("f0", "022", None, None)
    tiled = [
        FactRef(f"t{i}", f"ind_{i}", "house", "022", "acs", float(i), None, None, None, "2020-2024")
        for i in range(8)
    ]
    out = build(small_con, answer([missing, *tiled], ["{fact:f0} kūpuna."]))
    assert len(out["tiles"]) == 6 and out["verified"]["figures"] == 5
    marked = build(small_con, answer([missing, *tiled], ["{fact:t7} is the last."]))
    assert marked["verified"]["figures"] == 6


def test_no_figure_line_without_figures_or_on_declines(small_con):
    assert "verified" not in build(small_con, answer([], ["No data."]))
    assert "verified" not in build(small_con, answer([fact("f0", "022", None, None)], ["{fact:f0}"]))
    declined = Answer(
        "1", ("house", "022"), "decline_individual", ["x"], [fact("f1", "022", 1.0, 1.0)], [], []
    )
    assert "verified" not in build(small_con, declined)
    partial = Answer("1", ("house", "022"), "not_available", ["x"], [fact("f1", "022", 1.0, 1.0)], [], [])
    assert build(small_con, partial)["verified"]["figures"] == 1
