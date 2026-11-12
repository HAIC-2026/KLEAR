# Census API client for ACS estimates and margins of error.
import time
from urllib.parse import quote

import httpx
import pandas as pd

from klear import config
from klear.etl.acs_values import parse_estimate, parse_moe
from klear.etl.fetch import RETRY_STATUS, USER_AGENT, mask_key

BASE_URL = "https://api.census.gov/data"
GEOS = {
    "state": ("state:15", None),
    "county": ("county:*", "state:15"),
    "senate": ("state legislative district (upper chamber):*", "state:15"),
    "house": ("state legislative district (lower chamber):*", "state:15"),
    "tract": ("tract:*", "state:15"),
}
# 2020 Census 118th Congressional District Summary File, the 2020 tabulation of the 2022 districts
SLD_POP_DATASET = "dec/cd118"
CHUNK = 24
ATTEMPTS = 3
BACKOFF_S = 1.0
KEY_ATTEMPTS = 3
KEY_WAIT_S = 5.0
COLUMNS = ["geo_type", "geo_id", "variable", "estimate", "estimate_status", "moe", "moe_status"]


class CensusKeyError(RuntimeError):
    pass


def _url(dataset: str, year: int, params: list[tuple[str, str]]) -> str:
    query = "&".join(f"{k}={quote(v, safe=':*(),')}" for k, v in params)
    return f"{BASE_URL}/{year}/{dataset}?{query}"


def _is_key_error(resp: httpx.Response) -> bool:
    return "invalid key" in resp.text.lower() or "invalid_key" in resp.headers.get("location", "")


def _get_json(client: httpx.Client, url: str):
    key_tries = 0
    attempt = 0
    while True:
        try:
            resp = client.get(url, headers={"User-Agent": USER_AGENT})
        except httpx.TransportError as e:
            if attempt >= ATTEMPTS - 1:
                raise httpx.TransportError(mask_key(str(e))) from None
            time.sleep(BACKOFF_S * 2**attempt)
            attempt += 1
            continue
        if _is_key_error(resp):
            key_tries += 1
            if key_tries >= KEY_ATTEMPTS:
                raise CensusKeyError("Census API rejected the key (Invalid Key); key=***")
            time.sleep(KEY_WAIT_S)
            continue
        if resp.status_code in RETRY_STATUS and attempt < ATTEMPTS - 1:
            time.sleep(BACKOFF_S * 2**attempt)
            attempt += 1
            continue
        try:
            resp.raise_for_status()
        except httpx.HTTPStatusError as e:
            raise httpx.HTTPStatusError(mask_key(str(e)), request=e.request, response=e.response) from None
        try:
            return resp.json()
        except ValueError:
            raise RuntimeError(f"Census API returned non-JSON (HTTP {resp.status_code})") from None


def _geo_id(geo_type: str, row: dict) -> str:
    if geo_type == "state":
        return row["state"]
    if geo_type == "county":
        return row["state"] + row["county"]
    if geo_type == "tract":
        return row["state"] + row["county"] + row["tract"]
    chamber = "upper" if geo_type == "senate" else "lower"
    return row[f"state legislative district ({chamber} chamber)"]


def _keyed_url(client, dataset: str, year: int, params: list[tuple[str, str]]) -> str:
    # load the key only when the client cannot serve the request from its cache
    masked = _url(dataset, year, params + [("key", "***")])
    serves = getattr(client, "serves", None)
    if callable(serves) and serves(masked):
        return masked
    return _url(dataset, year, params + [("key", config.load_secret("CENSUS_API_KEY"))])


def _fetch_chunk(client, dataset, year, names, geo_type) -> list[dict]:
    for_, in_ = GEOS[geo_type]
    get = ",".join(["NAME"] + [f"{n}{s}" for n in names for s in ("E", "M")])
    params = [("get", get), ("for", for_)]
    if in_:
        params.append(("in", in_))
    data = _get_json(client, _keyed_url(client, dataset, year, params))
    header, rows = data[0], data[1:]
    return [dict(zip(header, r, strict=True)) for r in rows]


def get_acs(
    dataset: str,
    variables: list[str],
    geo_type: str,
    year: int = 2024,
    client: httpx.Client | None = None,
) -> pd.DataFrame:
    # base names ending in P (profile percents) request PE and PM like any other base name
    if geo_type not in GEOS:
        raise ValueError(f"unknown geo_type: {geo_type}")
    own = client is None
    client = client or httpx.Client(timeout=60.0)
    try:
        out = []
        for i in range(0, len(variables), CHUNK):
            names = variables[i : i + CHUNK]
            for row in _fetch_chunk(client, dataset, year, names, geo_type):
                geo_id = _geo_id(geo_type, row)
                for n in names:
                    est, est_status = parse_estimate(row[f"{n}E"])
                    moe, moe_status = parse_moe(row[f"{n}M"])
                    out.append((geo_type, geo_id, n, est, est_status, moe, moe_status))
    finally:
        if own:
            client.close()
    return pd.DataFrame(out, columns=COLUMNS)


def get_variable_labels(dataset: str, year: int = 2024, client: httpx.Client | None = None) -> dict[str, str]:
    url = f"{BASE_URL}/{year}/{dataset}/variables.json"
    own = client is None
    client = client or httpx.Client(timeout=60.0)
    try:
        data = _get_json(client, url)
    finally:
        if own:
            client.close()
    return {k: v.get("label", "") for k, v in data["variables"].items()}


def get_district_pop(
    chamber: str, dataset: str = SLD_POP_DATASET, year: int = 2020, client: httpx.Client | None = None
) -> pd.DataFrame:
    # published 2020 total population (P1_001N) of every House or Senate district, with the district name
    for_, in_ = GEOS[chamber]
    params = [("get", "NAME,P1_001N"), ("for", for_), ("in", in_)]
    own = client is None
    client = client or httpx.Client(timeout=60.0)
    try:
        data = _get_json(client, _keyed_url(client, dataset, year, params))
    finally:
        if own:
            client.close()
    header, rows = data[0], data[1:]
    recs = [dict(zip(header, r, strict=True)) for r in rows]
    return pd.DataFrame(
        {
            "chamber": chamber,
            "geo_id": [_geo_id(chamber, r) for r in recs],
            "name": [r["NAME"] for r in recs],
            "pop": [int(r["P1_001N"]) for r in recs],
        },
        columns=["chamber", "geo_id", "name", "pop"],
    )


def get_pl_blocks(county: str, year: int = 2020, client: httpx.Client | None = None) -> pd.DataFrame:
    # 2020 redistricting total population (P1_001N) for every block in one Hawaii county
    params = [("get", "P1_001N"), ("for", "block:*"), ("in", "state:15"), ("in", f"county:{county}")]
    own = client is None
    client = client or httpx.Client(timeout=120.0)
    try:
        data = _get_json(client, _keyed_url(client, "dec/pl", year, params))
    finally:
        if own:
            client.close()
    header, rows = data[0], data[1:]
    recs = [dict(zip(header, r, strict=True)) for r in rows]
    return pd.DataFrame(
        {
            "block": [r["state"] + r["county"] + r["tract"] + r["block"] for r in recs],
            "pop2020": [int(r["P1_001N"]) for r in recs],
        }
    )
