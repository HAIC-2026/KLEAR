# Tests for the deterministic agent tools against the built store and a small fixture store.
import json
import statistics
import time

import pytest

from klear.agent.config import FIXED_INCOME, PROFILE_SECTIONS
from klear.agent.tools import TOOL_SPECS, Session, _dedupe, _Named
from klear.eval.golden import load_golden
from klear.programs import catalog


def call(s, tool, **args):
    return json.loads(s.run(tool, args))


def first_geo(text):
    return json.loads(text)["matches"][0]["geo"]


def store_value(con, indicator, geo_type, geo_id):
    return con.execute(
        "SELECT value FROM fact WHERE indicator_id=? AND geo_type=? AND geo_id=?",
        [indicator, geo_type, geo_id],
    ).fetchone()[0]


def overall_direction(out, only=None):
    dirs = {c["direction"] for c in out["comparisons"] if only is None or c["geo"] in only}
    if "within_margin" in dirs:
        return "within_margin"
    return dirs.pop() if len(dirs) == 1 else "mixed"


def compare_goldens():
    return [g for g in load_golden() if g.category == "compare"]


# real store


def test_resolve_messy_district(session):
    assert first_geo(session.run("resolve_place", {"text": "hse dist 22"})) == "house:022"
    assert first_geo(session.run("resolve_place", {"text": "Senatte 17"})) == "senate:017"
    for text, geo in (("H22", "house:022"), ("SD 12", "senate:012"), ("senate 17", "senate:017")):
        assert first_geo(session.run("resolve_place", {"text": text})) == geo


@pytest.mark.parametrize(
    ("text", "geo"),
    [
        ("Hawaiʻi House District 31", "house:031"),
        ("House District 16 in Hawaiʻi", "house:016"),
        ("Hawaii Senate District 8", "senate:008"),
        ("State of Hawaiʻi House District 3", "house:003"),
        ("Hawaiʻi's Senate District 12", "senate:012"),
        ("HI SD 8", "senate:008"),
        ("House District 22, Hawaii", "house:022"),
        ("Senate District 9 in the State of Hawaii", "senate:009"),
    ],
)
def test_resolve_district_with_state_name(session, text, geo):
    out = call(session, "resolve_place", text=text)
    assert [m["geo"] for m in out["matches"]] == [geo]


def test_state_name_alone_or_after_a_place(session):
    for text in ("Hawaii", "Hawaiʻi", "State of Hawaiʻi"):
        assert [m["geo"] for m in call(session, "resolve_place", text=text)["matches"]] == ["state:15"]
    out = call(session, "resolve_place", text="Kāneʻohe, Hawaiʻi")["matches"]
    assert [m["name"] for m in out] == ["Kāneʻohe"]


def test_no_match_gives_a_hint(session):
    for text in ("Atlantis", "House District 99", "Hawaiʻi House District 99"):
        out = call(session, "resolve_place", text=text)
        assert out["matches"] == [] and "House District 22" in out["hint"] and "ZIP code" in out["hint"]
    assert "hint" not in call(session, "resolve_place", text="House District 22")


def test_resolve_place_unaccented(session):
    out = json.loads(session.run("resolve_place", {"text": "kaneohe"}))
    assert out["matches"][0]["name"] == "Kāneʻohe"
    assert any(g["geo"].startswith("house:") for g in out["matches"][0]["overlaps"])
    assert "Kāneʻohe" in session.places
    assert any(g[0] == "house" for g in session.resolved)


def test_ambiguous_place_returns_every_candidate(session):
    out = call(session, "resolve_place", text="Kailua")
    assert len(out["matches"]) == 2 and all(m["overlaps"] for m in out["matches"])


def test_unknown_place_no_guess(session):
    assert call(session, "resolve_place", text="Atlantis")["matches"] == []
    assert call(session, "resolve_place", text="House District 99")["matches"] == []


def test_zip_resolves_to_zcta_and_districts(session):
    out = call(session, "resolve_place", text="96744")
    m = out["matches"][0]
    assert m["geo"] == "zcta:96744" and any(o["geo"].startswith("house:") for o in m["overlaps"])
    assert ("zcta", "96744") in session.resolved and ("zcta", "96744") in session.geos


