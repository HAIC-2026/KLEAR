"""The district map data matches the store and is served with the page, and the map can be shaded by a measure."""

import json
import re
import threading
from functools import partial
from itertools import pairwise

import duckdb
import pandas as pd
import pytest

from klear.config import ROOT
from klear.labels import short_label
from klear.store.db import add_facts, add_geographies, add_indicators
from klear.web import __main__ as web
from klear.web import shade
from tests.agent.test_web import endpoint, request  # noqa: F401
from tests.fixtures.small_store import _fact, build_small_store

MAP = ROOT / "klear" / "web" / "static" / "districts.json"
COUNTS = {"house": 51, "senate": 25}
POVERTY = "pct_people_poverty"


def test_map_has_every_district_once():
    data = json.loads(MAP.read_text(encoding="utf-8"))
    for chamber, count in {"house": 51, "senate": 25}.items():
        rows = data["chambers"][chamber]
        assert [r["geo"] for r in rows] == [f"{chamber}:{n:03}" for n in range(1, count + 1)]
        for row in rows:
            assert row["d"].startswith("M") and row["group"] in data["groups"]
            assert len(row["label"]) == 2 and row["islands"]
    assert set(data["groups"]) == {"all", "kauai", "oahu", "honolulu", "maui", "hawaii"}


def test_map_files_are_public(endpoint):  # noqa: F811
    for path in ["/map.js", "/districts.json"]:
        assert request(endpoint, "GET", path)[0] == 200
    status, page = request(endpoint, "GET", "/")
    assert status == 200 and b'src="/map.js"' in page and b'id="map"' in page


def test_zip_table_covers_hawaii_zips():
    data = json.loads(MAP.read_text(encoding="utf-8"))
    geos = {c: {r["geo"] for r in rows} for c, rows in data["chambers"].items()}
    assert len(data["zips"]) == 97
    for zcta, entry in data["zips"].items():
        assert zcta.startswith("96")
        for chamber, rows in entry.items():
            assert {g for g, _ in rows} <= geos[chamber]
            if rows:
                assert abs(sum(s for _, s in rows) - 1) < 0.01
    assert [g for g, _ in data["zips"]["96744"]["house"]] == ["house:048", "house:049", "house:050"]


# Shading the map by a measure


def _every_district(path):
    """The small store with every House and Senate district and a poverty rate for each. House 50's figure is
    suppressed and House 51's is unreliable (a margin of error over half the figure); House 1 and 2 tie."""
    con = build_small_store(path)
    have = {("house", "022"), ("house", "023"), ("senate", "012")}
    geos = [
        (c, f"{n:03}", f"{c.title()} {n}", "15003")
        for c, count in COUNTS.items()
        for n in range(1, count + 1)
        if (c, f"{n:03}") not in have
    ]
    add_geographies(con, pd.DataFrame(geos, columns=["geo_type", "geo_id", "name", "parent_county"]))
    add_indicators(
        con,
        pd.DataFrame(
            [(POVERTY, "Percent of people below the poverty level", "percent", "people", None, False)],
            columns=["indicator_id", "label", "unit", "universe", "acs_var", "additive"],
        ),
    )
    rows = [_fact("state", "15", POVERTY, 10.0, 0.3)]
    for c, count in COUNTS.items():
        for n in range(1, count + 1):
            if (c, n) == ("house", 50):
                rows.append(
                    _fact(
                        c, "050", POVERTY, None, None, value_status="suppressed", moe_status="not_computable"
                    )
                )
            elif (c, n) == ("house", 51):
                rows.append(_fact(c, "051", POVERTY, 4.0, 3.0))
            else:
                rows.append(_fact(c, f"{n:03}", POVERTY, 3.0 + max(n, 2) * 0.4, 1.0))
    add_facts(con, pd.DataFrame(rows))
    return con


@pytest.fixture
def every(tmp_path):
    con = _every_district(tmp_path / "every.duckdb")
    yield con
    con.close()


@pytest.fixture
def every_site(tmp_path):
    path = tmp_path / "every.duckdb"
    _every_district(path).close()
    server = web.Server(("127.0.0.1", 0), partial(web.Handler, store=path))
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    yield server.server_port
    server.shutdown()
    server.server_close()
    thread.join()


