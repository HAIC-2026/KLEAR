# Offline tests for paged ArcGIS feature queries.
import httpx
import pytest

from klear import config
from klear.etl import fetch as fetch_mod
from klear.etl.arcgis import ArcGISQueryError, query_features

LAYER = "https://example.org/arcgis/rest/services/X/FeatureServer/0"


@pytest.fixture(autouse=True)
def raw_dir(tmp_path, monkeypatch):
    monkeypatch.setattr(config, "RAW_DIR", tmp_path)
    monkeypatch.setattr(fetch_mod, "BACKOFF_S", 0.0)
    return tmp_path


def make_client(pages):
    calls = []

    def handler(request):
        calls.append(request)
        return httpx.Response(200, json=pages[len(calls) - 1])

    return httpx.Client(transport=httpx.MockTransport(handler)), calls


def feats(*ids):
    return [{"attributes": {"GEOID": i, "OBJECTID": i}} for i in ids]


def test_pages_until_no_transfer_limit(raw_dir):
    client, calls = make_client(
        [
            {"features": feats("a", "b"), "exceededTransferLimit": True},
            {"features": feats("c")},
        ]
    )
    rows = query_features(LAYER, "STATE='15'", ["GEOID"], "hcv", client=client)
    assert rows == [{"GEOID": i, "OBJECTID": i} for i in "abc"]
    assert len(calls) == 2
    assert calls[1].url.params["resultOffset"] == "2"
    assert all(c.url.params["orderByFields"] == "OBJECTID" for c in calls)
    assert all("OBJECTID" in c.url.params["outFields"] for c in calls)
    assert len(list((raw_dir / "hcv").glob("*/page_?.json"))) == 2


def test_error_json_raises(raw_dir):
    client, _ = make_client([{"error": {"code": 400, "message": "Invalid query"}}])
    with pytest.raises(ArcGISQueryError, match="Invalid query"):
        query_features(LAYER, "bad", ["GEOID"], "hcv", client=client)
    assert not list(raw_dir.rglob("page_*.json"))


def test_cached_pages_not_refetched(raw_dir):
    page = {"features": feats("a")}
    client, calls = make_client([page])
    query_features(LAYER, "1=1", ["GEOID"], "hcv", client=client)
    query_features(LAYER, "1=1", ["GEOID"], "hcv", client=client)
    assert len(calls) == 1


def test_changed_where_does_not_reuse_cache(raw_dir):
    client, calls = make_client([{"features": feats("a")}, {"features": feats("b")}])
    first = query_features(LAYER, "STATE='15'", ["GEOID"], "hcv", client=client)
    second = query_features(LAYER, "STATE='06'", ["GEOID"], "hcv", client=client)
    assert len(calls) == 2
    assert first != second


def test_duplicate_ids_raise(raw_dir):
    client, _ = make_client(
        [
            {"features": feats("a", "b"), "exceededTransferLimit": True},
            {"features": feats("b")},
        ]
    )
    with pytest.raises(ArcGISQueryError, match="duplicate OBJECTID"):
        query_features(LAYER, "1=1", ["GEOID"], "hcv", client=client)