def test_get_facts_reuses_refs_and_fills_store_values(session, real_con):
    a = json.loads(session.run("get_facts", {"indicator_ids": ["pop_total"], "geos": ["house:018"]}))
    b = json.loads(session.run("get_facts", {"indicator_ids": ["pop_total"], "geos": ["house:018"]}))
    assert a["facts"][0]["ref"] == b["facts"][0]["ref"] == "f1"
    assert session.facts["f1"].value == store_value(real_con, "pop_total", "house", "018")


@pytest.mark.parametrize("g", compare_goldens(), ids=lambda g: g.id)
def test_compare_direction_matches_goldens(session, g):
    others = [f"{t}:{i}" for _, t, i in g.facts[1:]]
    out = call(session, "compare", indicator_id=g.facts[0][0], geo=":".join(g.facts[0][1:]), against=others)
    assert overall_direction(out, set(others)) == g.direction


def test_bad_args_return_error_json(session):
    assert "error" in json.loads(session.run("get_facts", {"indicator_ids": "pop_total"}))
    assert "error" in call(session, "nope")
    assert "error" in call(session, "get_facts", indicator_ids=["pop_total"], geos=["bogus"])
    assert "error" in call(session, "resolve_place")


def test_specs_cover_every_tool(session):
    assert [t["name"] for t in TOOL_SPECS] == [
        "resolve_place", "search_indicators", "get_facts", "compare", "district_profile", "rank_districts",
        "list_programs", "district_programs", "find_sites", "explain_source",
    ]  # fmt: skip
    assert all(t["input_schema"]["type"] == "object" and t["description"] for t in TOOL_SPECS)


def test_real_store_tools_smoke(session):
    assert call(session, "find_sites", geo="house:022")["sites"]
    assert (
        call(session, "list_programs", words=["snap"])["programs"][0]["rules"][0]["rule_id"] == "snap_fy2027"
    )
    assert (
        call(session, "explain_source", source_id="acs2024_5yr_profile")["publisher"] == "U.S. Census Bureau"
    )
    assert call(session, "search_indicators", words=["kūpuna"])["indicators"]
    for words in (["rent"], ["households", "rent"], ["share of households that rent"], ["tenure"]):
        ids = [i["indicator_id"] for i in call(session, "search_indicators", words=words)["indicators"]]
        # two CHAS renter counts tie with it on "renter", so allow six of the twelve results
        assert "pct_renter_occupied" in ids[:6], words


@pytest.mark.parametrize("words", [["fixed income"], ["kupuna", "fixed", "incomes"]])
def test_fixed_income_search_finds_every_fixed_income_figure(session, words):
    ids = [i["indicator_id"] for i in call(session, "search_indicators", words=words)["indicators"]]
    assert set(FIXED_INCOME) <= set(ids), words


# small store


def test_search_indicators_ranks_and_expands_aliases(small):
    ids = [i["indicator_id"] for i in call(small, "search_indicators", words=["kupuna"])["indicators"]]
    assert ids[0] == "pop_65_plus"
    ids = [i["indicator_id"] for i in call(small, "search_indicators", words=["keiki"])["indicators"]]
    assert ids == ["pop_under_18"]
    out = call(small, "search_indicators", words=["population"])["indicators"]
    assert out[0]["geo_types"] == ["county", "house", "state"]
    assert call(small, "search_indicators", words=["zzz"])["indicators"] == []


def test_resolve_place_tiers_and_fuzzy(small):
    assert call(small, "resolve_place", text="Kaneohe")["matches"][0]["name"] == "Kāneʻohe"
    assert call(small, "resolve_place", text="kane")["matches"][0]["name"] == "Kāneʻohe"
    assert call(small, "resolve_place", text="kaneoe")["matches"][0]["name"] == "Kāneʻohe"
    assert len(call(small, "resolve_place", text="kailua")["matches"]) == 2
    out = call(small, "resolve_place", text="kaneohe")["matches"][0]["overlaps"]
    assert {"geo": "house:022", "name": "House District 22", "share": 0.75} == {
        k: out[0][k] for k in ("geo", "name", "share")
    }
    assert ("senate", "012") in small.resolved and ("county", "15003") in small.resolved


def test_resolved_is_separate_from_geos(small):
    call(small, "get_facts", indicator_ids=["pop_65_plus"], geos=["house:023"])
    assert ("house", "023") in small.geos and ("house", "023") not in small.resolved