def _check_shades(view: dict, count: int):
    """Every district once, highest first and then those with no figure, in up to five shades of about equal size
    whose ranges never overlap."""
    districts = view["districts"]
    assert sorted(d["geo"] for d in districts) == [f"{view['chamber']}:{n:03}" for n in range(1, count + 1)]
    shown = [d for d in districts if d["status"] != shade.MISSING]
    assert districts[: len(shown)] == shown
    assert [d["number"] for d in shown] == sorted((d["number"] for d in shown), reverse=True)
    assert all(d["shade"] in range(1, 6) for d in shown)
    assert all(d["shade"] is None and d["value"] == shade.MISSING for d in districts[len(shown) :])
    shades = view["shades"]
    assert [s["shade"] for s in shades] == list(range(1, 6))
    assert sum(s["count"] for s in shades) == len(shown)
    # Equal figures stay together, so a shade can hold a few more or fewer than a fifth.
    assert all(abs(s["count"] - len(shown) / 5) <= 2 for s in shades), [s["count"] for s in shades]
    for low, high in pairwise(shades):
        assert low["high"] < high["low"]
    for d in shown:
        s = shades[d["shade"] - 1]
        assert s["low"] <= d["number"] <= s["high"] and d["low"] <= d["number"] <= d["high"]


def test_every_district_has_a_shade_or_a_mark(every):
    house = shade.map_view(every, POVERTY, "house")
    _check_shades(house, 51)
    assert (house["label"], house["unit"], house["title"]) == (
        "People below the poverty level",
        "percent",
        "People below the poverty level, by House district",
    )
    assert house["year"] == "2020-2024" and house["source"]["name"] == "American Community Survey 5-year"
    assert house["state"] == {"name": "Hawaiʻi", "value": "10.0%", "spread": "± 0.3 points", "number": 10.0}
    assert house["overlap"] == "Districts whose margins of error overlap may not differ."
    by_geo = {d["geo"]: d for d in house["districts"]}
    assert by_geo["house:050"]["status"] == "not available" and house["districts"][-1]["geo"] == "house:050"
    assert by_geo["house:050"]["text"] == (
        "People below the poverty level: no figure. Too few cases for the source to publish."
    )
    assert by_geo["house:051"]["status"] == "unreliable"
    assert by_geo["house:051"]["text"].endswith(" Unreliable estimate: the margin of error is large.")
    assert house["unreliable"].startswith("1 of the 50 estimates is unreliable")
    # Equal figures share a shade.
    assert by_geo["house:001"]["shade"] == by_geo["house:002"]["shade"]
    assert by_geo["house:022"] == {
        "geo": "house:022",
        "name": "House District 22",
        "value": "11.8%",
        "spread": "± 1.0 points",
        "number": pytest.approx(11.8),
        "low": 10.8,
        "high": 12.8,
        "status": "ok",
        "caution": "",
        "text": (
            "People below the poverty level: 11.8% ± 1.0 points. "
            "About 20 percent higher than the rate in Hawaiʻi: 10.0%."
        ),
        "shade": 3,
    }
    senate = shade.map_view(every, POVERTY, "senate")
    _check_shades(senate, 25)
    assert [s["count"] for s in senate["shades"]] == [5] * 5 and not senate["unreliable"]


def test_few_figures_spread_across_the_shades():
    assert shade._ramp(5) == [1, 2, 3, 4, 5]
    assert shade._ramp(3) == [1, 3, 5]
    assert shade._ramp(2) == [1, 5]
    assert shade._ramp(1) == [3]
    # Figures that read the same never straddle two shades.
    figures = [(v, f"{v:.1f}%") for v in (1, 2, 2, 2, 2, 3, 4, 5, 6, 7)]
    groups = shade._groups(figures)
    assert len(groups) <= 5 and [i for g in groups for i in g] == list(range(10))
    assert [i for g in groups if 1 in g for i in g] == [1, 2, 3, 4]


