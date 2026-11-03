# Downloads official 2022 House and Senate district polygons from the Hawaii Statewide GIS ArcGIS server.
import json
from pathlib import Path

import geopandas as gpd
import httpx
import pandas as pd

from klear.etl.fetch import fetch
from klear.text import hawaiian

SERVICE = "https://geodata.hawaii.gov/arcgis/rest/services/AdminBnd/MapServer"
LAYERS = {"house": f"{SERVICE}/33", "senate": f"{SERVICE}/34"}
# Step 0: layer fields. District number is house_id / senate_id (smallint), name is house_name / senate_name.
ID_FIELDS = {"house": "house_id", "senate": "senate_id"}
NAME_FIELDS = {"house": "house_name", "senate": "senate_name"}
PAGE_SIZE = 1000
SIMPLIFY_DEG = 0.0002


def _page_url(chamber: str, offset: int) -> str:
    q = (
        "where=1%3D1&outFields=*&outSR=4326&f=geojson&orderByFields=objectid"
        f"&resultOffset={offset}&resultRecordCount={PAGE_SIZE}"
    )
    return f"{LAYERS[chamber]}/query?{q}"


class ArcGISError(RuntimeError):
    pass


def _check_page(path: Path) -> None:
    try:
        data = json.loads(path.read_text())
    except ValueError:
        raise ArcGISError("ArcGIS returned a page that is not JSON") from None
    if not isinstance(data, dict) or "error" in data or not isinstance(data.get("features"), list):
        err = data.get("error", {}) if isinstance(data, dict) else {}
        raise ArcGISError(
            f"ArcGIS returned an error instead of features: code {err.get('code')}, {err.get('message')!r}"
        )


def download_districts(chamber: str, client: httpx.Client | None = None) -> gpd.GeoDataFrame:
    if chamber not in LAYERS:
        raise ValueError(f"unknown chamber {chamber!r}")
    frames = []
    offset = 0
    while True:
        path = fetch(
            _page_url(chamber, offset),
            f"boundaries/{chamber}_offset{offset}.geojson",
            client=client,
            validate=_check_page,
        )
        n = len(json.loads(path.read_text())["features"])
        if n:
            frames.append(gpd.read_file(path))
        if n < PAGE_SIZE:
            break
        offset += n
    raw = gpd.GeoDataFrame(pd.concat(frames, ignore_index=True), crs="EPSG:4326")
    out = gpd.GeoDataFrame(
        {
            "geo_id": raw[ID_FIELDS[chamber]].astype(int).map("{:03d}".format),
            "name": raw[NAME_FIELDS[chamber]].map(hawaiian),
        },
        geometry=raw.geometry,
        crs="EPSG:4326",
    )
    return out.sort_values("geo_id").reset_index(drop=True)


def export_web_geojson(gdf: gpd.GeoDataFrame, path: Path) -> None:
    out = gdf.copy()
    out["geometry"] = out.geometry.simplify(SIMPLIFY_DEG, preserve_topology=True)
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    out.to_file(path, driver="GeoJSON")
