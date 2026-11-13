# Tests for outreach pairs: the ranking behind the outreach page, its layout, its endpoint, and the outreach note on
# district profiles.
import json
import re
import threading
from functools import partial
from http.client import HTTPConnection
from types import SimpleNamespace

import pytest
import yaml

from klear.agent import config
from klear.agent.tools import Session, ToolError
from klear.eval.contract import FactRef
from klear.labels import CATALOG
from klear.programs import ELIGIBILITY_NOTE, catalog
from klear.web import __main__ as web
from klear.web import display
from tests.fixtures.small_store import INCOME_TEST, add_outreach, build_small_store


@pytest.fixture
def outreach_con(tmp_path):
    con = build_small_store(tmp_path / "outreach.duckdb")
    add_outreach(con)
    yield con
    con.close()


def _serve(path):
    server = web.Server(("127.0.0.1", 0), partial(web.Handler, store=path))
    threading.Thread(target=server.serve_forever, daemon=True).start()
    return server


@pytest.fixture
def endpoint(outreach_con, tmp_path):
    outreach_con.close()
    server = _serve(tmp_path / "outreach.duckdb")
    yield server.server_port
    server.shutdown()
    server.server_close()


def get(port, path):
    con = HTTPConnection("127.0.0.1", port, timeout=10)
    con.request("GET", path)
    response = con.getresponse()
    data = response.read()
    con.close()
    return response.status, json.loads(data), response.headers


# the pairs


def test_pairs_rank_income_test_counts_and_name_catalog_programs():
    entries = yaml.safe_load(CATALOG.read_text(encoding="utf-8"))["indicators"]
    notes = {e["indicator_id"]: e.get("note", "") for e in entries}
    programs = {p.program_id for p in catalog()}
    assert config.OUTREACH_DEFAULT in config.OUTREACH_PAIRS
    for pair, spec in config.OUTREACH_PAIRS.items():
        assert notes[spec["indicator"]].startswith("Meets the income test only"), pair
        assert spec["programs"] and set(spec["programs"]) <= programs, pair
    # The figures beside each group are the profile's, so the two pages agree.
    uses = {
        p: config.PROFILE_BESIDE.get(s["indicator"], (None,))[0] for p, s in config.OUTREACH_PAIRS.items()
    }
    assert uses == {
        "snap": "hh_snap",
        "med_quest": None,
        "marketplace": "aca_aptc_consumers",
        "kupuna": "hh_ssi",
        "eitc": "irs_eitc_returns",
    }


def test_page_offers_every_pair_as_written_in_config():
    html = (web.STATIC / "outreach.html").read_text(encoding="utf-8")
    options = re.findall(
        r'name="pair" value="(\w+)"[^>]*><span class="pair-title">(.*?)</span><span class="pair-who">(.*?)</span></label>',
        html,
    )
    plain = [(v, re.sub(r"<[^>]+>", "", t), re.sub(r"<[^>]+>", "", w)) for v, t, w in options]
    assert plain == [
        (k, s["title"], s["who"][:1].upper() + s["who"][1:]) for k, s in config.OUTREACH_PAIRS.items()
    ]
    assert {"/outreach.html", "/outreach.js"} <= set(web.PUBLIC)
    for page in ("index.html", "checks.html", "glossary.html", "methods.html", "outreach.html"):
        nav = re.search(
            r'<nav aria-label="Main navigation">(.*?)</nav>', (web.STATIC / page).read_text("utf-8")
        )
        assert 'href="/outreach.html"' in nav.group(1), page


# the ranking


def test_outreach_ranks_the_group_and_sets_the_use_figure_beside_it(outreach_con):
    s = Session(outreach_con)
    out = s.outreach("snap", "house")
    assert [[d["geo"] for d in t["districts"]] for t in out["tiers"]] == [["house:022"], ["house:023"]]
    assert out["indicator_id"] == "hh_poverty_no_snap" and out["note"] == INCOME_TEST
    assert s.facts[out["state"]["ref"]].value == 31000.0
    assert out["use"] == "hh_snap"
    assert {g: s.facts[f["ref"]].value for g, f in out["beside"].items()} == {
        "house:022": 900.0,
        "house:023": 300.0,
    }
    assert out["unreliable"] == 1


def test_outreach_refuses_unknown_pairs_and_stores_without_the_group(outreach_con, small_con):
    with pytest.raises(ToolError, match="pair"):
        Session(outreach_con).outreach("wic", "house")
    with pytest.raises(ToolError, match="chamber"):
        Session(outreach_con).outreach("snap", "county")
    with pytest.raises(ToolError, match="unknown indicator"):
        Session(small_con).outreach("snap", "house")


def test_tops_lists_the_pairs_whose_top_tier_holds_the_district(outreach_con):
    s = Session(outreach_con)
    top = s.outreach_tops(("house", "022"))
    assert [(t["pair"], t["shared"]) for t in top["tops"]] == [("snap", 1)] and top["ranked"] == 1
    assert top["tops"][0]["fact"]["geo"] == "house:022"
    assert s.outreach_tops(("house", "023")) == {"tops": [], "ranked": 1}


