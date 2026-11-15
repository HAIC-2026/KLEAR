"""Every page of the site has the same header and footer, marks itself as the page you are on, and reaches every other
part of KLEAR. The accessibility statement is served on the site from docs/ACCESSIBILITY.md."""

import html as entities
import re
import threading
from functools import partial
from http.client import HTTPConnection

import pytest

from klear.web import __main__ as web
from klear.web.statement import SLOT, STATEMENT, markdown, statement_page

# Each page, and the address it is served at.
PAGES = {
    "index.html": "/",
    "rankings.html": "/rankings",
    "data.html": "/data",
    "accessibility.html": "/accessibility.html",
}
# The header's links after the wordmark, which leads home to Find: compare two districts, rank every district, then
# the data. Each opens a page of its own; Compare is a view of the home page's file.
HEADER_LINKS = [
    ("/compare", "Compare"),
    ("/rankings", "Rankings"),
    ("/data", "Data & methods"),
]


def html(page: str) -> str:
    return (web.STATIC / page).read_text(encoding="utf-8")


def block(page: str, tag: str) -> str:
    """A page's header or footer element."""
    pattern = r'<header class="site-header">.*?</header>' if tag == "header" else r"<footer>.*?</footer>"
    found = re.search(pattern, html(page), re.DOTALL)
    assert found, (page, tag)
    return found.group(0)


def same(markup: str) -> str:
    """Markup with the page's own mark taken out: which link is current."""
    return markup.replace(' aria-current="page"', "")


def links(markup: str) -> list[tuple[str, str]]:
    return [
        (href, entities.unescape(re.sub(r"<[^>]+>", "", text)).strip())
        for href, text in re.findall(r'<a [^>]*?href="([^"]+)"[^>]*>(.*?)</a>', markup, re.DOTALL)
    ]


@pytest.mark.parametrize("tag", ["header", "footer"])
def test_every_page_has_the_same_header_and_footer(tag):
    home = same(block("index.html", tag))
    for page in PAGES:
        assert same(block(page, tag)) == home, f"{page}'s {tag} differs from index.html's"


def test_the_header_reaches_every_part_of_klear_in_task_order():
    header = block("index.html", "header")
    assert links(re.search(r"<nav .*?</nav>", header, re.DOTALL).group(0)) == HEADER_LINKS
    assert "<details" not in header, "the header has no menu: every part is one click away"
    # Every link opens a page the server serves.
    for href, _ in HEADER_LINKS:
        served = "/index.html" if href == "/" else web.PAGES.get(href, href)
        assert href in web.PUBLIC and (web.STATIC / served.lstrip("/")).is_file(), href


def test_no_header_link_jumps_into_a_page():
    # A link to part of a page means different things on different pages, so the header has none.
    for page in PAGES:
        assert not re.search(r'href="/?#', block(page, "header")), page


@pytest.mark.parametrize("page", [p for p in PAGES if p != "index.html"])
def test_each_page_marks_itself_as_the_current_page(page):
    here = PAGES[page]
    header, footer = block(page, "header"), block(page, "footer")
    current = re.findall(r'<a href="([^"]+)" aria-current="page">', header + footer)
    in_header = any(href == here for href, _ in HEADER_LINKS)
    in_footer = f'href="{here}"' in same(footer)
    assert current and current == [here] * (in_header + in_footer), page
    assert (header + footer).count("aria-current") == len(current), page
    # The breadcrumb names the page as the current one too.
    assert re.search(
        r'<p class="crumb"><a href="/">KLEAR</a> .*?<span aria-current="page">[^<]+</span></p>', html(page)
    )


def test_the_home_page_marks_no_link_as_current():
    assert "aria-current" not in block("index.html", "header") + block("index.html", "footer")


def test_the_footer_says_who_made_klear_and_lists_the_team_the_reports_and_the_statement():
    footer = block("index.html", "footer")
    assert "Made for HAIC 2026" in footer and "Not an official State of Hawaiʻi product" in footer
    assert [text for _, text in links(footer)] == [
        "datatrove.ai (external site)",
        "Kanta Saito on GitHub (external site)",
        "Andrew He on GitHub (external site)",
        "Raine S. Mendoza on GitHub (external site)",
        "Lukas Moroz on GitHub (external site)",
        "Elijah Saloma on GitHub (external site)",
        "Data & methods",
        "Validation report (external site)",
        "Accessibility statement",
        "Open a GitHub issue (external site)",
    ]
    assert '<a href="/accessibility.html">Accessibility statement</a>' in footer
    assert "ACCESSIBILITY.md" not in footer


