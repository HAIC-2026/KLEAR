# Tests for the Census API client.
import httpx
import pandas as pd
import pytest

from klear.etl import census_api
from klear.etl.census_api import CensusKeyError, get_acs, get_variable_labels

KEY = "SECRETKEY123"


@pytest.fixture
def setup(monkeypatch):
    monkeypatch.setenv("CENSUS_API_KEY", KEY)
    monkeypatch.setattr(census_api, "KEY_WAIT_S", 0.0)
    monkeypatch.setattr(census_api, "BACKOFF_S", 0.0)


def make_client(handler):
    calls = []

    def wrapped(request):
        calls.append(request)
        return handler(request)

    return httpx.Client(transport=httpx.MockTransport(wrapped)), calls


def echo_handler(value="100", moe="10", district="001"):
    def handler(request):
        get = request.url.params["get"].split(",")
        header = get + ["state", "state legislative district (lower chamber)"]
        row = ["X"] + [value if v.endswith("E") else moe for v in get[1:]] + ["15", district]
        return httpx.Response(200, json=[header, row])

    return handler


def test_url_encodes_spaces_as_percent20(setup):
    client, calls = make_client(echo_handler())
    get_acs("acs/acs5", ["B01003_001"], "house", client=client)
    url = str(calls[0].url)
    assert "state%20legislative%20district%20(lower%20chamber)" in url
    assert "+" not in url


def test_invalid_key_html_raises(setup):
    client, calls = make_client(lambda r: httpx.Response(200, text="<html><body>Invalid Key</body></html>"))
    with pytest.raises(CensusKeyError) as e:
        get_acs("acs/acs5", ["B01003_001"], "house", client=client)
    assert KEY not in str(e.value)
    assert len(calls) == 3


def test_invalid_key_redirect_raises(setup):
    client, calls = make_client(
        lambda r: httpx.Response(302, headers={"location": "https://api.census.gov/data/invalid_key.html"})
    )
    with pytest.raises(CensusKeyError):
        get_acs("acs/acs5", ["B01003_001"], "house", client=client)
    assert len(calls) == 3


def test_chunks_over_48_variables(setup):
    client, calls = make_client(echo_handler())
    names = [f"B01001_{i:03d}" for i in range(1, 61)]
    df = get_acs("acs/acs5", names, "house", client=client)
    assert len(calls) == 3
    assert len(df) == 60
    assert set(df["variable"]) == set(names)
    for c in calls:
        assert len(c.url.params["get"].split(",")) <= 50


def test_house_geo_id_is_3_digits(setup):
    client, _ = make_client(echo_handler(district="007"))
    df = get_acs("acs/acs5", ["B01003_001"], "house", client=client)
    assert df["geo_id"].tolist() == ["007"]
    assert df["geo_type"].tolist() == ["house"]


def test_sentinels_become_null(setup):
    client, _ = make_client(echo_handler(value="-666666666", moe="-666666666"))
    df = get_acs("acs/acs5", ["B01003_001"], "house", client=client)
    row = df.iloc[0]
    assert pd.isna(row["estimate"])
    assert row["estimate_status"] == "suppressed"
    assert pd.isna(row["moe"])
    assert row["moe_status"] == "not_computable"


def test_retries_on_5xx(setup):
    state = {"n": 0}

    def handler(request):
        state["n"] += 1
        if state["n"] == 1:
            return httpx.Response(503)
        return echo_handler()(request)

    client, calls = make_client(handler)
    df = get_acs("acs/acs5", ["B01003_001"], "house", client=client)
    assert len(calls) == 2 and len(df) == 1


def test_variable_labels(setup):
    client, _ = make_client(
        lambda r: httpx.Response(200, json={"variables": {"B01003_001E": {"label": "Total"}}})
    )
    assert get_variable_labels("acs/acs5", client=client) == {"B01003_001E": "Total"}


def test_profile_percent_variable(setup):
    client, calls = make_client(echo_handler(value="12.5", moe="1.5"))
    df = get_acs("acs/acs5/profile", ["DP05_0005P"], "house", client=client)
    assert calls[0].url.params["get"] == "NAME,DP05_0005PE,DP05_0005PM"
    row = df.iloc[0]
    assert row["variable"] == "DP05_0005P" and row["estimate"] == 12.5 and row["moe"] == 1.5


@pytest.mark.parametrize("status", [400, 404, 500])
def test_http_error_masks_key(setup, status):
    client, _ = make_client(lambda r: httpx.Response(status, text="boom"))
    with pytest.raises(httpx.HTTPStatusError) as e:
        get_acs("acs/acs5", ["B01003_001"], "house", client=client)
    assert KEY not in str(e.value) and "key=***" in str(e.value)


@pytest.mark.network
def test_live_profile_percent_variable():
    df = get_acs("acs/acs5/profile", ["DP05_0005P"], "house")
    assert pd.notna(df[df["geo_id"] == "001"].iloc[0]["estimate"])


@pytest.mark.network
def test_live_house_district_1_population():
    df = get_acs("acs/acs5", ["B01003_001"], "house")
    assert len(df) == 51
    row = df[df["geo_id"] == "001"].iloc[0]
    assert row["estimate"] == 26368
    assert row["moe"] == 1633


@pytest.mark.network
def test_live_tract_count():
    df = get_acs("acs/acs5", ["B01003_001"], "tract")
    assert len(df) > 400
    assert df["geo_id"].str.len().eq(11).all()
