"""Source links open pages people can read: each source's page, each ACS figure's table on data.census.gov, the
methods page for figures KLEAR estimated, and each program's page to learn or apply."""

import csv
import io
import json
import re
import threading
from functools import partial
from http.client import HTTPConnection
from http.server import ThreadingHTTPServer

import duckdb
import pandas as pd
import pytest

from klear.eval.contract import Answer, FactRef
from klear.rules.registry import load_rules
from klear.store.db import SCHEMA, add_facts, add_source
from klear.web import __main__ as web
from klear.web.display import SHARES_SOURCE, _cited, build, profile, profile_csv, programs
from tests.fixtures.small_store import _fact

DP05_HOUSE_22 = "https://data.census.gov/table/ACSDP5Y2024.DP05?g=620L900US15022"
PROFILES_PAGE = "https://www.census.gov/acs/www/data/data-tables-and-tools/data-profiles/2024/"


def answer(facts):
    return Answer("1", ("house", "022"), "none", ["x"], facts, [], [])


def _linked_sources(con):
    """The small store with an ACS profile figure and an IRS figure KLEAR moved to districts."""
    add_source(
        con, "acs2024_5yr_profile", "ACS Data Profiles", "Census", "https://api.census.gov/data/2024/acs/acs5/profile",
        "2020-2024 ACS 5-year", "pd", page_url=PROFILES_PAGE,
    )  # fmt: skip
    add_source(
        con, "irs_soi_zip_2023", "IRS SOI ZIP", "IRS", "https://www.irs.gov/pub/irs-soi/23zpallnoagi.csv",
        "Tax year 2023", "pd", page_url="https://www.irs.gov/statistics/soi-tax-stats",
    )  # fmt: skip
    rows = [
        _fact("house", "022", "pop_65_plus", 1000.0, 100.0, source_id="acs2024_5yr_profile", table_id="DP05"),
        _fact("state", "15", "pop_65_plus", 9000.0, 10.0, source_id="acs2024_5yr_profile", table_id="DP05"),
        _fact(
            "house", "022", "pop_under_18", 40.0, None, source_id="irs_soi_zip_2023", tier="A",
            moe_status="range", value_low=30.0, value_high=50.0,
        ),
    ]  # fmt: skip
    add_facts(con, pd.DataFrame(rows))


def test_sources_open_readable_pages_and_each_figure_can_be_checked(small_con):
    _linked_sources(small_con)
    acs = [
        FactRef("f1", "pop_65_plus", "house", "022", "acs2024_5yr_profile", 1000.0, None, None, 100.0, "2020-2024"),
        FactRef("f2", "pop_65_plus", "state", "15", "acs2024_5yr_profile", 9000.0, None, None, 10.0, "2020-2024"),
    ]  # fmt: skip
    irs = FactRef(
        "f3", "pop_under_18", "house", "022", "irs_soi_zip_2023", 40.0, 30.0, 50.0, None, "Tax year 2023"
    )
    plain = FactRef("f4", "pop_65_plus", "house", "023", "acs", 100.0, None, None, 80.0, "2020-2024")
    census, tax, other = build(small_con, answer([*acs, irs, plain]))["sources"]
    # The source name opens the publisher's page, never the API or file the build downloads.
    assert census["url"] == PROFILES_PAGE
    # Each ACS figure opens its own table for its own place on data.census.gov, built from its Census GEO_ID.
    assert census["links"] == [
        {"text": "Table DP05 for House District 22", "url": DP05_HOUSE_22},
        {
            "text": "Table DP05 for Hawaiʻi",
            "url": "https://data.census.gov/table/ACSDP5Y2024.DP05?g=0400000US15",
        },
    ]
    # A figure KLEAR moved to districts opens the methods section for its source instead.
    assert tax["url"] == "https://www.irs.gov/statistics/soi-tax-stats"
    assert tax["links"] == [
        {"text": "How KLEAR estimates district ranges", "url": "/methods.html#irs_soi_zip_2023"}
    ]
    # A source with nothing more to check is cited as before.
    assert other == {
        "name": "American Community Survey 5-year",
        "url": "https://example.org",
        "year": "2020-2024",
    }


def test_citations_merge_the_links_of_every_figure_from_a_source():
    table = {"text": "Table DP05 for House District 22", "url": "https://data.census.gov/table/x"}
    other = {"text": "Table DP02 for House District 22", "url": "https://data.census.gov/table/y"}
    cited = _cited(
        [
            {"name": "ACS", "url": "https://a", "year": "2024", "links": [table]},
            {"name": "IRS", "url": "https://b", "year": "2023", "links": []},
            {"name": "ACS", "url": "https://a", "year": "2024", "links": [other, table]},
        ]
    )
    assert cited == [
        {"name": "ACS", "url": "https://a", "year": "2024", "links": [table, other]},
        {"name": "IRS", "url": "https://b", "year": "2023"},
    ]


