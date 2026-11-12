"""Website answers show a plain lead, number tiles, comparison charts and one source list."""

import json
import shutil

from klear.agent.config import PROFILE_BESIDE
from klear.agent.tools import Session
from klear.config import CURRENT_LAYER
from klear.eval.contract import Answer, FactRef
from klear.store.db import add_source
from klear.web.display import PROFILE_LABELS, RULES, build, profile, programs


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


def test_charts_say_what_they_show_in_a_sentence_and_keep_table_figures(small_con):
    facts = [fact("f1", "022", 1000.0, 100.0), fact("f2", "023", 100.0, 80.0)]
    (chart,) = build(small_con, answer(facts, ["x"]))["charts"]
    assert (
        chart["summary"] == "House District 22, at 1,000 ± 100, is higher than House District 23 (100 ± 80)."
    )
    assert [(b["number"], b["spread"]) for b in chart["bars"]] == [("1,000", "± 100"), ("100", "± 80")]


def test_chart_sentences_name_shares_ranges_and_overlapping_margins(small_con):
    state = FactRef("f3", "pop_65_plus", "state", "15", "acs", 50000.0, None, None, None, "2020-2024")
    county = FactRef("f4", "pop_65_plus", "county", "15003", "acs", 20000.0, None, None, 500.0, "2020-2024")
    ranged = fact("f1", "022", 1000.0, None, 900.0, 1100.0)
    (chart,) = build(small_con, answer([ranged, county, state], ["x"]))["charts"]
    # A district's total is part of the county's and the state's, so it has no direction against them.
    assert chart["summary"] == (
        "House District 22, at about 1,000 (range 900 to 1,100), compares with Honolulu County (20,000 ± 500)"
        " and is 2.0% of the total for Hawaiʻi (50,000)."
    )
    close = [fact("f1", "022", 1000.0, 100.0), fact("f2", "023", 950.0, 80.0)]
    (chart,) = build(small_con, answer(close, ["x"]))["charts"]
    assert chart["summary"].endswith("is within the margin of error of House District 23 (950 ± 80).")


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
    assert set(rows) >= set(PROFILE_BESIDE) and all(r["label"] == PROFILE_LABELS[i] for i, r in rows.items())
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


def test_programs_follow_rule_file_changes_and_survive_broken_rules(tmp_path):
    rules = tmp_path / "rules"
    shutil.copytree(RULES, rules)
    names = [p["name"] for p in programs(rules)]
    assert "WIC" in names and names.count("Federal EITC") == 1
    # The list is read again when a rule file changes.
    wic = rules / "wic_2026_27.yaml"
    text = wic.read_text(encoding="utf-8")
    wic.write_text(text.replace("status: confirmed", "status: needs_manual"), encoding="utf-8")
    assert "WIC" not in [p["name"] for p in programs(rules)]
    # A rule file that cannot be read leaves the list empty rather than failing the profile.
    wic.write_text("status: [broken", encoding="utf-8")
    assert programs(rules) == []
    assert programs(tmp_path / "missing") == []
