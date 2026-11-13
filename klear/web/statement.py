"""The accessibility statement as a page of the site. It is rendered from docs/ACCESSIBILITY.md each time it is
asked for, so the page and the statement in the repository always say the same thing."""

import re
from html import escape

from klear.config import ROOT

STATEMENT = ROOT / "docs" / "ACCESSIBILITY.md"
# The page around the statement: the site's header and footer, with this slot where the statement goes.
SLOT = "<!-- statement -->"
# Links between documents, like ACCESSIBILITY-CHECKLIST.md, open on GitHub, where those documents live.
REPO_DOCS = "https://github.com/HAIC-2026/KLEAR/blob/main/docs/"
EXTERNAL = '<span class="visually-hidden"> (external site)</span>'

_LINK = re.compile(r"\[([^\]]+)\]\(([^)\s]+)\)")
_BOLD = re.compile(r"\*\*(.+?)\*\*")
_CODE = re.compile(r"`([^`]+)`")


def _link(m: re.Match) -> str:
    text, href = m.group(1), m.group(2)
    if href.startswith(("/", "#")):
        return f'<a href="{href}">{text}</a>'
    if not re.match(r"https?://", href):
        href = REPO_DOCS + href
    return f'<a class="ext" href="{href}">{text}{EXTERNAL}</a>'


def inline(text: str) -> str:
    """One block of Markdown text as HTML: escaped, with its code, bold and links."""
    html = escape(text, quote=True)
    html = _CODE.sub(r"<code>\1</code>", html)
    html = _BOLD.sub(r"<strong>\1</strong>", html)
    return _LINK.sub(_link, html)


def markdown(text: str) -> str:
    """The part of Markdown the statement uses: headings, paragraphs, bulleted lists, links, bold and code."""
    out, para, items = [], [], []

    def flush():
        if para:
            out.append(f"<p>{inline(' '.join(para))}</p>")
            para.clear()
        if items:
            out.append("<ul>" + "".join(f"<li>{inline(i)}</li>" for i in items) + "</ul>")
            items.clear()

    for raw in text.splitlines():
        line = raw.strip()
        heading = re.match(r"(#{1,4})\s+(.*)", line)
        if not line:
            flush()
        elif heading:
            flush()
            level = len(heading.group(1))
            out.append(f"<h{level}>{inline(heading.group(2))}</h{level}>")
        elif re.match(r"[-*]\s+", line):
            if para:
                flush()
            items.append(re.sub(r"^[-*]\s+", "", line))
        elif items and raw[:1].isspace():
            items[-1] += " " + line
        else:
            if items:
                flush()
            para.append(line)
    flush()
    return "\n".join(out)


def statement_page(shell: str, source=STATEMENT) -> bytes:
    """The statement inside the site's page."""
    return shell.replace(SLOT, markdown(source.read_text(encoding="utf-8")), 1).encode()