def test_get_facts_fields_statuses_and_missing(small):
    out = call(
        small,
        "get_facts",
        indicator_ids=["pop_65_plus", "pop_under_18", "nope"],
        geos=["house:22", "county:15005", "zcta:96744"],
    )
    f = next(x for x in out["facts"] if x["indicator_id"] == "pop_65_plus")
    assert f["geo"] == "house:022" and f["geo_name"] == "House District 22" and f["source"] == "ACS"
    assert f["source_id"] == "acs"
    assert f["value_text"] == "1,000 (margin of error ±100)" and f["vintage"] == "2020-2024"
    assert f["reliable"] is True and "plus or minus" in f["status"]
    reasons = {(m["indicator_id"], m["geo"]): m["reason"] for m in out["missing"]}
    assert reasons[("pop_65_plus", "county:15005")] == "no Kalawao row"
    assert "not published" in reasons[("pop_65_plus", "zcta:96744")]
    assert "unknown indicator" in reasons[("nope", "house:022")]
    assert reasons[("pop_under_18", "house:022")] == "too few cases for the source to publish"
    assert all(x["indicator_id"] != "pop_under_18" for x in out["facts"])
    assert len(small.facts) == 1


def test_unreliable_when_cv_high(small):
    f = call(small, "get_facts", indicator_ids=["pop_65_plus"], geos=["house:023"])["facts"][0]
    assert f["reliable"] is False


def test_compare_directions(small):
    out = call(small, "compare", indicator_id="pop_65_plus", geo="house:022")
    by = {c["geo"]: c["direction"] for c in out["comparisons"]}
    assert by == {"county:15001": "within_margin", "county:15003": "lower", "state:15": "lower"}
    assert out["facts"][0]["ref"] == "f1"
    vs = call(small, "compare", indicator_id="pop_65_plus", geo="house:022", against=["house:023"])
    assert {c["geo"]: c["direction"] for c in vs["comparisons"]}["house:023"] == "higher"
    low = call(small, "compare", indicator_id="pop_65_plus", geo="house:023")
    assert {c["direction"] for c in low["comparisons"]} == {"lower"}
    assert "missing" in call(small, "compare", indicator_id="pop_under_18", geo="house:023")


def test_find_sites_and_kind_filter(small):
    out = call(small, "find_sites", geo="house:022")
    assert out["sites"] == [{"kind": "lihtc", "name": "Aloha Apts"}] and out["facts"]
    assert "address" not in json.dumps(out)
    assert call(small, "find_sites", geo="house:022", kind="health_center")["sites"] == []
    assert "error" in call(small, "find_sites", geo="county:15003")
    assert "error" in call(small, "find_sites", geo="house:022", kind="zoo")


def test_programs_and_source(small):
    assert len(call(small, "list_programs")["programs"]) == len(catalog())
    p = call(small, "list_programs", words=["kupuna"])["programs"]
    assert [x["program_id"] for x in p] == ["kupuna_care"] and p[0]["agency"] and p[0]["rules"][0]["status"]
    s = call(small, "explain_source", source_id="hud")
    assert s["name"] == "Public Housing" and s["url"] == "https://example.org/hud"
    assert "error" in call(small, "explain_source", source_id="nope")


def test_county_and_island_names(small):
    for text, geo in (("Honolulu County", "county:15003"), ("Big Island", "county:15001")):
        assert call(small, "resolve_place", text=text)["matches"][0]["geo"] == geo
    assert ("county", "15003") in small.resolved
    county = call(small, "resolve_place", text="Honolulu County")["matches"][0]
    assert [(o["geo"], o["share"]) for o in county["overlaps"]] == [
        ("house:022", 0.8475), ("house:023", 0.1525), ("senate:012", 1.0)
    ]  # fmt: skip
    oahu = call(small, "resolve_place", text="Oʻahu")["matches"]
    assert [(m["kind"], m["name"], m["island"]) for m in oahu] == [("island", "Oʻahu", "Oʻahu")]
    assert {"geo": "county:15003", "of_county": 1.0}.items() <= oahu[0]["overlaps"][-1].items()
    assert "Oʻahu" in small.places
    assert call(small, "resolve_place", text="Maui")["matches"][0]["geo"] == "county:15009"
    assert call(small, "resolve_place", text="Kalawao")["matches"][0]["geo"] == "county:15005"
    assert [m["geo"] for m in call(small, "resolve_place", text="Kalaupapa")["matches"]] == ["county:15005"]


