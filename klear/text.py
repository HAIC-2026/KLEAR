# Hawaiian place-name spelling with ʻokina and kahakō.
import re
import unicodedata

OKINA = "ʻ"
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
# Marks people type for the ʻokina: apostrophe, quotation marks, grave accent and modifier apostrophe.
LOOKALIKES = "'‘’`ʼ"
VOWELS = "aeiouāēīōūAEIOUĀĒĪŌŪ"
# A word, with an ʻokina or a lookalike joined in only where it stands before a vowel, as an ʻokina does.
WORD = re.compile(
    f"(?:[{OKINA}{LOOKALIKES}](?=[{VOWELS}]))?[^\\W\\d_]+(?:[{OKINA}{LOOKALIKES}](?=[{VOWELS}])[^\\W\\d_]+)*"
)


def _pattern(name: str) -> str:
    out = []
    for ch in name:
        if ch == OKINA:
            out.append("[ʻ'’‘`]?")
        elif ch in MACRON:
            out.append(f"[{ch}{MACRON[ch]}]")
        else:
            out.append(re.escape(ch))
    return r"(?<![\w])" + "".join(out) + r"(?!\w)"


_RULES = [(re.compile(_pattern(n)), n) for n in (*NAMES, *PLACES)]
_ANY_CASE = [(re.compile(_pattern(n), re.IGNORECASE), n) for n in (*NAMES, *PLACES)]
_ORG = re.compile(
    r"(?<!\w)(?:" + "|".join(re.escape(n) for n in sorted(ORG_NAMES, key=len, reverse=True)) + r")(?!\w)"
)


def _plain_chunks(s: str) -> list[tuple[bool, str]]:
    out, pos = [], 0
    for m in _ORG.finditer(s):
        out += [(False, s[pos : m.start()]), (True, m.group())]
        pos = m.end()
    return [*out, (False, s[pos:])]


def hawaiian(s: str) -> str:
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


def _plain_word(word: str) -> str:
    word = unicodedata.normalize("NFD", word.casefold())
    return "".join(c for c in word if not unicodedata.combining(c) and c not in OKINA + LOOKALIKES)


def _marked(word: str) -> bool:
    return OKINA in word or any(c in MACRON for c in word.casefold())


def unmarked(s: str, names) -> list[tuple[str, str]]:
    """(written, correct) for each known name that s writes without its ʻokina or kahakō, or with a lookalike
    mark. A name counts only when each of its words starts with a capital letter, and organization names are
    skipped."""
    spellings: dict[tuple[str, ...], dict[tuple[str, ...], str]] = {}
    for name in names:
        name = unicodedata.normalize("NFC", name)
        if words := tuple(m.group() for m in WORD.finditer(name)):
            spellings.setdefault(tuple(map(_plain_word, words)), {})[words] = name
    lengths = sorted({len(k) for k in spellings}, reverse=True)
    found = []
    s = unicodedata.normalize("NFC", EMPHASIS.sub("", strip_ignorable(s)))
    for is_org, chunk in _plain_chunks(s):
        if is_org:
            continue
        hits = list(WORD.finditer(chunk))
        i = 0
        while i < len(hits):
            for n in lengths:
                part = tuple(m.group() for m in hits[i : i + n])
                known = spellings.get(tuple(map(_plain_word, part))) if len(part) == n else None
                if not known or not all(w.lstrip(OKINA + LOOKALIKES)[:1].isupper() for w in part):
                    continue
                right = sorted(name for words, name in known.items() if any(map(_marked, words)))
                folded = {tuple(w.casefold() for w in k) for k in known}
                if tuple(w.casefold() for w in part) not in folded and right:
                    found.append((chunk[hits[i].start() : hits[i + n - 1].end()], right[0]))
                i += n - 1
                break
            i += 1
    return found
