"""Street address search: the Census geocoder client and the /api/resolve path that uses it."""

import json
import threading
from functools import partial
from http.client import HTTPConnection
from urllib.parse import quote

import httpx
import pytest

from klear.text import OKINA, OKINA_LOOKALIKES
from klear.web import __main__ as web
from klear.web.geocode import NOTES, GeocoderError, find_blocks, looks_like_address

CAPITOL = "415 S BERETANIA ST, HONOLULU, HI, 96813"


def _reply(*matches):
    return {"result": {"addressMatches": list(matches)}}


def _match(address, geoid):
    return {
        "matchedAddress": address,
        "geographies": {"Census Blocks": [{"GEOID": geoid, "STATE": geoid[:2]}]},
    }


def _client(handler):
    return httpx.Client(transport=httpx.MockTransport(handler))


@pytest.mark.parametrize(
    ("text", "address"),
    [
        ("415 S Beretania St", True),
        ("94-275 Mokuola St, Waipahu", True),
        ("12B Main St", True),
        ("House 22", False),
        ("96744", False),
        ("96744 Kaneohe", False),
        ("Kāneʻohe", False),
        ("22", False),
    ],
)
def test_looks_like_address(text, address):
    assert looks_like_address(text) is address


@pytest.mark.parametrize("mark", [OKINA, *OKINA_LOOKALIKES, ""])
def test_street_names_may_start_with_any_okina(mark):
    assert looks_like_address(f"99-1849 {mark}Aiea Heights Dr")
    assert looks_like_address(f"91-1001 {mark}Ewa Beach Rd, {mark}Ewa Beach")
    assert not looks_like_address(f"96701 {mark}Aiea")


@pytest.mark.parametrize("mark", [OKINA, *OKINA_LOOKALIKES])
def test_find_blocks_reads_any_okina_in_the_state_name(mark):
    seen = []

    def handler(request):
        seen.append(dict(request.url.params))
        return httpx.Response(200, json=_reply())

    with _client(handler) as client:
        find_blocks(f"46-056 Kamehameha Hwy, Kāne{mark}ohe, Hawai{mark}i", client=client)
    assert seen[0]["address"] == f"46-056 Kamehameha Hwy, Kāne{mark}ohe, Hawai{mark}i"


def test_find_blocks_adds_the_state_and_keeps_hawaii_blocks():
    seen = []

    def handler(request):
        seen.append(dict(request.url.params))
        return httpx.Response(
            200, json=_reply(_match(CAPITOL, "150030039001001"), _match("415 S X ST, CA", "060290029012015"))
        )

    with _client(handler) as client:
        assert find_blocks("415 S Beretania St", client=client) == [("150030039001001", CAPITOL)]
        assert find_blocks("415 S Beretania St, Honolulu, HI 96813", client=client)
    assert [s["address"] for s in seen] == [
        "415 S Beretania St, HI",
        "415 S Beretania St, Honolulu, HI 96813",
    ]
    assert seen[0]["layers"] == "Census Blocks" and seen[0]["vintage"] == "Census2020_Current"


def test_find_blocks_with_no_match():
    with _client(lambda r: httpx.Response(200, json=_reply())) as client:
        assert find_blocks("1 Nowhere Rd", client=client) == []


@pytest.mark.parametrize(
    "respond",
    [
        lambda r: (_ for _ in ()).throw(httpx.ConnectError(f"cannot reach {r.url}")),
        lambda r: httpx.Response(500, text="busy"),
        lambda r: httpx.Response(200, text="<html>not json</html>"),
        lambda r: httpx.Response(200, json={"errors": ["bad"]}),
    ],
)
def test_find_blocks_failure_never_repeats_the_address(respond):
    with _client(respond) as client, pytest.raises(GeocoderError) as err:
        find_blocks("415 S Beretania St", client=client)
    assert "Beretania" not in str(err.value) and err.value.__cause__ is None