def test_programs_link_where_residents_learn_or_apply_not_the_page_a_rule_was_checked_on():
    rules = {r.rule_id: r for r in load_rules()}
    by_name = {p["name"]: p["url"] for p in programs()}
    assert by_name["SSI federal payment"] == rules["ssi_federal_2026"].info_url == "https://www.ssa.gov/ssi"
    assert rules["ssi_federal_2026"].source_url.endswith(".txt")
    assert SHARES_SOURCE["url"].startswith("https://www.census.gov/")


def test_real_profile_links_each_figure_to_where_it_can_be_checked(real_con, session):
    raw = json.loads(session.run("district_profile", {"district": "house:022"}))
    out = profile(real_con, raw, session)
    sections = {s["title"]: s for s in out["sections"]}
    acs = next(s for s in sections["People"]["sources"] if "Data Profiles" in s["name"])
    assert acs["url"] == PROFILES_PAGE
    assert {"text": "Table DP05 for House District 22", "url": DP05_HOUSE_22} in acs["links"]
    irs = next(s for s in sections["Programs in use"]["sources"] if s["name"].startswith("IRS"))
    assert irs["links"] == [
        {"text": "How KLEAR estimates district ranges", "url": "/methods.html#irs_soi_zip_2023"}
    ]
    # No source anywhere in the profile, or its spreadsheet, opens an API answer or a download.
    cited = [x["url"] for s in out["sections"] for x in s["sources"]]
    rows = list(csv.reader(io.StringIO(profile_csv(real_con, raw, session)[1:])))
    for url in cited + [r[10] for r in rows[1:]]:
        assert url.startswith("https://") and "api." not in url and not url.endswith((".csv", ".zip")), url


@pytest.fixture
def server(tmp_path):
    """A server on a store, started by each test that needs one."""
    started = []

    def start(store):
        srv = web.Server(("127.0.0.1", 0), partial(web.Handler, store=store))
        thread = threading.Thread(target=srv.serve_forever, daemon=True)
        thread.start()
        started.append((srv, thread))
        return srv.server_port

    yield start
    for srv, thread in started:
        srv.shutdown()
        srv.server_close()
        thread.join(timeout=5)


def get(port, path):
    con = HTTPConnection("127.0.0.1", port, timeout=10)
    con.request("GET", path)
    response = con.getresponse()
    body = response.read()
    con.close()
    return response.status, body, response.headers


def test_methods_page_is_served_with_its_assets(small_con, tmp_path, server):
    small_con.close()
    status, body, headers = get(server(tmp_path / "small.duckdb"), "/methods.html")
    assert status == 200 and headers["Content-Type"] == "text/html"
    assert b"How KLEAR estimates district figures" in body and b'id="irs_soi_zip_2023"' in body
    refs = re.findall(r'(?:src|href)="(/[^"#?]*)', body.decode("utf-8"))
    assert refs and set(refs) <= set(web.PUBLIC)
    index = (web.STATIC / "index.html").read_text(encoding="utf-8")
    assert 'href="/methods.html"' in index


def test_a_store_from_an_older_klear_waits_for_a_rebuild(tmp_path, monkeypatch, capsys, server):
    # A store written before sources had a page_url.
    store = tmp_path / "old.duckdb"
    schema = SCHEMA.read_text(encoding="utf-8")
    page_url = ",\n    page_url VARCHAR NOT NULL CHECK (length(page_url) > 0)"
    assert page_url in schema
    with duckdb.connect(str(store)) as con:
        con.execute(schema.replace(page_url, ""))
    port = server(store)
    assert get(port, "/api/profile?district=house:022")[0] == 503
    assert get(port, "/api/resolve?q=House%2022")[0] == 503
    status, body, _ = get(port, "/api/about")
    assert status == 503 and "older version of KLEAR" in json.loads(body)["error"]
    assert get(port, "/")[0] == 200

    def stop(srv):
        raise KeyboardInterrupt

    monkeypatch.setattr(ThreadingHTTPServer, "serve_forever", stop)
    web.main(["--port", "0", "--basic-only", "--store", str(store)])
    out = capsys.readouterr().out
    assert "lacks source.page_url" in out and "rebuilt with: uv run python -m klear.etl build" in out
