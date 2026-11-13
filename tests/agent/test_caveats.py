# Tests that district caveats come from data rules, reach the tools, profiles and answers, and cite sources.
import json

from klear import caveats
from klear.agent.tools import Session
from klear.eval.contract import Answer, FactRef
from klear.web.display import build, profile

PICKED = {
    "maui_wildfires": ["house:012", "house:014", "senate:006", "senate:007"],
    "population_base": [
        "house:030", "house:031", "house:032", "house:040", "house:041", "house:046", "house:047", "house:050",
        "senate:015", "senate:023", "senate:024",
    ],
    "kalawao": ["county:15005", "house:013", "senate:007"],
}  # fmt: skip


def test_every_rule_has_a_title_text_and_public_sources():
    units, rules = caveats.load()
    assert set(units.values()) == {"Hawaiʻi Island", "Oʻahu", "Maui County", "Kauaʻi"}
    for rule in rules:
        assert rule.title and rule.text and chr(0x2014) not in rule.text
        assert rule.sources and all(s["url"].startswith("https://") and s["name"] for s in rule.sources)


def test_rules_pick_the_wildfire_military_and_kalawao_districts_from_the_store(real_con):
    assert caveats.picked(real_con) == PICKED
    named = {n for r in caveats.load()[1] for n in r.places}
    found = {
        r[0]
        for r in real_con.execute(
            "SELECT name FROM place WHERE name IN (SELECT unnest(?))", [list(named)]
        ).fetchall()
    }
    assert found == named


def test_caveat_text_fills_in_the_places_and_counts_behind_it(real_con):
    notes = {c["id"]: c["text"] for c in caveats.for_places(real_con, [("house", "046")])}
    assert set(notes) == {"population_base"}
    assert (
        "Military housing areas here include Schofield Barracks and Wheeler AFB." in notes["population_base"]
    )
    assert "The 2020 Census counted 51,085 people here" in notes["population_base"]
    (kalawao,) = caveats.for_places(real_con, [("county", "15005")])
    assert "the 2020 Census counted 82 people there" in kalawao["text"]


def test_the_small_store_has_no_caveats(small_con):
    geos = [("house", "022"), ("house", "023"), ("senate", "012"), ("county", "15005")]
    assert caveats.for_places(small_con, geos) == []


def test_tools_return_place_caveats_so_the_model_need_not_remember_them(real_con):
    s = Session(real_con)
    out = json.loads(
        s.run("get_facts", {"indicator_ids": ["pct_people_poverty"], "geos": ["house:014", "state:15"]})
    )
    assert [(c["geo"], c["title"]) for c in out["place_caveats"]] == [("house:014", "Maui wildfires")]
    profile_raw = json.loads(s.run("district_profile", {"district": "house:013"}))
    assert [c["title"] for c in profile_raw["place_caveats"]] == ["Kalawao County"]
    ranked = json.loads(s.run("rank_districts", {"indicator": "pop_total", "chamber": "house"}))
    base = next(c for c in ranked["place_caveats"] if c["title"] == "Military and student population")
    assert "house:046" in base["geos"] and "Schofield" not in base["text"]
    plain = json.loads(s.run("get_facts", {"indicator_ids": ["pct_people_poverty"], "geos": ["house:022"]}))
    assert "place_caveats" not in plain


def test_profiles_and_answers_show_caveats_with_their_sources(real_con):
    s = Session(real_con)
    raw = json.loads(s.run("district_profile", {"district": "house:014"}))
    (note,) = profile(real_con, raw, s)["caveats"]
    assert note["title"] == "Maui wildfires" and "August 2023" in note["text"] and len(note["sources"]) == 2
    f = FactRef("f1", "pct_people_poverty", "house", "014", "acs2024_5yr_profile", 9.0, None, None, 2.0, "x")
    other = FactRef(
        "f2", "pct_people_poverty", "house", "046", "acs2024_5yr_profile", 9.0, None, None, 2.0, "x"
    )
    out = build(real_con, Answer("1", ("house", "014"), "none", ["x"], [f, other], [], []))
    assert [c["title"] for c in out["place_caveats"]] == [
        "Maui wildfires",
        "Military and student population: House District 46",
    ]
    decline = build(real_con, Answer("1", ("house", "014"), "decline_partisan", ["x"], [], [], []))
    assert decline["place_caveats"] == []