def test_whole_word_place_match(session):
    names = {m["name"] for m in call(session, "resolve_place", text="Honolulu")["matches"]}
    assert names == {"Urban Honolulu", "East Honolulu"}


def test_short_words_never_fuzzy_match_places(session):
    for text in ("help", "care", "food", "rent", "housing", "senior"):
        assert call(session, "resolve_place", text=text)["matches"] == [], text
    for text, name in (("kaneohe", "Kāneʻohe"), ("waianae", "Waiʻanae"), ("kaneohee", "Kāneʻohe")):
        assert call(session, "resolve_place", text=text)["matches"][0]["name"] == name
    assert len(call(session, "resolve_place", text="kailua")["matches"]) == 2


def test_reliable_flag_zero_and_no_moe(small_con, small):
    small_con.execute(
        "INSERT INTO fact VALUES ('house','023','pop_under_18',0,5,'ok','ok','D','acs',NULL,NULL,NULL)"
    )
    f = call(small, "get_facts", indicator_ids=["pop_under_18"], geos=["house:023"])["facts"][0]
    assert f["reliable"] is False
    f = call(small, "get_facts", indicator_ids=["pop_65_plus"], geos=["state:15"])["facts"][0]
    assert f["reliable"] is None
    f = call(small, "get_facts", indicator_ids=["pop_65_plus"], geos=["house:022"])["facts"][0]
    assert f["reliable"] is True


def test_agencies(session):
    out = {p["program_id"]: p["agency"] for p in call(session, "list_programs")["programs"]}
    assert "Department of Health" in out["wic"] and "WIC" in out["wic"]
    assert all(out.values())


def test_facts_name_their_method(session):
    out = call(
        session,
        "get_facts",
        indicator_ids=["irs_eitc_returns", "places_received_snap", "pct_hh_snap"],
        geos=["house:011"],
    )
    by = {f["indicator_id"]: f["method"] for f in out["facts"]}
    assert "apportioned" in by["irs_eitc_returns"] and "not bounded" in by["irs_eitc_returns"]
    assert "bounds it" not in " ".join(by.values())
    hcv = call(session, "get_facts", indicator_ids=["hud_vouchers"], geos=["house:011"])["facts"][0]["method"]
    assert "apportioned" in hcv and "ZIP code or tract" in hcv
    assert "modeled" in by["places_received_snap"]
    assert by["pct_hh_snap"] == "published for this geography"


def test_programs_say_what_they_cover_and_match_on_it(session):
    programs = call(session, "list_programs")["programs"]
    assert all(p["covers"] for p in programs)
    hits = [p["program_id"] for p in call(session, "list_programs", words=["vouchers"])["programs"]]
    assert "public_housing" in hits
    food = [p["program_id"] for p in call(session, "list_programs", words=["food"])["programs"]]
    assert "snap" in food


def test_main_runs_tools_in_one_session(real_con, capsys):
    from klear.agent.tools import main

    assert (
        main(
            [
                "resolve_place",
                '{"text": "House District 18"}',
                "get_facts",
                '{"indicator_ids": ["pop_total"], "geos": ["house:018"]}',
            ]
        )
        == 0
    )
    out = capsys.readouterr().out
    assert '"geo": "house:018"' in out and '"ref": "f1"' in out
    assert main(["resolve_place"]) == 2


@pytest.mark.parametrize("text", ["Atlantis Kai", "Hawaiʻi Atlantis"])
def test_multiword_unknown_place_is_no_match_with_hint(session, text):
    out = call(session, "resolve_place", text=text)
    assert out["matches"] == [] and "hint" in out


def test_fuzzy_tier_is_single_word_only(small):
    assert call(small, "resolve_place", text="kaneoe")["matches"][0]["name"] == "Kāneʻohe"
    assert call(small, "resolve_place", text="Kaneoe, HI")["matches"][0]["name"] == "Kāneʻohe"
    out = call(small, "resolve_place", text="Kane Ohe")
    assert out["matches"] == [] and "hint" in out


