"""Hawaiian place-name spelling with ʻokina and kahakō."""

import re
import unicodedata

OKINA = "ʻ"
# What people type for the ʻokina (U+02BB): apostrophes and single quotes ' ‘ ’ ‛ ＇, accents ` ´ ΄, primes
# ′ ‵, and modifier letters ʹ ʼ ʽ ʿ ˈ ˋ. Search and spelling read each one as an ʻokina.
OKINA_LOOKALIKES = "'‘’‛＇`´΄′‵ʹʼʽʿˈˋ"
NAMES = ("Hawaiʻi", "Kauaʻi", "Oʻahu", "Lānaʻi", "Molokaʻi", "Kahoʻolawe", "Niʻihau")
PLACES = (
    "Kāneʻohe",
    "Waiʻanae",
    "Waimānalo",
    "Hawaiʻi Kai",
    "ʻEwa Beach",
    "Kailua-Kona",
    "Hilo",
    "Līhuʻe",
    "Kahului",
    "Lahaina",
)
# Official organization names kept exactly as written, taken from the site table.
ORG_NAMES = (
    "COUNTY OF HAWAII",
    "HO'OLA LAHUI HAWAI'I",
    "HO'OLA LAHUI-HAWAII",
    "KANEOHE ELDERLY",
    "KAUAI COMMUNITY HEALTH CTR-WEST",
    "SENIOR RESIDENCE AT KANEOHE B",
    "WAIANAE COAST COMPREHENSIVE HEALTH CTR",
    "WAIMANALO APTS",
    "Waianae Coast Comprehensive Health Center",
    "Waianae Elementary School",
    "Waianae High School School-based Health Center",
    "Waianae Intermediate School School-based Health Center",
    "Waianae Vision Care",
    "Waimanalo Elementary and Intermediate School",
    "Waimanalo Health Center",
)
EMPHASIS = re.compile("[*_\u0060~]")
# Default_Ignorable_Code_Point ranges outside category Cf, unassigned ones included.
IGNORABLE = re.compile(
    "[\u034f\u115f\u1160\u17b4\u17b5\u180b-\u180f\u200b-\u200f\u202a-\u202e\u2060-\u206f\u3164"
    "\ufe00-\ufe0f\ufeff\uffa0\ufff0-\ufff8\U0001bca0-\U0001bca3\U0001d173-\U0001d17a\U000e0000-\U000e0fff]"
)
MACRON = {"ā": "a", "ē": "e", "ī": "i", "ō": "o", "ū": "u"}


def _pattern(name: str) -> str:
    out = []
    for ch in name:
        if ch == OKINA:
            out.append(f"[{OKINA}{re.escape(OKINA_LOOKALIKES)}]?")
        elif ch in MACRON:
            out.append(f"[{ch}{MACRON[ch]}]")
        else:
            out.append(re.escape(ch))
    return r"(?<![\w])" + "".join(out) + r"(?!\w)"


_RULES = [(re.compile(_pattern(n)), n) for n in (*NAMES, *PLACES)]
_ANY_CASE = [(re.compile(_pattern(n), re.IGNORECASE), n) for n in (*NAMES, *PLACES)]
# Web addresses, with or without a scheme, which cannot carry the ʻokina.
LINK = r"https?://[^\s)\]>]+|(?<![\w@.-])(?:[\w-]+\.)+(?:gov|org|com|edu|net|us)(?![\w-])(?:/[^\s)\]>]*)?"
_ORG = re.compile(
    r"(?<!\w)(?:" + "|".join(re.escape(n) for n in sorted(ORG_NAMES, key=len, reverse=True)) + r")(?!\w)"
)
_KEEP = re.compile(f"(?i:{LINK})|{_ORG.pattern}")


def _plain_chunks(s: str) -> list[tuple[bool, str]]:
    """Text split into (kept as written, chunk): organization names and web addresses are kept."""
    out, pos = [], 0
    for m in _KEEP.finditer(s):
        out += [(False, s[pos : m.start()]), (True, m.group())]
        pos = m.end()
    return [*out, (False, s[pos:])]


def hawaiian(s: str) -> str:
    """Place names spelled with ʻokina and kahakō, organization names and web addresses kept."""
    s = unicodedata.normalize("NFC", s)
    out = []
    for is_org, chunk in _plain_chunks(s):
        if not is_org:
            for rx, name in _RULES:
                chunk = rx.sub(name, chunk)
        out.append(chunk)
    return "".join(out)


def strip_ignorable(s: str) -> str:
    """Remove format (Cf) and default-ignorable code points."""
    return IGNORABLE.sub("", "".join(c for c in s if unicodedata.category(c) != "Cf"))


def misspellings(s: str) -> list[str]:
    """Place names in any case whose spelling differs from the correct form, organization names excepted."""
    found = []
    s = unicodedata.normalize("NFC", EMPHASIS.sub("", strip_ignorable(s)))
    for is_org, chunk in _plain_chunks(s):
        if is_org:
            continue
        for rx, name in _ANY_CASE:
            found += [m.group() for m in rx.finditer(chunk) if m.group().casefold() != name.casefold()]
    return found