def test_map_figures_match_each_district_profile(every_site):
    status, body = request(every_site, "GET", f"/api/map?indicator={POVERTY}&chamber=house")
    assert status == 200
    view = json.loads(body)
    # The lowest and highest, a middle one, and the unreliable one: a profile takes a moment to build.
    picked = {"house:001", "house:022", "house:049", "house:051"}
    for d in view["districts"]:
        if d["geo"] not in picked:
            continue
        _, raw = request(every_site, "GET", f"/api/profile?district={d['geo']}")
        rows = {r["id"]: r for s in json.loads(raw)["sections"] for r in s["rows"]}
        row = rows[POVERTY]
        assert (row["value"], row["spread"], row["caution"]) == (d["value"], d["spread"], d["caution"])
        state = row["against"][-1]
        assert f" {state['lead'][0].upper()}{state['lead'][1:]} {state['rest']}." in d["text"]


def test_map_requests_name_a_listed_measure_and_chamber(every_site):
    for query in [
        "indicator=pop_total&chamber=house",
        f"indicator={POVERTY}&chamber=county",
        "",
        "chamber=house",
    ]:
        status, body = request(every_site, "GET", f"/api/map?{query}")
        assert status == 400 and json.loads(body)["error"].startswith("Choose one of the listed measures")
    # A listed measure the store lacks
    status, body = request(every_site, "GET", "/api/map?indicator=pct_uninsured&chamber=house")
    assert status == 503 and "no figures for this measure" in json.loads(body)["error"]
    status, body = request(every_site, "GET", f"/api/map?indicator={POVERTY}&chamber=senate")
    assert status == 200 and len(json.loads(body)["districts"]) == 25


def test_map_without_a_store_says_so(tmp_path):
    server = web.Server(("127.0.0.1", 0), partial(web.Handler, store=tmp_path / "none.duckdb"))
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        status, body = request(server.server_port, "GET", f"/api/map?indicator={POVERTY}&chamber=house")
        assert status == 503 and json.loads(body)["error"].startswith("The map figures are unavailable")
    finally:
        server.shutdown()
        server.server_close()
        thread.join()


def test_the_map_lists_each_measure_by_its_short_label():
    page = (web.STATIC / "index.html").read_text(encoding="utf-8")
    select = re.search(r'<select id="map-shade">(.*?)</select>', page).group(1)
    options = re.findall(r'<option value="([^"]*)">([^<]*)</option>', select)
    assert options[0] == ("", "Nothing (plain map)")
    assert [v for v, _ in options[1:]] == list(shade.MEASURES)
    for value, text in options[1:]:
        assert text == short_label(value, ""), value
    assert '<label for="map-shade">Color the map by</label>' in page
    assert "<summary>Show as a table</summary>" in page


def test_shading_uses_classes_never_style_attributes():
    script = (web.STATIC / "map.js").read_text(encoding="utf-8")
    assert not re.search(r"\.style\.(?!setProperty\('--label')|setAttribute\('style'|cssText", script)
    css = (web.STATIC / "style.css").read_text(encoding="utf-8")
    for q in range(1, 6):
        assert f"#map.shaded .district.q{q}{{fill:var(--q{q})}}" in css
        assert f".swatch.q{q}{{background:var(--q{q})}}" in css


@pytest.fixture(scope="module")
def built():
    store = ROOT / "data/build/klear.duckdb"
    if not store.exists():
        pytest.skip("Build the store to check the shaded map on real figures")
    con = duckdb.connect(str(store), read_only=True)
    yield con
    con.close()


@pytest.mark.parametrize("indicator", shade.MEASURES)
def test_built_store_shades_every_district(built, indicator):
    """Every listed measure shades all 51 House and all 25 Senate districts in five shades."""
    for chamber, count in COUNTS.items():
        view = shade.map_view(built, indicator, chamber)
        _check_shades(view, count)
        assert all(d["status"] != shade.MISSING for d in view["districts"])
        assert view["state"] and view["year"] and view["source"]["url"].startswith("https://")
    assert indicator not in {
        r[0] for r in built.execute("SELECT indicator_id FROM indicator WHERE additive").fetchall()
    }
