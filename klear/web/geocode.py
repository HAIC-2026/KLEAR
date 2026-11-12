"""Finds the 2020 Census block of a street address with the U.S. Census Bureau geocoder, for district search.

The address goes to the Census Bureau and nowhere else. KLEAR uses it only to find the block, then looks the
block up in its own store, so districts come from the same 2022 plan as everything else on the site. The address
is never stored or logged: the web server logs no requests, and nothing here writes it anywhere.
"""

import re

import httpx

from klear.text import OKINA, OKINA_LOOKALIKES

_OKINA = f"[{OKINA}{re.escape(OKINA_LOOKALIKES)}]"
URL = "https://geocoding.geo.census.gov/geocoder/geographies/onelineaddress"
# Census 2020 blocks stay fixed for the decade, so the block layer of the 2020 vintage matches the block table.
PARAMS = {
    "benchmark": "Public_AR_Current",
    "vintage": "Census2020_Current",
    "layers": "Census Blocks",
    "format": "json",
}
TIMEOUT = 8.0
MAX_MATCHES = 3
# A house number, as in 415 or 94-275, then a street name, which may start with an ʻokina typed as any of its
# lookalikes (as in ʻAiea Heights Dr). A leading Hawaiʻi ZIP code is not a house number.
ADDRESS = re.compile(
    r"^\s*(?!96[78]\d\d\b)\d{1,6}(?:-\d{1,5})?[a-z]?\s+" + _OKINA + r"?[^\W\d_]", re.IGNORECASE
)
# Without a state the geocoder rarely finds an address, so Hawaiʻi is added unless one is given.
STATE = re.compile(r"\b(?:hi|hawai" + _OKINA + r"?i|96[78]\d\d)\b", re.IGNORECASE)


class GeocoderError(RuntimeError):
    """The geocoder could not be reached or sent a reply that could not be read."""


def looks_like_address(text: str) -> bool:
    return bool(ADDRESS.match(text))


def find_blocks(text: str, client: httpx.Client | None = None) -> list[tuple[str, str]]:
    """(block GEOID, matched address) for each Hawaiʻi match, best first; empty when nothing matched."""
    address = text.strip() if STATE.search(text) else f"{text.strip()}, HI"
    try:
        if client is None:
            with httpx.Client(timeout=TIMEOUT) as own:
                r = own.get(URL, params={**PARAMS, "address": address})
        else:
            r = client.get(URL, params={**PARAMS, "address": address}, timeout=TIMEOUT)
        r.raise_for_status()
        matches = r.json()["result"]["addressMatches"]
    except (httpx.HTTPError, ValueError, KeyError, TypeError) as e:
        # Name only the kind of failure: the request URL would carry the address.
        raise GeocoderError(f"Census geocoder failed: {type(e).__name__}") from None
    out = []
    for m in matches if isinstance(matches, list) else []:
        blocks = [b for layer in (m.get("geographies") or {}).values() for b in layer if isinstance(b, dict)]
        block = next(
            (str(b.get("GEOID", "")) for b in blocks if str(b.get("GEOID", "")).startswith("15")), ""
        )
        if len(block) == 15 and (block, m.get("matchedAddress")) not in out:
            out.append((block, str(m.get("matchedAddress") or text.strip())))
    return out[:MAX_MATCHES]
