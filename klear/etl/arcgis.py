# Paged, cached attribute queries against ArcGIS FeatureServer layers.
import hashlib
import json
from pathlib import Path
from urllib.parse import urlencode

import httpx

from klear.etl.boundaries import ArcGISError, _check_page
from klear.etl.fetch import fetch

PAGE_SIZE = 1000


class ArcGISQueryError(RuntimeError):
    pass


def _validate(path: Path) -> None:
    try:
        _check_page(path)
    except ArcGISError as e:
        raise ArcGISQueryError(str(e)) from None


def query_features(
    layer_url: str,
    where: str,
    out_fields: list[str],
    name: str,
    *,
    order_by: str = "OBJECTID",
    client: httpx.Client | None = None,
) -> list[dict]:
    # return attributes of every matching feature in order_by order, which must be a unique id field
    # pages are cached as name/{query hash}/page_{n}.json and the id field is always requested
    fields = out_fields if order_by in out_fields else [*out_fields, order_by]
    key = hashlib.sha256(json.dumps([where, fields, order_by]).encode()).hexdigest()[:10]
    rows: list[dict] = []
    offset = 0
    page = 0
    while True:
        q = urlencode(
            {
                "where": where,
                "outFields": ",".join(fields),
                "orderByFields": order_by,
                "returnGeometry": "false",
                "f": "json",
                "resultOffset": offset,
                "resultRecordCount": PAGE_SIZE,
            }
        )
        path = fetch(
            f"{layer_url}/query?{q}", f"{name}/{key}/page_{page}.json", client=client, validate=_validate
        )
        data = json.loads(path.read_text())
        features = data["features"]
        rows.extend(f["attributes"] for f in features)
        if not data.get("exceededTransferLimit") or not features:
            ids = [r.get(order_by) for r in rows]
            if len(set(ids)) != len(ids):
                raise ArcGISQueryError(f"{name}: duplicate {order_by} values across pages")
            return rows
        offset += len(features)
        page += 1