# the layout


def test_layout_names_the_top_tier_and_flags_unreliable_estimates(outreach_con):
    s = Session(outreach_con)
    out = display.outreach(outreach_con, s.outreach("snap", "house"), s)
    assert out["heading"] == "Households in poverty without SNAP, by House district"
    assert out["lead"] == (
        "House District 22 has the most households in poverty that did not get SNAP of any House district: 400 ± 60."
    )
    assert [t["title"] for t in out["tiers"]] == ["Top group", "Second group"]
    assert out["tiers"][0]["note"] == "Its range sits above the range of every other district."
    first, second = out["tiers"][0]["districts"][0], out["tiers"][1]["districts"][0]
    assert first == {
        "geo": "house:022",
        "name": "House District 22",
        "path": "/district/house-22",
        "value": "400",
        "spread": "± 60",
        "reliable": True,
        "caution": "",
        "use": {"value": "900", "spread": "± 120"},
    }
    assert (
        second["caution"] == "The margin of error is large compared with the estimate."
        and second["reliable"] is False
    )
    assert (
        out["unreliable"]
        == "1 of these 2 estimates is unreliable because its margin of error is large. It is marked."
    )
    assert out["state"] == {"name": "Hawaiʻi", "value": "31,000", "spread": "± 1,500"}
    assert out["caveat"] == INCOME_TEST and out["note"] == ELIGIBILITY_NOTE
    assert out["use"]["label"] == "Number of households receiving SNAP"
    assert out["use"]["note"].startswith("This column is for comparison only. It does not set the groups.")
    [snap] = out["programs"]
    assert snap["name"] == "SNAP" and snap["phone"] and snap["url"].startswith("https://")
    assert out["year"] == "2020-2024 ACS"
    assert out["sources"] == [
        {"name": "American Community Survey 5-year", "url": "https://example.org", "year": "2020-2024"}
    ]
    assert [p["id"] for p in out["pairs"]] == list(config.OUTREACH_PAIRS)


def test_a_chamber_without_figures_says_so_and_counts_no_estimates(outreach_con):
    s = Session(outreach_con)
    out = display.outreach(outreach_con, s.outreach("snap", "senate"), s)
    assert out["tiers"] == [] and out["unreliable"] == "" and out["year"] == ""
    assert out["lead"] == "No Senate district has a figure for households in poverty that did not get SNAP."
    assert [(m["name"], m["path"]) for m in out["missing"]] == [("Senate District 12", "/district/senate-12")]


def test_a_figure_without_a_margin_is_listed_outside_the_groups(outreach_con):
    outreach_con.execute(
        "UPDATE fact SET moe = NULL, moe_status = 'not_computable' "
        "WHERE indicator_id = 'hh_poverty_no_snap' AND geo_type = 'house' AND geo_id = '023'"
    )
    s = Session(outreach_con)
    out = display.outreach(outreach_con, s.outreach("snap", "house"), s)
    assert [[r["geo"] for r in t["districts"]] for t in out["tiers"]] == [["house:022"]]
    [left] = out["unranked"]
    assert (left["name"], left["value"], left["reason"]) == ("House District 23", "150", display.UNRANKED)


class _Places:
    def place(self, geo_type, geo_id):
        return f"{geo_type.title()} District {int(geo_id)}"

    def unit(self, f):
        return "count"


def _fact(geo_id, value, moe):
    return FactRef(f"f{geo_id}", "g", "house", geo_id, "acs", value, None, None, moe, "2020-2024")


def test_lead_names_a_small_top_tier_in_district_order_and_counts_a_large_one():
    who = "households in poverty that did not get SNAP"
    small = [_fact("024", 1755.0, 268.0), _fact("002", 1200.0, 371.0)]
    assert display._outreach_lead(small, _Places(), who, "house", 51) == (
        "House District 2 (1,200 ± 371) and House District 24 (1,755 ± 268) have the most households in poverty "
        "that did not get SNAP. Their ranges overlap, so the data cannot say which is highest."
    )
    large = [_fact(f"{k:03}", 200.0 + k, 100.0) for k in range(1, 7)]
    assert display._outreach_lead(large, _Places(), who, "senate", 25) == (
        "6 of 25 Senate districts share the top group for households in poverty that did not get SNAP, from 201 to "
        "206 each. Their ranges overlap, so no one district stands out."
    )
    assert display._outreach_lead([], _Places(), who, "house", 51) == (
        "No House district has a figure for households in poverty that did not get SNAP."
    )


def test_later_tiers_say_only_what_the_ranges_support():
    assert (
        display._tier_note(0, 3)
        == "Their ranges overlap, so the data cannot say which of these districts is highest."
    )
    assert display._tier_note(1, 1) == "It is clearly below the highest district in the group above."
    assert "cannot put them in order" in display._tier_note(2, 4)


# the endpoint and the profile note


