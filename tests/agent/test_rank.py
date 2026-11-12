# Tests for rank_districts: sorting, tiers of overlapping ranges, county filter and answers that cite it.
import json

import pandas as pd
import pytest

from klear.agent import config
from klear.agent.answer import build_answer
from klear.agent.tools import Session
from klear.store.db import add_blocks, add_facts, add_geographies, add_indicators, add_source, open_store

NOTE = "Meets the income test only."


def call(s, **args):
    return json.loads(s.run("rank_districts", args))


def _fact(geo_id, value, moe, **kw):
    row = {
        "geo_type": "house", "geo_id": geo_id, "indicator_id": "u", "value": value, "moe": moe,
        "value_status": "ok", "moe_status": "ok", "tier": "D", "source_id": "acs", "table_id": "C27016",
    }  # fmt: skip
    return row | kw


@pytest.fixture
def ranked(tmp_path):
    con = open_store(tmp_path / "rank.duckdb")
    add_source(
        con, "acs", "American Community Survey 5-year", "Census", "https://example.org", "2020-2024", "pd"
    )
    geos = [("state", "15", "Hawaiʻi"), ("county", "15003", "Honolulu"), ("county", "15001", "Hawaii")]
    geos += [("house", f"00{k}", f"House {k}") for k in range(1, 7)]
    add_geographies(
        con, pd.DataFrame([(*g, None) for g in geos], columns=["geo_type", "geo_id", "name", "parent_county"])
    )
    add_indicators(
        con,
        pd.DataFrame(
            [
                {"indicator_id": "u", "label": "Uninsured under 138 percent", "unit": "count", "universe": "U",
                 "acs_var": None, "additive": True, "note": NOTE},
            ]
        ),
    )  # fmt: skip
    rows = [
        _fact("001", 100.0, 10.0),  # 90 to 110, first tier
        _fact("002", 95.0, 10.0),  # 85 to 105, overlaps 001
        _fact("003", 70.0, 5.0),  # 65 to 75, second tier
        _fact("004", 60.0, 20.0),  # 40 to 80, below 001, overlaps 003
        _fact("005", None, None, value_status="suppressed", moe_status="not_computable"),
        _fact("006", 50.0, None, moe_status="not_computable"),
    ]
    add_facts(con, pd.DataFrame(rows))
    blocks = [(k, "15003" if k < 5 else "15001", 10) for k in range(1, 7)] + [(2, "15001", 30)]
    add_blocks(
        con,
        pd.DataFrame(
            [
                {"block": f"b{i}", "county": c, "tract": "t", "zcta": None, "house": f"00{k}", "senate": "001",
                 "pop2020": p}
                for i, (k, c, p) in enumerate(blocks)
            ]
        ),
    )  # fmt: skip
    yield Session(con)
    con.close()


def tier_geos(tier):
    return [d["geo"] for d in tier["districts"]]


def test_districts_are_sorted_and_grouped_into_overlapping_tiers(ranked):
    out = call(ranked, indicator="u", chamber="house")
    assert [tier_geos(t) for t in out["tiers"]] == [["house:001", "house:002"], ["house:003", "house:004"]]
    assert [t["tier"] for t in out["tiers"]] == [1, 2]
    first = out["tiers"][0]["districts"][0]
    assert (
        first["value_text"].startswith("100") and first["ref"] in ranked.facts and first["reliable"] is True
    )
    assert out["unranked"] == [
        {"ref": "f5", "geo": "house:006", "geo_name": "House District 6", "value_text": "50",
         "reliable": None, "reason": "X, margin of error not available, treat as uncertain"}
    ]  # fmt: skip
    assert [m["geo"] for m in out["missing"]] == ["house:005"]
    assert out["note"] == NOTE and out["how_to_read"] == config.RANK_NOTE and out["order"] == "highest first"
    assert out["unreliable"] == 0


