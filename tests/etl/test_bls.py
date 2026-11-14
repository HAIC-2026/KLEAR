# Tests for the BLS county unemployment loader and its check, on a small API reply.
import json

import httpx
import pytest

from klear import config
from klear.etl import bls
from klear.etl import fetch as fetch_mod
from klear.store.db import add_facts, add_indicators, add_source, open_store
from klear.validation.bls import check_bls

# September and August 2026 by area: unemployed, labor force, rate; October 2025 was not published
AREAS = {
    "ST1500000000000": ((22507, 700257, 3.2), (20427, 698204, 2.9)),
    "CN1500100000000": ((3307, 93661, 3.5), (3242, 94772, 3.4)),
    "CN1500300000000": ((14636, 484553, 3.0), (13734, 479733, 2.9)),
    "CN1500700000000": ((1953, 38646, 5.1), (982, 38896, 2.5)),
    "CN1500900000000": ((2612, 83398, 3.1), (2469, 84803, 2.9)),
}
PRELIM = [{"code": "P", "text": "Preliminary."}]


def _point(month, name, value, footnotes=None):
    return {
        "year": "2026",
        "period": month,
        "periodName": name,
        "value": value,
        "footnotes": footnotes or [{}],
    }


def reply(areas=None, status="REQUEST_SUCCEEDED", drop=()):
    series = []
    for area, (sep, aug) in (areas or AREAS).items():
        for k, m in enumerate(("04", "06", "03")):
            sid = f"LAU{area}{m}"
            if sid in drop:
                continue
            data = [
                _point("M09", "September", str(sep[k]), PRELIM),
                _point("M08", "August", str(aug[k])),
                {
                    "year": "2025",
                    "period": "M10",
                    "periodName": "October",
                    "value": "-",
                    "footnotes": [{"code": "X"}],
                },
                {"year": "2025", "period": "M13", "periodName": "Annual", "value": "1"},
            ]
            series.append({"seriesID": sid, "data": data})
    return {"status": status, "message": [], "Results": {"series": series}}


@pytest.fixture
def raw(tmp_path, monkeypatch):
    monkeypatch.setattr(config, "RAW_DIR", tmp_path)
    monkeypatch.setattr(fetch_mod, "BACKOFF_S", 0.0)
    return tmp_path


@pytest.fixture
def serve(raw, monkeypatch):
    # answer every request with handler(request), recording each request's method and JSON body
    seen = []

    def use(handler):
        def wrapped(request):
            seen.append((request.method, json.loads(request.content)))
            return handler(request)

        real = httpx.Client
        monkeypatch.setattr(
            httpx, "Client", lambda *a, **kw: real(*a, **(kw | {"transport": httpx.MockTransport(wrapped)}))
        )
        return seen

    return use


def _write(path, body):
    path.write_text(json.dumps(body), encoding="utf-8")
    return path


def test_series_ids_follow_the_laus_pattern():
    assert "LAUCN150030000000003" in bls.SERIES and "LAUST150000000000006" in bls.SERIES
    assert bls.SERIES["LAUCN150030000000003"] == ("county", "15003", "bls_unemployment_rate")
    assert not any("CN15005" in s for s in bls.SERIES)
    assert len(bls.SERIES) == 15


def test_parse_skips_unpublished_months_and_annual_averages(tmp_path):
    df = bls.parse_laus(_write(tmp_path / "r.json", reply()))
    assert len(df) == 15 * 2
    assert set(zip(df.year, df.month)) == {(2026, 9), (2026, 8)}
    sep = df[df.month == 9]
    assert sep.preliminary.all() and not df[df.month == 8].preliminary.any()


def test_latest_month_is_the_newest_every_series_has(tmp_path):
    body = reply()
    body["Results"]["series"][0]["data"] = body["Results"]["series"][0]["data"][1:]
    rows = bls.latest(bls.parse_laus(_write(tmp_path / "r.json", body)))
    assert set(rows.month) == {8} and bls.vintage(rows) == "August 2026"
    rows = bls.latest(bls.parse_laus(_write(tmp_path / "s.json", reply())))
    assert bls.vintage(rows) == "September 2026 (preliminary)"


