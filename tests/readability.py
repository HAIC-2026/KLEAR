"""A small Flesch-Kincaid grade estimate for checking that reader text stays plain, about grade 8."""

import re
from html.parser import HTMLParser

WORD = re.compile(r"[^\W\d_]+(?:[-'’ʻ][^\W\d_]+)*", re.UNICODE)
# A sentence ends at . ! or ? before a space or the end, so 1.5 and HealthCare.gov do not end one.
SENTENCE_END = re.compile(r"[.!?]+[”\"’)]*(?=\s|$)")
VOWELS = re.compile(r"[aeiouyāēīōū]+")


def syllables(word: str) -> int:
    """Vowel groups, less a silent final e: rough, but steady enough to compare texts."""
    w = re.sub(r"['’ʻ]", "", word.casefold())
    count = len(VOWELS.findall(w))
    if w.endswith("e") and not w.endswith(("le", "ee", "ye")) and count > 1:
        count -= 1
    return max(1, count)


def grade(blocks: list[str]) -> float:
    """Flesch-Kincaid grade level of blocks of text. A block without an end mark, like a heading or a button,
    counts as one sentence."""
    words = [w for b in blocks for w in WORD.findall(b)]
    if not words:
        return 0.0
    sentences = sum(max(1, len(SENTENCE_END.findall(b.strip()))) for b in blocks if WORD.search(b))
    total = sum(syllables(w) for w in words)
    return 0.39 * len(words) / sentences + 11.8 * total / len(words) - 15.59


class _Text(HTMLParser):
    """The text a reader sees in a page's body, one block per paragraph, heading, list item, cell or control."""

    BLOCKS = frozenset(
        {
            "p",
            "li",
            "dt",
            "dd",
            "td",
            "th",
            "h1",
            "h2",
            "h3",
            "figcaption",
            "label",
            "summary",
            "button",
            "div",
        }
    )
    SKIP = frozenset({"head", "script", "style", "svg", "select"})
    VOID = frozenset({"img", "input", "br", "hr", "meta", "link", "source"})

    def __init__(self):
        super().__init__()
        self.blocks, self._buf, self._open = [], [], []

    def handle_starttag(self, tag, attrs):
        if tag in self.VOID:
            return
        hidden = tag in self.SKIP or "visually-hidden" in (dict(attrs).get("class") or "").split()
        self._open.append((tag, hidden))
        self._edge(tag)

    def handle_endtag(self, tag):
        while self._open:
            if self._open.pop()[0] == tag:
                break
        self._edge(tag)

    def handle_data(self, data):
        if not any(hidden for _, hidden in self._open):
            self._buf.append(data)

    def _edge(self, tag):
        if tag in self.BLOCKS:
            self.flush()
        else:
            self._buf.append(" ")  # inline tags still separate words, as in "Asking about" + "No district"

    def flush(self):
        if text := " ".join("".join(self._buf).split()):
            self.blocks.append(text)
        self._buf = []


def page_text(html: str) -> list[str]:
    """Blocks of visible text in a page, leaving out text only screen readers get."""
    parser = _Text()
    parser.feed(html)
    parser.flush()
    return parser.blocks