def test_every_page_loads_the_menu_script_and_is_public():
    for page, address in PAGES.items():
        assert '<script src="/nav.js" defer></script>' in html(page), page
        assert address in web.PUBLIC, page
    assert "/nav.js" in web.PUBLIC


# The accessibility statement


def test_markdown_covers_what_the_statement_uses():
    text = """# Title

Intro with **bold**, `code` and <b>tags</b> & "quotes".
Same paragraph.

## List

- First item with [a document](CHECKLIST.md).
- Second item
  that goes on, with [an issue](https://github.com/x/y/issues/1) and [the glossary](/glossary.html).
After the list.
"""
    assert markdown(text) == (
        "<h1>Title</h1>\n"
        "<p>Intro with <strong>bold</strong>, <code>code</code> and &lt;b&gt;tags&lt;/b&gt; &amp; &quot;quotes&quot;. "
        "Same paragraph.</p>\n"
        "<h2>List</h2>\n"
        '<ul><li>First item with <a class="ext" href="https://github.com/HAIC-2026/KLEAR/blob/main/docs/CHECKLIST.md">'
        'a document<span class="visually-hidden"> (external site)</span></a>.</li>'
        '<li>Second item that goes on, with <a class="ext" href="https://github.com/x/y/issues/1">an issue'
        '<span class="visually-hidden"> (external site)</span></a> and <a href="/glossary.html">the glossary</a>.</li>'
        "</ul>\n"
        "<p>After the list.</p>"
    )


def test_the_statement_page_holds_the_whole_statement():
    shell = html("accessibility.html")
    assert shell.count(SLOT) == 1
    page = statement_page(shell).decode()
    assert SLOT not in page
    assert page.count("<h1>") == 1 and "<h1>KLEAR accessibility statement</h1>" in page
    headings = re.findall(r"^## (.+)$", STATEMENT.read_text(encoding="utf-8"), re.MULTILINE)
    assert headings and re.findall(r"<h2>(.+?)</h2>", page) == headings
    # Links between documents open on GitHub, where those documents live.
    assert 'href="ACCESSIBILITY-CHECKLIST.md"' not in page


@pytest.fixture
def port(tmp_path):
    srv = web.Server(("127.0.0.1", 0), partial(web.Handler, store=tmp_path / "klear.duckdb"))
    thread = threading.Thread(target=srv.serve_forever, daemon=True)
    thread.start()
    yield srv.server_port
    srv.shutdown()
    srv.server_close()
    thread.join()


def test_the_server_serves_the_statement_and_the_menu_script(port):
    con = HTTPConnection("127.0.0.1", port, timeout=10)
    bodies = {}
    for path, kind in (("/accessibility.html", "text/html"), ("/nav.js", "javascript")):
        con.request("GET", path)
        response = con.getresponse()
        bodies[path] = response.read()
        assert response.status == 200 and kind in response.headers["content-type"], path
        assert int(response.headers["content-length"]) == len(bodies[path]), path
    con.close()
    page = bodies["/accessibility.html"].decode()
    assert "<h1>KLEAR accessibility statement</h1>" in page and '<header class="site-header">' in page


@pytest.mark.parametrize(
    ("old", "new"),
    [
        ("/outreach.html?pair=snap&chamber=senate", "/rankings?view=outreach&pair=snap&chamber=senate"),
        ("/compare.html", "/compare"),
        (
            "/compare?view=outreach&pair=snap&chamber=senate",
            "/rankings?view=outreach&pair=snap&chamber=senate",
        ),
        ("/compare?view=rankings", "/rankings?view=rankings"),
        ("/sources.html", "/data?section=sources"),
        ("/methods.html", "/data?section=methods"),
        ("/glossary.html", "/data?section=glossary"),
        ("/checks.html", "/data?section=checks"),
    ],
)
def test_moved_pages_send_old_links_to_their_new_home(port, old, new):
    con = HTTPConnection("127.0.0.1", port, timeout=10)
    con.request("GET", old)
    response = con.getresponse()
    response.read()
    con.close()
    assert (response.status, response.getheader("Location")) == (301, new)


def test_the_compare_rankings_and_data_pages_are_served_at_clean_addresses(port):
    con = HTTPConnection("127.0.0.1", port, timeout=10)
    for path, title in (
        ("/compare", '<h1 id="compare-title" tabindex="-1">Compare</h1>'),
        ("/rankings", "<h1>Rankings and outreach</h1>"),
        ("/data", "<h1>Data and methods</h1>"),
    ):
        con.request("GET", path)
        response = con.getresponse()
        body = response.read().decode()
        assert response.status == 200 and title in body, path
    con.close()
