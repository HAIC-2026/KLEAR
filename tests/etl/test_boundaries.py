# Tests for district boundary download and export.
import json

import geopandas as gpd
import httpx
import pytest
from shapely.geometry import Polygon, box

from klear import config
from klear.etl import boundaries
from klear.etl.boundaries import download_districts, export_web_geojson
from klear.validation.boundaries import check_boundaries


def feature(i, chamber="house"):
    x = -158 + i * 0.1
    ring = [[x, 21], [x + 0.05, 21], [x + 0.05, 21.05], [x, 21.05], [x, 21]]
    props = {"objectid": i, f"{chamber}_id": i, f"{chamber}_name": f"State {chamber} District {i}"}
    return {"type": "Feature", "properties": props, "geometry": {"type": "Polygon", "coordinates": [ring]}}


def test_download_districts_pages_until_done(tmp_path, monkeypatch):
    monkeypatch.setattr(config, "RAW_DIR", tmp_path)
    monkeypatch.setattr(boundaries, "PAGE_SIZE", 2)
    calls = []

    def handler(request):
        off = int(request.url.params["resultOffset"])
        calls.append(off)
        feats = [feature(i) for i in range(1, 6)][off : off + 2]
        return httpx.Response(200, json={"type": "FeatureCollection", "features": feats})

    client = httpx.Client(transport=httpx.MockTransport(handler))
    gdf = download_districts("house", client=client)
    assert calls == [0, 2, 4]
    assert list(gdf["geo_id"]) == ["001", "002", "003", "004", "005"]
    assert gdf.crs.to_epsg() == 4326
    assert list(gdf.columns) == ["geo_id", "name", "geometry"]
    assert (tmp_path / "boundaries" / "house_offset0.geojson.meta.json").exists()


@pytest.mark.parametrize(
    "body",
    [
        {"error": {"code": 400, "message": "Invalid or missing input parameters."}},
        {"type": "FeatureCollection"},
    ],
)
def test_arcgis_error_page_raises_and_is_not_cached(tmp_path, monkeypatch, body):
    monkeypatch.setattr(config, "RAW_DIR", tmp_path)
    client = httpx.Client(transport=httpx.MockTransport(lambda r: httpx.Response(200, json=body)))
    with pytest.raises(boundaries.ArcGISError, match="ArcGIS returned an error"):
        download_districts("house", client=client)
    assert not list(tmp_path.rglob("*.geojson*"))


def test_download_districts_unknown_chamber():
    with pytest.raises(ValueError):
        download_districts("council")


def test_export_web_geojson_simplifies(tmp_path):
    pts = [(-158 + i * 0.001, 21 + (0.00005 if i % 2 else 0)) for i in range(100)]
    poly = Polygon(pts + [(-157.8, 21.2), (-158, 21.2)])
    gdf = gpd.GeoDataFrame({"geo_id": ["001"], "name": ["x"]}, geometry=[poly], crs="EPSG:4326")
    out = tmp_path / "web" / "house.geojson"
    export_web_geojson(gdf, out)
    back = gpd.read_file(out)
    assert len(back) == 1
    assert len(back.geometry[0].exterior.coords) < len(poly.exterior.coords)
    assert json.loads(out.read_text())["features"][0]["properties"]["geo_id"] == "001"


@pytest.mark.network
def test_live_download_counts_and_names():
    house = download_districts("house")
    senate = download_districts("senate")
    assert len(house) == 51 and len(senate) == 25
    assert house["geo_id"].iloc[0] == "001"


def grid_gdf(n, overlap_m=0.0, cell=0.01, width_m=None):
    # squares along a row near Honolulu, cell degrees wide
    geoms = [box(-158.0 + i * cell, 21.3, -158.0 + (i + 1) * cell, 21.3 + cell) for i in range(n)]
    return gpd.GeoDataFrame(
        {"geo_id": [f"{i + 1:03d}" for i in range(n)], "name": [f"D{i + 1}" for i in range(n)]},
        geometry=geoms,
        crs="EPSG:4326",
    )


def test_check_boundaries_passes_clean_tiling():
    r = check_boundaries(grid_gdf(25), "senate")
    assert r.passed and r.gate and r.n_mismatch == 0


def test_check_boundaries_flags_overlap():
    g = grid_gdf(25)
    # widen district 1 so it overlaps district 2 by about 2 km2
    g.loc[0, "geometry"] = box(-158.0, 21.3, -157.99 + 0.0185, 21.31)
    r = check_boundaries(g, "senate")
    assert not r.passed
    assert any("overlap" in d["check"] for d in r.details)


def test_check_boundaries_flags_missing_district():
    r = check_boundaries(grid_gdf(50), "house")
    assert not r.passed
    assert any(d["check"] == "count" for d in r.details)


def test_check_boundaries_flags_invalid_geometry():
    g = grid_gdf(25)
    from shapely.geometry import Polygon

    g.loc[3, "geometry"] = Polygon([(0, 0), (1, 1), (1, 0), (0, 1)])
    r = check_boundaries(g, "senate")
    assert not r.passed
    assert any(d["check"] == "valid" for d in r.details)


def test_check_boundaries_flags_noncontiguous_ids():
    g = grid_gdf(25)
    g.loc[24, "geo_id"] = "026"
    r = check_boundaries(g, "senate")
    assert not r.passed
    assert any(d["check"] == "ids" for d in r.details)


def test_check_boundaries_flags_union_area_mismatch():
    r = check_boundaries(grid_gdf(25), "senate", other=grid_gdf(25, cell=0.02))
    assert not r.passed
    assert any(d["check"] == "union_area" for d in r.details)


def test_check_boundaries_union_area_within_tolerance():
    assert check_boundaries(grid_gdf(25), "senate", other=grid_gdf(25)).passed


def test_check_boundaries_unknown_chamber():
    with pytest.raises(ValueError):
        check_boundaries(grid_gdf(25), "council")


@pytest.mark.network
def test_live_boundaries_pass():
    house = download_districts("house")
    senate = download_districts("senate")
    assert check_boundaries(house, "house", other=senate).passed
    assert check_boundaries(senate, "senate", other=house).passed