@pytest.mark.parametrize("text", ["the Big Island", "Big Island of Hawaiʻi", "the island of Hawaii"])
def test_big_island_phrases_resolve_to_hawaii_county(small, text):
    assert [m["geo"] for m in call(small, "resolve_place", text=text)["matches"]] == ["county:15001"]


def test_district_profile_header_and_comparisons(small):
    out = call(small, "district_profile", district="house:022")
    assert out["district"] == {"geo": "house:022", "name": "House District 22"}
    assert [(c["geo"], c["share"]) for c in out["counties"]] == [
        ("county:15003", round(50 / 53, 4)),
        ("county:15001", round(3 / 53, 4)),
    ]
    assert [(o["geo"], o["share"]) for o in out["overlaps"]] == [("senate:012", 1.0)]
    assert {p["name"] for p in out["places"]} == {"Kāneʻohe", "Kailua"}
    assert [s["title"] for s in out["sections"]] == [title for title, _ in PROFILE_SECTIONS]
    rows = {r["fact"]["indicator_id"]: r for s in out["sections"] for r in s["rows"]}
    # A total is set against its main county and the state as the district's share.
    assert [(a["geo"], a["share"]) for a in rows["pop_65_plus"]["against"]] == [
        ("county:15003", "20.0%"),
        ("state:15", "11.1%"),
    ]
    assert rows["sites_lihtc"]["fact"]["value_text"] == "2"
    # a place with no value is listed with the reason, never dropped
    assert rows["sites_lihtc"]["against"] == [
        {
            "geo": "county:15003",
            "geo_name": "Honolulu County",
            "missing": "not published for county geographies",
        },
        {"geo": "state:15", "geo_name": "Hawaiʻi", "missing": "not published for state geographies"},
    ]
    assert {"indicator_id": "pop_under_18", "geo": "house:022"}.items() <= next(
        m for m in out["missing"] if m["indicator_id"] == "pop_under_18"
    ).items()


def test_district_profile_needs_a_district(small):
    assert "error" in call(small, "district_profile", district="county:15003")
    assert "error" in call(small, "district_profile", district="house:999")


def test_every_district_profile_is_complete_and_fast(real_con):
    districts = real_con.execute(
        "SELECT geo_type, geo_id FROM geography WHERE geo_type IN ('house', 'senate') ORDER BY 1, 2"
    ).fetchall()
    assert len(districts) == 76
    took = []
    for geo_type, geo_id in districts:
        start = time.perf_counter()
        out = json.loads(Session(real_con).run("district_profile", {"district": f"{geo_type}:{geo_id}"}))
        took.append(time.perf_counter() - start)
        assert took[-1] < 3, f"{geo_type}:{geo_id} took too long"
        rows = [r for s in out["sections"] for r in s["rows"]]
        assert len(rows) >= 30 and out["counties"], f"{geo_type}:{geo_id}"
        for r in rows:
            f = r["fact"]
            assert f["geo"] == f"{geo_type}:{geo_id}" and f["source"] and f["vintage"] and f["status"], f
        rates = [a for r in rows for a in r["against"] if "direction" in a]
        assert rates and all(a["direction"] in ("higher", "lower", "within_margin") for a in rates)
        # every profile figure is set against its main county and the state
        for r in rows:
            where, ind = [a["geo"].split(":")[0] for a in r["against"]], r["fact"]["indicator_id"]
            assert where == ["county", "state"] and not [a for a in r["against"] if "missing" in a], (
                geo_id,
                ind,
            )
    assert statistics.median(took) < 1, "profiles are slow"


def test_neighborhoods_other_names_and_islands(small):
    out = call(small, "resolve_place", text="Waikiki")["matches"]
    assert [(m["kind"], m["name"], m["island"]) for m in out] == [("neighborhood", "Waikīkī", "Oʻahu")]
    assert [o["geo"] for o in out[0]["overlaps"]] == ["house:022", "senate:012", "county:15003"]
    assert "Waikīkī" in small.places and ("house", "022") in small.resolved
    assert [m["area_id"] for m in call(small, "resolve_place", text="Waikiki Beach")["matches"]] == ["nb09"]
    kailua = call(small, "resolve_place", text="Kailua")["matches"]
    assert sorted(m["island"] for m in kailua) == ["Hawaiʻi Island", "Oʻahu"]
    assert call(small, "resolve_place", text="96744")["matches"][0]["island"] == "Oʻahu"


