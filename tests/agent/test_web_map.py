"""The district map data matches the store and is served with the page."""

import json

from klear.config import ROOT
from tests.agent.test_web import endpoint, request  # noqa: F401

MAP = ROOT / "klear" / "web" / "static" / "districts.json"


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