def test_facts_are_county_level_context(tmp_path):
    rows = bls.latest(bls.parse_laus(_write(tmp_path / "r.json", reply())))
    facts = bls.laus_facts(rows)
    assert set(facts.tier) == {"C"} and set(facts.geo_type) == {"county", "state"}
    assert set(facts.moe_status) == {"not_applicable"} and facts.moe.isna().all()
    rate = facts[(facts.geo_id == "15003") & (facts.indicator_id == "bls_unemployment_rate")].iloc[0]
    assert rate.value == 3.0 and rate.table_id == "LAUCN150030000000003"
    labels = bls.laus_indicator_rows().set_index("indicator_id").label
    assert all("county-level" in label for label in labels)


def test_county_level_facts_load_into_the_store_only_for_counties_and_state(tmp_path):
    con = open_store(tmp_path / "s.duckdb")
    con.execute(
        "INSERT INTO geography VALUES ('state', '15', 'Hawaii', NULL), ('house', '022', 'House 22', NULL)"
    )
    con.execute(
        "INSERT INTO geography SELECT 'county', unnest(['15001', '15003', '15007', '15009']), 'c', NULL"
    )
    add_source(con, bls.SOURCE_ID, bls.NAME, bls.PUBLISHER, bls.API_URL, "2026", "pd", page_url=bls.PAGE_URL)
    add_indicators(con, bls.laus_indicator_rows())
    facts = bls.laus_facts(bls.latest(bls.parse_laus(_write(tmp_path / "r.json", reply()))))
    add_facts(con, facts)
    assert con.execute("SELECT count(*) FROM fact WHERE tier = 'C'").fetchone()[0] == 15
    district = facts.head(1).assign(geo_type="house", geo_id="022")
    with pytest.raises(ValueError, match="tier C outside"):
        add_facts(con, district)
    con.close()


@pytest.mark.parametrize(
    ("body", "message"),
    [
        (reply(status="REQUEST_NOT_PROCESSED"), "did not answer"),
        (reply(drop=("LAUCN150070000000003",)), "no data for LAUCN150070000000003"),
    ],
)
def test_failed_replies_are_not_cached(raw, serve, body, message):
    serve(lambda r: httpx.Response(200, json=body))
    with pytest.raises(bls.Unavailable, match=message):
        bls.fetch_laus()
    assert not (raw / bls.RAW_NAME).exists()


def test_fetch_posts_every_series_once_and_records_the_request(serve):
    seen = serve(lambda r: httpx.Response(200, json=reply()))
    path = bls.fetch_laus()
    bls.fetch_laus()
    assert seen == [("POST", {"seriesid": list(bls.SERIES)})]
    meta = json.loads(path.with_name(path.name + ".meta.json").read_text(encoding="utf-8"))
    assert meta["request_json"] == {"seriesid": list(bls.SERIES)} and meta["url"] == bls.API_URL


def test_check_passes_on_consistent_figures(tmp_path):
    rows = bls.latest(bls.parse_laus(_write(tmp_path / "r.json", reply())))
    result = check_bls(rows, bls.vintage(rows))
    assert result.passed and result.n_compared == 15, result.details
    assert "Honolulu County 3.0%" in " ".join(result.notes)


def test_check_fails_on_a_rate_that_disagrees_with_its_counts(tmp_path):
    areas = AREAS | {"CN1500300000000": ((14636, 484553, 4.0), (13734, 479733, 2.9))}
    rows = bls.latest(bls.parse_laus(_write(tmp_path / "r.json", reply(areas))))
    result = check_bls(rows, bls.vintage(rows))
    assert not result.passed and result.details[0]["check"] == "rate"


def test_check_fails_when_counties_do_not_add_to_the_state(tmp_path):
    areas = AREAS | {"ST1500000000000": ((30000, 900000, 3.3), (20427, 698204, 2.9))}
    rows = bls.latest(bls.parse_laus(_write(tmp_path / "r.json", reply(areas))))
    result = check_bls(rows, bls.vintage(rows))
    assert {d["check"] for d in result.details} == {"county_sum"}


def test_check_fails_on_a_missing_series(tmp_path):
    rows = bls.latest(bls.parse_laus(_write(tmp_path / "r.json", reply())))
    rows = rows[~((rows.geo_id == "15009") & (rows.indicator_id == "bls_unemployed"))]
    result = check_bls(rows, "September 2026")
    assert not result.passed and {"check": "missing", "geo_type": "county", "geo_id": "15009",
                                   "indicator": "bls_unemployed"} in result.details  # fmt: skip
