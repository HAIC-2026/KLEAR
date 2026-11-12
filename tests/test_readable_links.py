"""Every source and program link the website shows opens a page people can read, not a file or an API answer."""

import re
from html.parser import HTMLParser
from urllib.parse import urlsplit

import duckdb
import httpx
import pytest

from klear import config
from klear.etl import build, chas, cms, hud, irs, places, profile
from klear.programs import catalog
from klear.rules.registry import load_rules
from klear.web import __main__ as web
from klear.web.display import SHARES_SOURCE

# Downloads and API answers: what the build reads, never what a reader should open.
MACHINE_PATH = re.compile(
    r"\.(csv|zip|json|txt|xlsx?|geojson)$|/FeatureServer|/resource/|/DataDownload/", re.IGNORECASE
)
MACHINE_HOST = re.compile(r"^(api\.|services\.arcgis\.com$)")
# Sources whose district figures KLEAR moves from ZIP codes or tracts (tier A) or averages from a model (tier M).
METHOD_SOURCES = [
    irs.SOURCE_ID, cms.SOURCE_ID, hud.SOURCE_ID, places.SOURCE_ID, places.COUNTY_SOURCE_ID, chas.SOURCE_ID,
]  # fmt: skip
# Sites that answer scripts with a block or a challenge page, though a browser gets the page.
SCRIPT_WALLS = {"www.ssa.gov": {403}, "www.huduser.gov": {202, 403}}
BROWSER = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/130.0 Safari/537.36"


def pages() -> dict[str, str]:
    """Every address the website links a reader to for a source or a program, named by what it is for."""
    out = {f"source {sid}": s[4] for sid, s in build.SOURCES.items()}
    out |= {f"source {sid}": s["page_url"] for sid, s in profile.SOURCES.items()}
    out["source 2020 Census shares"] = SHARES_SOURCE["url"]
    out |= {f"rule {r.rule_id}": r.info_url for r in load_rules()}
    out |= {f"program {p.program_id}": p.url for p in catalog()}
    return out


@pytest.mark.parametrize(("what", "url"), sorted(pages().items()))
def test_links_are_pages_not_downloads_or_api_answers(what, url):
    parts = urlsplit(url)
    assert parts.scheme == "https", what
    assert not MACHINE_HOST.search(parts.hostname), what
    assert not MACHINE_PATH.search(parts.path), what


def test_the_build_still_downloads_from_the_machine_addresses():
    # page_url is for readers; url stays the address the build fetches, as the manifest records it.
    for sid, (_, _, url, _, page) in build.SOURCES.items():
        assert url != page, sid
    assert all(s[2].startswith("https://") for s in build.SOURCES.values())


class _Ids(HTMLParser):
    def __init__(self):
        super().__init__()
        self.ids = set()

    def handle_starttag(self, tag, attrs):
        self.ids.update(v for k, v in attrs if k == "id")


def test_the_methods_page_has_a_section_for_every_estimated_source():
    parser = _Ids()
    parser.feed((web.STATIC / "methods.html").read_text(encoding="utf-8"))
    assert set(METHOD_SOURCES) <= parser.ids
    assert "/methods.html" in web.PUBLIC


@pytest.fixture
def real_con():
    store = config.BUILD_DIR / "klear.duckdb"
    if not store.is_file():
        pytest.skip("Build the store to check its sources")
    con = duckdb.connect(str(store), read_only=True)
    yield con
    con.close()


def test_every_estimated_source_in_the_built_store_has_a_methods_section(real_con):
    parser = _Ids()
    parser.feed((web.STATIC / "methods.html").read_text(encoding="utf-8"))
    used = {
        r[0]
        for r in real_con.execute("SELECT DISTINCT source_id FROM fact WHERE tier IN ('A', 'M')").fetchall()
    }
    assert used and used <= parser.ids


@pytest.mark.network
@pytest.mark.parametrize(("what", "url"), sorted(pages().items()))
def test_links_open_web_pages(what, url):
    response = httpx.get(url, headers={"User-Agent": BROWSER}, follow_redirects=True, timeout=60.0)
    if response.status_code in SCRIPT_WALLS.get(urlsplit(url).hostname, ()):
        pytest.skip(f"{urlsplit(url).hostname} blocks scripts")
    assert response.status_code == 200, what
    assert response.headers["content-type"].startswith("text/html"), what
