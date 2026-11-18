"""Compare's figures: two districts' profiles aligned row by row, so a row one profile left out is never shown as a
missing figure, a real gap says why, and county-level figures say whose they are."""

import json
import threading
from contextlib import contextmanager
from functools import partial
from http.client import HTTPConnection

import pandas as pd
import pytest

from klear.agent.tools import Session
from klear.store.db import add_coverage, add_facts, add_indicators
from klear.web import __main__ as web
from klear.web import display
from tests.fixtures.small_store import INDUSTRY_SHARES, _fact, add_county_context, add_industries

H22, H23 = ("house", "022"), ("house", "023")
WORK = "Work and education"
COUNTY = "County-level figures"


def _section(p: dict, title: str) -> dict:
    return next(s for s in p["sections"] if s["title"] == title)


def _ids(p: dict, title: str) -> list[str]:
    return [r["id"] for r in _section(p, title)["rows"]]


@pytest.fixture
def store(small_con):
    add_industries(small_con)
    add_county_context(small_con)
    # A median age for House 22 only: House 23's is suppressed, as a source does for too few cases.
    add_indicators(
        small_con,
        pd.DataFrame(
            [("median_age", "Median age", "years", "people", None, False)],
            columns=["indicator_id", "label", "unit", "universe", "acs_var", "additive"],
        ),
    )
    add_coverage(small_con, pd.DataFrame([("median_age", "house")], columns=["indicator_id", "geo_type"]))
    add_facts(
        small_con,
        pd.DataFrame(
            [
                _fact("house", "022", "median_age", 44.1, 1.2),
                _fact(
                    "house",
                    "023",
                    "median_age",
                    None,
                    None,
                    value_status="suppressed",
                    moe_status="not_computable",
                ),
            ]
        ),
    )
    return small_con


def test_each_profile_still_shows_only_its_own_three_largest_industries(store):
    session = Session(store)
    for geo in (H22, H23):
        p = display.profile(store, session.district_profile(geo), session)
        industries = [i for i in _ids(p, WORK) if i in INDUSTRY_SHARES]
        assert len(industries) == 3
        assert _section(p, WORK)["intro"].startswith("The industry rows are the district's three largest")
    p22 = display.profile(store, session.district_profile(H22), session)
    assert "pct_industry_public_admin" not in _ids(p22, WORK)


def test_compare_shows_every_industry_either_profile_shows_for_both_districts(store):
    a, b = display.comparison(store, [H22, H23])["districts"]
    industries = [i for i in _ids(a, WORK) if i in INDUSTRY_SHARES]
    assert set(industries) == set(INDUSTRY_SHARES)
    assert _ids(a, WORK) == _ids(b, WORK)
    for p, geo in ((a, "022"), (b, "023")):
        rows = {r["id"]: r for r in _section(p, WORK)["rows"]}
        for i in INDUSTRY_SHARES:
            value, moe = INDUSTRY_SHARES[i][geo]
            assert "missing" not in rows[i], (p["district"], i)
            assert (rows[i]["num"], rows[i]["spread"]) == (value, f"± {moe:.1f} points")
    assert _section(a, WORK)["intro"].startswith(
        "The industry rows are the industries among either district's"
    )


def test_swapping_the_districts_keeps_the_same_rows_in_the_same_order(store):
    ab = display.comparison(store, [H22, H23])["districts"]
    ba = display.comparison(store, [H23, H22])["districts"]
    assert [p["geo"] for p in ba] == ["house:023", "house:022"]
    for title in (s["title"] for s in ab[0]["sections"]):
        assert _ids(ab[0], title) == _ids(ba[1], title) == _ids(ba[0], title)
    assert [r["id"] for r in ab[0]["headline"]] == [r["id"] for r in ba[0]["headline"]]


def test_a_real_gap_keeps_its_row_and_says_why_with_no_figure(store):
    a, b = display.comparison(store, [H22, H23])["districts"]
    age_a = next(r for r in _section(a, "People")["rows"] if r["id"] == "median_age")
    age_b = next(r for r in _section(b, "People")["rows"] if r["id"] == "median_age")
    assert age_a["value"] == "44.1" and "missing" not in age_a
    assert age_b["missing"] == "Too few cases for the source to publish"
    assert "value" not in age_b and "num" not in age_b and age_b["label"] == age_a["label"]


def test_county_level_figures_name_their_county_for_each_district(store):
    a, b = display.comparison(store, [H22, H23])["districts"]
    for p in (a, b):
        county = _section(p, COUNTY)
        assert county["geo"] == "county:15003" and county["place"] == "Honolulu County"
        assert (
            county["scope"]
            == f"These figures cover all of Honolulu County. They are not published for {p['district']}."
        )
    # Both districts point at the same county figure, which the page shows once.
    assert _section(a, COUNTY)["rows"][0]["value"] == _section(b, COUNTY)["rows"][0]["value"]


def test_rows_keep_their_source_year_and_method(store):
    a, _ = display.comparison(store, [H22, H23])["districts"]
    row = next(r for r in _section(a, WORK)["rows"] if r["id"] == "pct_industry_retail")
    assert row["source"] == {
        "name": "American Community Survey 5-year",
        "url": "https://example.org",
        "year": "2020-2024",
    }
    assert {"geo", "place", "source"} <= set(row["against"][0])


@contextmanager
def _serving(path):
    with web.Server(("127.0.0.1", 0), partial(web.Handler, store=path)) as server:
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        try:
            yield server.server_port
        finally:
            server.shutdown()
            thread.join()


def _get(port, path):
    con = HTTPConnection("127.0.0.1", port, timeout=10)
    con.request("GET", path)
    response = con.getresponse()
    body = response.read()
    con.close()
    return response.status, json.loads(body)


def test_compare_endpoint_serves_the_aligned_figures_and_refuses_bad_choices(store, tmp_path):
    store.close()
    with _serving(tmp_path / "small.duckdb") as port:
        status, data = _get(port, "/api/compare?d=house:022,house:23")
        assert status == 200 and [p["geo"] for p in data["districts"]] == ["house:022", "house:023"]
        assert _ids(data["districts"][0], WORK) == _ids(data["districts"][1], WORK)
        assert "programs" not in data["districts"][0]
        status, one = _get(port, "/api/compare?d=house:022")
        assert status == 200 and len(one["districts"]) == 1
        for bad in ("house:022,house:022", "house:099", "house:022,house:023,senate:012", "county:15003", ""):
            status, error = _get(port, f"/api/compare?d={bad}")
            assert status == 400 and error == {"error": "Choose one or two stored House or Senate districts."}
