"""Every page of the site has the same header and footer, marks itself as the page you are on, and reaches every other
part of KLEAR. The accessibility statement is served on the site from docs/ACCESSIBILITY.md."""

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
    "outreach.html": "/outreach.html",
    "methods.html": "/methods.html",
    "glossary.html": "/glossary.html",
    "checks.html": "/checks.html",
    "accessibility.html": "/accessibility.html",
}
# The header's links in order: tasks first, then the Data and methods menu, then About.
HEADER_LINKS = [
    ("/#find", "Find a district"),
    ("#chat", "Ask"),
    ("#district-map", "Map"),
    ("/outreach.html", "Outreach"),
    ("#sources", "Sources"),
    ("/methods.html", "Methods"),
    ("/glossary.html", "Glossary"),
    ("/checks.html", "How we check KLEAR"),
    ("#about", "About"),
]
MENU = ["Sources", "Methods", "Glossary", "How we check KLEAR"]


def html(page: str) -> str:
    return (web.STATIC / page).read_text(encoding="utf-8")


def block(page: str, tag: str) -> str:
    """A page's header or footer element."""
    pattern = r'<header class="site-header">.*?</header>' if tag == "header" else r"<footer>.*?</footer>"
    found = re.search(pattern, html(page), re.DOTALL)
    assert found, (page, tag)
    return found.group(0)


def same(markup: str) -> str:
    """Markup with the page's own marks taken out: which link is current, and whether a home page section is linked
    from the home page itself (#chat) or from another page (/#chat)."""
    return markup.replace(' aria-current="page"', "").replace('href="/#', 'href="#')


def links(markup: str) -> list[tuple[str, str]]:
    return [
        (href, re.sub(r"<[^>]+>", "", text).strip())
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
    # The menu is a details element, so it opens with no script.
    menu = re.search(r'<details class="nav-menu"><summary>(.*?)</summary>(.*?)</details>', header, re.DOTALL)
    assert re.sub(r"<[^>]+>", "", menu.group(1)) == "Data and methods"
    assert [text for _, text in links(menu.group(2))] == MENU
    # Every link leads somewhere: a section of the home page, or a page the server serves.
    home = html("index.html")
    for href, _ in HEADER_LINKS:
        if "#" in href:
            assert f'id="{href.split("#")[1]}"' in home, href
        else:
            assert href in web.PUBLIC and (web.STATIC / href.lstrip("/")).is_file(), href


def test_other_pages_link_the_home_page_sections_from_anywhere():
    # On the home page, its sections are linked in place, so a district's own address keeps its district. Elsewhere
    # they open the home page. Find a district opens the home page's search from everywhere.
    assert 'href="/#find"' in block("index.html", "header")
    assert not re.search(r'href="/#(?!find")', block("index.html", "header"))
    for page in set(PAGES) - {"index.html"}:
        assert not re.search(r'href="#', block(page, "header")), page


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


def test_the_footer_lists_methods_and_the_statement_on_the_site():
    footer = block("index.html", "footer")
    assert [text for _, text in links(footer)][:5] == [
        "Methods",
        "Glossary",
        "How we check KLEAR",
        "Validation report (external site)",
        "Accessibility statement",
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
