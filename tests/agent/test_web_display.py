"""Website answers show a plain lead, number tiles, comparison charts and one source list."""

from klear.config import CURRENT_LAYER
from klear.eval.contract import Answer, FactRef
from klear.store.db import add_source
from klear.web.display import build


def fact(ref, geo_id, value, moe, low=None, high=None):
    return FactRef(ref, "pop_65_plus", "house", geo_id, "acs", value, low, high, moe, "2020-2024")


def answer(facts, blocks, caveats=()):
    return Answer("1", ("house", "022"), "none", blocks, facts, [], list(caveats))


def test_single_place_gets_plain_lead_and_tiles(small_con):
    out = build(
        small_con, answer([fact("f1", "022", 1000.0, 100.0)], ["{geo:house:022} has {fact:f1} kūpuna."])
    )
    assert out["lead"] == ["House District 22 has 1,000 kūpuna."]
    assert out["tiles"] == [
        {"value": "1,000", "label": "Population 65 years and over", "spread": "± 100", "geography": ""}
    ]
    assert out["charts"] == []
    assert out["sources"] == [
        {"name": "American Community Survey 5-year", "url": "https://example.org", "year": "2020-2024"}
    ]


def test_two_places_become_a_chart_with_a_margin_tag(small_con):
    facts = [fact("f2", "023", 100.0, 80.0), fact("f1", "022", 1000.0, 100.0)]
    out = build(small_con, answer(facts, ["{geo:house:022} has more than {geo:house:023}."]))
    (chart,) = out["charts"]
    assert [b["geography"] for b in chart["bars"]] == ["House District 22", "House District 23"]
    assert chart["tag"] == "Higher than House District 23" and chart["lower"] is True
    assert chart["bars"][1]["low"] == 20.0 and chart["bars"][1]["high"] == 180.0
    assert out["tiles"] == []


def test_overlapping_margins_tag_within_margin(small_con):
    facts = [fact("f1", "022", 1000.0, 100.0), fact("f2", "023", 950.0, 80.0)]
    (chart,) = build(small_con, answer(facts, ["Close."]))["charts"]
    assert chart["tag"] == "Within the margin of error"


def test_range_facts_read_as_about_and_caveats_fill_markers(small_con):
    f = fact("f1", "022", 1000.0, None, 900.0, 1100.0)
    out = build(small_con, answer([f], ["{fact:f1} people."], ["{label:f1} is apportioned."]))
    assert out["lead"] == ["about 1,000 people."]
    assert out["tiles"][0]["spread"] == "range 900 to 1,100"
    assert out["caveats"] == ["Population 65 years and over is apportioned."]


def test_tiles_name_other_places(small_con):
    out = build(small_con, answer([fact("f1", "023", 100.0, 80.0)], ["x"]))
    assert out["tiles"][0]["geography"] == "House District 23"


def test_lead_drops_inline_vintages(small_con):
    f = fact("f1", "022", 1000.0, 100.0)
    out = build(small_con, answer([f], ["* {label:f1}: {fact:f1} ({vintage:f1})"]))
    assert out["lead"] == ["* Population 65 years and over: 1,000"]


def test_district_total_against_the_state_reads_as_a_share(small_con):
    state = FactRef("f2", "pop_65_plus", "state", "15", "acs", 50000.0, None, None, 500.0, "2020-2024")
    (chart,) = build(small_con, answer([fact("f1", "022", 1000.0, 100.0), state], ["Kūpuna here."]))["charts"]
    assert chart["tag"] == "2.0% of Hawaiʻi" and chart["lower"] is False


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