def test_no_match_says_why(small):
    out = call(small, "resolve_place", text="House 52")
    assert out["matches"] == [] and out["note"].startswith("Hawaiʻi has 2 House districts") and "hint" in out
    out = call(small, "resolve_place", text="96799")
    assert out["matches"] == [] and "ZIP code 96799 has no area" in out["note"]


def test_shared_name_keeps_the_island_then_the_place():
    def named(kind, id_, island="Oʻahu"):
        return _Named(kind, id_, "Kailua", "kailua", 1, island)

    found = [
        named("division", "d"),
        named("neighborhood", "n"),
        named("place", "p"),
        named("place", "q", "Maui"),
    ]
    assert [n.id for n in _dedupe(found)] == ["p", "q"]
    assert [n.id for n in _dedupe([named("division", "d"), named("island", "i")])] == ["i"]
    assert [n.id for n in _dedupe([named("place", "p"), named("place", "p")])] == ["p"]


def test_store_without_area_tables_searches_places(small_con):
    for table in ("area_alias", "area_overlap", "area"):
        small_con.execute(f"DROP TABLE {table}")
    s = Session(small_con)
    assert [m["island"] for m in call(s, "resolve_place", text="Kailua")["matches"]] == [None, None]
    assert call(s, "resolve_place", text="Waikiki")["matches"] == []
    assert call(s, "resolve_place", text="96744")["matches"][0]["island"] is None


def test_block_match_lists_the_address_districts(small):
    m = small.block_match("150030001001001", "1 TEST ST, HONOLULU, HI, 96744")
    assert m["kind"] == "address" and m["island"] == "Oʻahu"
    assert [o["geo"] for o in m["overlaps"]] == ["house:023", "senate:012", "county:15003"]
    assert ("house", "023") in small.resolved
    assert small.block_match("159999999999999", "nowhere") is None


def _shares_add_up(match):
    for chamber in ("house", "senate"):
        shares = [o["share"] for o in match["overlaps"] if o["geo"].startswith(chamber)]
        assert shares and abs(sum(shares) - 1) < 1e-3, (match["name"], chamber)


@pytest.mark.parametrize(
    ("text", "want"),
    [
        (
            "Kona",
            [("division", "North Kona", "Hawaiʻi Island"), ("division", "South Kona", "Hawaiʻi Island")],
        ),
        ("Waikīkī", [("neighborhood", "Waikīkī", "Oʻahu")]),
        ("waikiki", [("neighborhood", "Waikīkī", "Oʻahu")]),
        ("Honolulu", [("place", "East Honolulu", "Oʻahu"), ("place", "Urban Honolulu", "Oʻahu")]),
        ("Maui", [("island", "Maui", "Maui")]),
        ("Molokaʻi", [("island", "Molokaʻi", "Molokaʻi")]),
        ("Oʻahu", [("island", "Oʻahu", "Oʻahu")]),
        ("Big Island", [("island", "Hawaiʻi Island", "Hawaiʻi Island")]),
        ("Kailua", [("place", "Kailua", "Hawaiʻi Island"), ("place", "Kailua", "Oʻahu")]),
        ("Kāneʻohe", [("place", "Kāneʻohe", "Oʻahu")]),
        ("Kalihi", [("neighborhood", "Kalihi Valley", "Oʻahu"), ("neighborhood", "Kalihi-Pālama", "Oʻahu")]),
        ("Kakaako", [("neighborhood", "Ala Moana-Kakaʻako", "Oʻahu")]),
        ("upcountry Maui", [("division", "Kula", "Maui"), ("division", "Makawao-Pāʻia", "Maui")]),
        ("Kailua-Kona", [("place", "Kailua", "Hawaiʻi Island")]),
        ("Lānaʻi", [("island", "Lānaʻi", "Lānaʻi")]),
    ],
)
def test_issue_examples_name_their_island_and_shares(session, text, want):
    out = call(session, "resolve_place", text=text)["matches"]
    assert sorted((m["kind"], m["name"], m["island"]) for m in out) == want
    for m in out:
        _shares_add_up(m)


