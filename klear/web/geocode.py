"""Finds the 2020 Census block of a street address with the U.S. Census Bureau geocoder, for district search and chat.

The address goes to the Census Bureau and nowhere else. KLEAR uses it only to find the block, then looks the
block up in its own store, so districts come from the same 2022 plan as everything else on the site. The address
is never stored or logged: the web server logs no requests, and nothing here writes it anywhere.
"""

import re

import httpx

from klear.text import MACRON, OKINA, OKINA_LOOKALIKES, hawaiian

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
_SUFFIX = (
    r"(?i:st|street|ave|avenue|rd|road|hwy|highway|blvd|boulevard|dr|drive|pl|place|ln|lane|way|loop|cir|circle"
    r"|ct|court|pkwy|parkway|ter|terrace|mall|sq|square|trl|trail|walk|row|plz|plaza)\.?"
)
_WORD = _OKINA + r"?[^\W\d_][\w" + _OKINA[1:-1] + r".'-]*"
_TOWN = r"(?!(?:House|Senate|District)\b)" + _OKINA + r"?[A-ZĀĒĪŌŪ][\w" + _OKINA[1:-1] + r"'-]*"
# A street address inside a sentence: house number, street words up to a street type, then any town, state and ZIP.
IN_TEXT = re.compile(
    r"(?<![\w-])(?!96[78]\d\d\b)\d{1,6}(?:-\d{1,5})?[a-z]?(?:\s+"
    + _WORD
    + r"){0,5}?\s+"
    + _SUFFIX
    + r"(?!\w)"
    r"(?:,?\s+" + _TOWN + r"(?:\s+" + _TOWN + r"){0,2})?"
    r"(?:,?\s+(?i:hi|hawai" + _OKINA + r"?i)\b)?(?:,?\s+96[78]\d\d\b)?"
)
# A number after one of these words is a district or ZIP code, not a house number.
NOT_HOUSE = re.compile(r"\b(?:district|house|senate|hd|sd|zip|code|no)\.?\s*$", re.IGNORECASE)
NOTES = {
    "off": "Street address lookup is turned off on this server. Try a place, ZIP code or district instead.",
    "failed": "The address lookup is unavailable right now. Try a place, ZIP code or district instead.",
    "none": "No Hawaiʻi address matched. Check the house number and street name, or try a place, ZIP code or "
    "district.",
}


class GeocoderError(RuntimeError):
    """The geocoder could not be reached or sent a reply that could not be read."""


def looks_like_address(text: str) -> bool:
    return bool(ADDRESS.match(text))


def _plain(word: str) -> str:
    return "".join(MACRON.get(c, c) for c in re.sub(_OKINA, "", word.casefold()))


def readable(matched: str, typed: str) -> str:
    """The geocoder's capitalized match in title case, with Hawaiian spelling from the typed address and the
    place-name rules, as in 310 W Kaʻahumanu Ave, Kahului, HI 96732."""
    spelled = {
        _plain(w): re.sub(_OKINA, OKINA, w)
        for w in re.findall(r"[\w" + _OKINA[1:-1] + r"]+", typed)
        if _plain(w) != w.casefold()
    }

    def word(m: re.Match) -> str:
        w = m.group(0)
        if w == "HI" or not w.isalpha():
            return w
        return spelled.get(_plain(w), w.capitalize())

    text = re.sub(r"[^\W_]+", word, matched)
    return hawaiian(re.sub(r"\b(HI),\s*(\d{5})\b", r"\1 \2", text))


def address_in(text: str) -> str | None:
    """The street address a question names, or None; a question that starts with an address is taken whole."""
    for m in IN_TEXT.finditer(text):
        if not NOT_HOUSE.search(text[: m.start()]):
            return m.group(0).strip()
    return text.strip() if looks_like_address(text) else None


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