def test_ranked_districts_may_be_named_in_the_answer(ranked):
    out = call(ranked, indicator="u", chamber="house")
    refs = [d["ref"] for d in out["tiers"][0]["districts"]]
    a, b = refs
    blocks = [f"{{geo:house:001}} has {{fact:{a}}} and {{geo:house:002}} has {{fact:{b}}} ({{vintage:{a}}})."]
    args = {
        "resolved": None,
        "refusal": "none",
        "blocks": blocks,
        "facts": refs,
        "programs": [],
        "caveats": [],
    }
    answer, problems = build_answer(ranked, args)
    assert problems == [] and answer is not None


def test_county_keeps_districts_with_most_of_their_people_there(ranked):
    out = call(ranked, indicator="u", chamber="house", county="county:15003")
    # house 002 has 10 people in Honolulu and 30 in Hawaiʻi County, so it belongs to neither ranking of Honolulu
    assert [g for t in out["tiers"] for g in tier_geos(t)] == ["house:001", "house:003", "house:004"]
    assert out["county"] == "county:15003" and ("county", "15003") in ranked.resolved
    by_name = call(ranked, indicator="u", chamber="house", county="Hawaii County")
    assert by_name["county"] == "county:15001"
    assert [g for t in by_name["tiers"] for g in tier_geos(t)] == ["house:002"]


@pytest.mark.parametrize(
    ("args", "error"),
    [
        ({"indicator": "u", "chamber": "county"}, "chamber"),
        ({"indicator": "nope", "chamber": "house"}, "unknown indicator"),
        ({"indicator": "u", "chamber": "house", "county": "Atlantis"}, "unknown county"),
        ({"indicator": "u", "chamber": "house", "county": "house:001"}, "unknown county"),
        ({"chamber": "house"}, "indicator"),
    ],
)
def test_bad_arguments_return_errors(ranked, args, error):
    assert error in call(ranked, **args)["error"]


def _interval(s, ref):
    f = s.facts[ref]
    if f.value_low is not None:
        return f.value_low, f.value_high
    return f.value - (f.moe or 0), f.value + (f.moe or 0)


@pytest.mark.parametrize(
    ("indicator", "chamber"),
    [
        ("uninsured_under_138pct_poverty", "house"),
        ("hh_poverty_no_snap", "senate"),
        ("kupuna_under_200pct_poverty", "house"),
        ("irs_eitc_returns", "house"),
    ],
)
def test_tiers_never_claim_an_order_the_ranges_do_not_support(session, indicator, chamber):
    out = call(session, indicator=indicator, chamber=chamber)
    tiers = out["tiers"]
    placed = [d["geo"] for t in tiers for d in t["districts"]]
    others = [d["geo"] for d in out["unranked"]] + [m["geo"] for m in out["missing"]]
    assert sorted(placed + others) == sorted({*placed, *others}) and len(placed + others) == (
        51 if chamber == "house" else 25
    )
    for k, tier in enumerate(tiers):
        lead = _interval(session, tier["districts"][0]["ref"])
        assert all(_interval(session, d["ref"])[1] >= lead[0] for d in tier["districts"])
        later = [d for t in tiers[k + 1 :] for d in t["districts"]]
        assert all(_interval(session, d["ref"])[1] < lead[0] for d in later)
    leads = [session.facts[t["districts"][0]["ref"]].value for t in tiers]
    assert leads == sorted(leads, reverse=True)


def test_rank_within_a_real_county(session, real_con):
    out = call(session, indicator="hh_poverty_no_snap", chamber="house", county="Maui")
    assert out["county"] == "county:15009" and out["tiers"]
    for t in out["tiers"]:
        for d in t["districts"]:
            rows = real_con.execute(
                "SELECT county, sum(pop2020) FROM blocks WHERE house = ? GROUP BY 1", [d["geo"].split(":")[1]]
            ).fetchall()
            pops = dict(rows)
            assert pops.get("15009", 0) * 2 > sum(pops.values())