def test_islands_list_their_districts(session):
    molokai = call(session, "resolve_place", text="Molokai")["matches"][0]
    assert [(o["geo"], o["share"]) for o in molokai["overlaps"] if o["geo"][0] in "hs"] == [
        ("house:013", 1.0), ("senate:007", 1.0)
    ]  # fmt: skip
    maui = call(session, "resolve_place", text="Maui")["matches"][0]
    assert next(o for o in maui["overlaps"] if o["geo"] == "county:15009")["of_county"] < 1
    oahu = call(session, "resolve_place", text="Oahu")["matches"][0]
    assert sum(o["geo"].startswith("house:") for o in oahu["overlaps"]) > 30
    county = call(session, "resolve_place", text="Maui County")["matches"][0]
    assert county["geo"] == "county:15009" and county["overlaps"]
    _shares_add_up(county)


def test_zip_and_place_name_their_island(session):
    assert call(session, "resolve_place", text="96744")["matches"][0]["island"] == "Oʻahu"
    assert call(session, "resolve_place", text="Lānaʻi City")["matches"][0]["island"] == "Lānaʻi"


@pytest.mark.parametrize(("text", "note"), [("House 52", "51 House"), ("Senate 26", "25 Senate")])
def test_out_of_range_district_says_how_many(session, text, note):
    out = call(session, "resolve_place", text=text)
    assert out["matches"] == [] and f"Hawaiʻi has {note} districts" in out["note"]


def _suggested(s, text):
    return [(x["label"], x["detail"], x["query"]) for x in s.suggest(text)]


def test_suggestions_for_names_zips_and_districts(small):
    assert small.suggest("k") == [] and small.suggest("") == []
    assert _suggested(small, "ka") == [
        ("Kāneʻohe", "Oʻahu · Town", "place:1500001"),
        ("Kailua", "Oʻahu · Town", "place:1500002"),
        ("Kailua", "Hawaiʻi Island · Town", "place:1500003"),
        ("Kalawao County", "County", "county:15005"),
    ]
    assert _suggested(small, "Wai") == [
        ("Waikīkī", "Oʻahu · Neighborhood", "area:nb09"),
        ("Waikīkī Beach", "Oʻahu · in Waikīkī", "waikiki beach"),
    ]
    assert _suggested(small, "oah") == [("Oʻahu", "Island", "area:oahu")]
    assert _suggested(small, "967") == [("ZIP code 96744", "Oʻahu", "96744")]
    assert _suggested(small, "house 2") == [
        ("House District 22", "Oʻahu", "House 22"),
        ("House District 23", "Oʻahu", "House 23"),
    ]
    assert _suggested(small, "sd1") == [("Senate District 12", "Oʻahu", "Senate 12")]
    assert small.suggest("h9") == [] and small.suggest("415 S Beretania") == []


def test_picked_suggestion_finds_exactly_it(small):
    out = call(small, "resolve_place", text="place:1500003")["matches"]
    assert [(m["name"], m["island"]) for m in out] == [("Kailua", "Hawaiʻi Island")]
    assert [m["name"] for m in call(small, "resolve_place", text="AREA:nb09")["matches"]] == ["Waikīkī"]
    assert call(small, "resolve_place", text="county:15003")["matches"][0]["geo"] == "county:15003"
    for text in ("place:9999999", "area:nb99", "county:15099"):
        out = call(small, "resolve_place", text=text)
        assert out["matches"] == [] and "hint" in out, text


def test_suggestions_on_the_built_store(session):
    ko = session.suggest("ko")
    assert len(ko) == 8 and (ko[0]["label"], ko[0]["detail"]) == ("Kona", "Hawaiʻi Island · 2 areas")
    assert {"North Kona", "Kōloa", "Koʻolauloa"} <= {s["label"] for s in ko}
    assert [s["label"] for s in session.suggest("maui")][:2] == ["Maui", "Maui County"]
    assert [s["label"] for s in session.suggest("upcountry")] == ["Upcountry"]
    for text in ("ko", "wai", "hono", "maui", "lan", "kai", "96", "house 2", "sen 1", "st l"):
        for s in session.suggest(text):
            out = call(Session(session.con), "resolve_place", text=s["query"])["matches"]
            assert out, (text, s)
            if s["kind"] in ("place", "island", "neighborhood", "division"):
                assert [m["name"] for m in out] == [s["label"]], (text, s)