def _fake_geocoder(calls):
    def lookup(text):
        calls.append(text)
        if text.startswith("3 "):
            raise GeocoderError("Census geocoder failed: ConnectError")
        if text.startswith("1 "):
            return [("150030001001001", "1 TEST ST, HONOLULU, HI, 96744")]
        return []

    return lookup


@pytest.fixture
def server(small_con, tmp_path):
    small_con.close()
    calls = []

    def start(geocoder):
        handler = partial(web.Handler, store=tmp_path / "small.duckdb", geocoder=geocoder)
        srv = web.Server(("127.0.0.1", 0), handler)
        threading.Thread(target=srv.serve_forever, daemon=True).start()
        servers.append(srv)
        return srv.server_port

    servers = []
    yield start, calls
    for srv in servers:
        srv.shutdown()
        srv.server_close()


def resolve(port, text):
    con = HTTPConnection("127.0.0.1", port, timeout=10)
    con.request("GET", "/api/resolve?q=" + quote(text))
    response = con.getresponse()
    data = json.loads(response.read())
    con.close()
    return response.status, data


def test_address_search_finds_the_block_districts(server):
    start, calls = server
    port = start(_fake_geocoder(calls))
    status, data = resolve(port, "1 Test St")
    assert status == 200
    [m] = data["matches"]
    assert (m["kind"], m["name"], m["island"]) == ("address", "1 TEST ST, HONOLULU, HI, 96744", "Oʻahu")
    assert [o["geo"] for o in m["overlaps"]] == ["house:023", "senate:012", "county:15003"]
    assert calls == ["1 Test St"]


def test_address_search_says_why_nothing_matched(server):
    start, calls = server
    port = start(_fake_geocoder(calls))
    assert resolve(port, "2 Nowhere Rd")[1]["note"] == NOTES["none"]
    assert resolve(port, "3 Down St")[1]["note"] == NOTES["failed"]
    assert resolve(port, "Kailua")[1]["matches"] and calls == ["2 Nowhere Rd", "3 Down St"]


def test_address_search_off_says_so(server):
    start, _ = server
    port = start(None)
    data = resolve(port, "1 Test St")[1]
    assert data["matches"] == [] and data["note"] == NOTES["off"]
    assert resolve(port, "96744")[1]["matches"][0]["geo"] == "zcta:96744"


def test_startup_says_addresses_go_to_the_census(tmp_path, monkeypatch, capsys):
    monkeypatch.setattr(web.Server, "serve_forever", lambda self: None)
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    for flags, said in (([], True), (["--no-address-lookup"], False)):
        web.main(["--port", "0", "--basic-only", "--store", str(tmp_path / "none.duckdb"), *flags])
        assert ("U.S. Census Bureau geocoder" in capsys.readouterr().out) is said


def suggest(port, text):
    con = HTTPConnection("127.0.0.1", port, timeout=10)
    con.request("GET", "/api/suggest?q=" + quote(text))
    response = con.getresponse()
    data = json.loads(response.read())
    con.close()
    return response.status, data


def test_suggestions_come_from_the_store_and_never_the_geocoder(server):
    start, calls = server
    port = start(_fake_geocoder(calls))
    status, data = suggest(port, "wai")
    assert status == 200 and [s["label"] for s in data["suggestions"]] == ["Waikīkī", "Waikīkī Beach"]
    assert suggest(port, "1 Test St") == (200, {"suggestions": []})
    assert suggest(port, "") == (200, {"suggestions": []})
    assert suggest(port, "x" * 161)[0] == 400
    assert calls == []


def test_suggestions_without_a_store(tmp_path):
    srv = web.Server(("127.0.0.1", 0), partial(web.Handler, store=tmp_path / "missing.duckdb"))
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    try:
        assert suggest(srv.server_port, "wai")[0] == 503
    finally:
        srv.shutdown()
        srv.server_close()