def test_outreach_endpoint_matches_the_profile(endpoint):
    status, page, headers = get(endpoint, "/api/outreach?pair=snap&chamber=house")
    assert status == 200 and headers["Cache-Control"] == "no-store"
    rows = {r["geo"]: r for t in page["tiers"] for r in t["districts"]}
    for geo in ("house:022", "house:023"):
        profile = get(endpoint, f"/api/profile?district={geo}")[1]
        row = next(r for s in profile["sections"] for r in s["rows"] if r["id"] == "hh_poverty_no_snap")
        assert (rows[geo]["value"], rows[geo]["spread"], rows[geo]["caution"]) == (
            row["value"],
            row["spread"],
            row["caution"],
        )
        assert (rows[geo]["use"]["value"], rows[geo]["use"]["spread"]) == (
            row["beside"]["value"],
            row["beside"]["spread"],
        )
    # With no choice made, the page opens on SNAP in the House.
    assert get(endpoint, "/api/outreach")[1]["lead"] == page["lead"]


@pytest.mark.parametrize("query", ["pair=wic&chamber=house", "pair=snap&chamber=county", "pair=../x"])
def test_outreach_endpoint_refuses_unknown_choices(endpoint, query):
    status, body, headers = get(endpoint, f"/api/outreach?{query}")
    assert status == 400 and "House or Senate" in body["error"] and headers["Cache-Control"] == "no-store"


def test_outreach_endpoint_says_when_the_store_lacks_the_group(small_con, tmp_path):
    small_con.close()
    server = _serve(tmp_path / "small.duckdb")
    try:
        status, body, _ = get(server.server_port, "/api/outreach?pair=snap&chamber=house")
        assert status == 503 and "no figures for this group" in body["error"]
        assert get(server.server_port, "/api/profile?district=house:022")[1]["outreach"] is None
    finally:
        server.shutdown()
        server.server_close()


def test_profile_notes_where_the_district_is_in_a_top_tier(endpoint):
    note = get(endpoint, "/api/profile?district=house:022")[1]["outreach"]
    assert note["title"] == "Outreach opportunities" and note["none"] == ""
    [item] = note["items"]
    assert item["text"] == (
        "House District 22 has the most households in poverty that did not get SNAP of any House district: 400 ± 60."
    )
    assert item["link"] == "Compare House districts for SNAP"
    assert item["url"] == "/outreach.html?pair=snap&chamber=house"
    other = get(endpoint, "/api/profile?district=house:023")[1]["outreach"]
    assert other["items"] == []
    assert (
        other["none"]
        == "House District 23 is not in the top group of House districts for any outreach measure."
    )
    assert other["link"]["url"] == "/outreach.html?chamber=house"


def test_profile_note_names_at_most_two_pairs_smallest_tier_first():
    tops = {
        "tops": [
            {"pair": p, "fact": {"ref": "f1", "reliable": True}, "shared": n}
            for p, n in (("kupuna", 2), ("snap", 4), ("eitc", 21))
        ],
        "ranked": 5,
    }

    session = SimpleNamespace(
        facts={"f1": FactRef("f1", "g", "house", "004", "acs", 2839.0, None, None, 618.0, "2020-2024")}
    )
    note = display.outreach_note({"name": "House District 4", "geo": "house:004"}, tops, session, _Places())
    assert [i["url"] for i in note["items"]] == [
        "/outreach.html?pair=kupuna&chamber=house",
        "/outreach.html?pair=snap&chamber=house",
    ]
    assert note["items"][0]["text"] == (
        "House District 4 is one of 2 House districts in the top group for kūpuna with income under 200% of "
        "poverty, with 2,839 ± 618. Their ranges overlap."
    )
    assert note["more"] == "It is in the top group for 1 more measure."


# the built store


@pytest.mark.parametrize("chamber", config.CHAMBER_TYPES)
@pytest.mark.parametrize("pair", list(config.OUTREACH_PAIRS))
def test_every_pair_names_its_top_tier_with_the_profile_figures(real_con, pair, chamber):
    s = Session(real_con)
    out = display.outreach(real_con, s.outreach(pair, chamber), s)
    top = out["tiers"][0]["districts"]
    assert top and out["year"] and out["sources"] and out["programs"]
    named = [r["name"] for r in top] if len(top) <= display.NAMED_MAX else [f"{len(top)} of"]
    assert all(n in out["lead"] for n in named)
    # The first district of the top tier reads the same on its profile.
    first = top[0]
    p = Session(real_con)
    profile = display.profile(real_con, json.loads(p.run("district_profile", {"district": first["geo"]})), p)
    row = next(
        r
        for sec in profile["sections"]
        for r in sec["rows"]
        if r["id"] == config.OUTREACH_PAIRS[pair]["indicator"]
    )
    assert (first["value"], first["spread"], first["caution"]) == (
        row["value"],
        row["spread"],
        row["caution"],
    )
    if first["use"]:
        assert (first["use"]["value"], first["use"]["spread"]) == (
            row["beside"]["value"],
            row["beside"]["spread"],
        )
