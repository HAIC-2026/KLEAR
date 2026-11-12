# User-facing text writes the ʻokina as U+02BB, never as ' ‘ ’ ` or another lookalike, official organization
# names excepted.
import re
from pathlib import Path

import duckdb
import pytest
import yaml

from klear import config
from klear.text import OKINA, OKINA_LOOKALIKES, ORG_NAMES

ROOT = config.ROOT
STORE = config.BUILD_DIR / "klear.duckdb"
LOOKALIKES = re.escape(OKINA_LOOKALIKES)
VOWELS = "aeiouāēīōū"
MACRON = {"ā": "a", "ē": "e", "ī": "i", "ō": "o", "ū": "u"}
TEXT_SUFFIXES = {".css", ".html", ".js", ".json", ".md", ".py", ".sql", ".svg", ".txt", ".yaml"}
# Every Hawaiian syllable ends in a vowel, so inside a word the ʻokina always stands between two vowels.
BETWEEN_VOWELS = re.compile(f"[{VOWELS}][{LOOKALIKES}][{VOWELS}]", re.IGNORECASE)


def user_facing_files() -> list[Path]:
    """What the site, its answers and its public reports are made from: the code and pages under klear/, the
    data files under data/ (not the downloads or the build), the docs and the README."""
    files = [
        p for p in (ROOT / "klear").rglob("*") if p.suffix in TEXT_SUFFIXES and "__pycache__" not in p.parts
    ]
    data = ROOT / "data"
    files += [
        p for p in data.rglob("*.yaml") if not {"raw", "build", "manual"} & set(p.relative_to(data).parts)
    ]
    return sorted([*files, *(ROOT / "docs").rglob("*.md"), ROOT / "README.md"])


def without_org_names(text: str) -> str:
    for name in sorted(ORG_NAMES, key=len, reverse=True):
        text = text.replace(name, "")
    return text


# Common words with a leading ʻokina, checked even where no text spells them right
KNOWN_INITIAL = {
    "Aiea",
    "Āina",
    "Ewa",
    "Iolani",
    "ohana",
    "okina",
    "ōlelo",
    "Ōmaʻo",
    "Ualapuʻe",
    "Ulupalakua",
}


def initial_okina_words(texts: list[str]) -> set[str]:
    """Words written with a leading ʻokina, such as ʻEwa and ʻohana: the known ones and those in the texts."""
    found = {w for t in texts for w in re.findall(rf"(?<![\w{OKINA}]){OKINA}(\w[\w{OKINA}]*)", t)}
    return KNOWN_INITIAL | found


def _spelled(word: str) -> str:
    out = []
    for ch in word.casefold():
        if ch == OKINA:
            out.append(f"[{OKINA}{LOOKALIKES}]?")
        elif ch in MACRON:
            out.append(f"[{ch}{MACRON[ch]}]")
        else:
            out.append(re.escape(ch))
    return "".join(out)


def okina_lookalikes(text: str, words: set[str]) -> list[str]:
    """Spots in text where ' ‘ ’ or ` stands for an ʻokina: between two vowels, or before a word the text
    elsewhere writes with a leading ʻokina."""
    text = without_org_names(text)
    found = [m.group() for m in BETWEEN_VOWELS.finditer(text)]
    if words:
        alts = "|".join(_spelled(w) for w in sorted(words, key=len, reverse=True))
        initial = re.compile(rf"(?<!\w)[{LOOKALIKES}](?:{alts})(?!\w)", re.IGNORECASE)
        found += [m.group() for m in initial.finditer(text)]
    return found


def test_finds_lookalikes():
    words = {"Ewa", "ohana"}
    assert okina_lookalikes("Hawai'i, O‘ahu and Kane`ohe", words) == ["i'i", "O‘a", "e`o"]
    assert okina_lookalikes("‘Ewa Beach and 'Ohana", words) == ["‘Ewa", "'Ohana"]
    assert okina_lookalikes("Hawaiʻi’s ʻEwa, don't, it's, 'all', y'know", words) == []
    assert okina_lookalikes("HO'OLA LAHUI HAWAI'I", words) == []


def test_scans_the_site_data_and_reports():
    files = {p.relative_to(ROOT).as_posix() for p in user_facing_files()}
    for want in (
        "klear/web/static/index.html",
        "klear/web/static/app.js",
        "klear/agent/prompt.py",
        "data/places/hawaiian_names.yaml",
        "data/places/areas.yaml",
        "docs/VALIDATION.md",
        "README.md",
    ):
        assert want in files


def test_user_facing_text_uses_u02bb_for_the_okina():
    texts = {p: p.read_text(encoding="utf-8") for p in user_facing_files()}
    words = initial_okina_words(list(texts.values()))
    assert {"Ewa", "ohana"} <= words
    bad = []
    for path, text in texts.items():
        for n, line in enumerate(text.splitlines(), 1):
            bad += [
                f"{path.relative_to(ROOT).as_posix()}:{n}: {hit}" for hit in okina_lookalikes(line, words)
            ]
    assert not bad, "write the ʻokina as U+02BB:\n" + "\n".join(bad)


def _strings(node) -> list[str]:
    if isinstance(node, dict):
        return [s for k, v in node.items() for s in (*_strings(k), *_strings(v))]
    if isinstance(node, list):
        return [s for v in node for s in _strings(v)]
    return [node] if isinstance(node, str) else []


@pytest.mark.parametrize("name", ["hawaiian_names.yaml", "areas.yaml"])
def test_place_files_use_only_u02bb(name):
    data = yaml.safe_load((ROOT / "data" / "places" / name).read_text(encoding="utf-8"))
    bad = [
        s for s in _strings(data) if any(c in s for c in OKINA_LOOKALIKES) and not s.startswith("https://")
    ]
    assert not bad


def test_built_names_use_only_u02bb():
    if not STORE.exists():
        pytest.skip("Build the store to check the names it shows")
    con = duckdb.connect(str(STORE), read_only=True)
    try:
        tables = {r[0] for r in con.execute("SELECT table_name FROM information_schema.tables").fetchall()}
        names = []
        for table in ("place", "area", "area_alias", "geography"):
            if table in tables:
                names += [r[0] for r in con.execute(f"SELECT DISTINCT name FROM {table}").fetchall()]
    finally:
        con.close()
    assert names
    assert not [n for n in names if n and any(c in n for c in OKINA_LOOKALIKES)]
