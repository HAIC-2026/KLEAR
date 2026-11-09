"""The site's colors meet WCAG AA contrast and stay distinct for people with common color vision deficiencies.

Colors are read from the tokens at the top of style.css, so a color change that breaks a pair fails here.
Color blindness is simulated with the Machado, Oliveira and Fernandes (2009) matrices at full severity,
and differences are CIELAB distances (delta E 1976), where about 10 is clearly visible and 20 is obvious.
"""

import math
import re

import pytest

from klear.web import __main__ as web

ROOT_BLOCK = re.search(
    r":root\{(.*?)\n\}", (web.STATIC / "style.css").read_text(encoding="utf-8"), re.DOTALL
).group(1)
TOKENS = dict(re.findall(r"--([\w-]+):(#[0-9a-fA-F]{6})", ROOT_BLOCK))
WHITE = "#ffffff"

# Linear-RGB matrices for each deficiency at full severity.
DEFICIENCIES = {
    "protanopia": (
        (0.152286, 1.052583, -0.204868),
        (0.114503, 0.786281, 0.099216),
        (-0.003882, -0.048116, 1.051998),
    ),
    "deuteranopia": (
        (0.367322, 0.860646, -0.227968),
        (0.280085, 0.672501, 0.047413),
        (-0.011820, 0.042940, 0.968881),
    ),
    "tritanopia": (
        (1.255528, -0.076749, -0.178779),
        (-0.078411, 0.930809, 0.147602),
        (0.004733, 0.691367, 0.303900),
    ),
}


def color(name: str) -> str:
    return WHITE if name == "white" else TOKENS[name]


def _linear(hex_color: str) -> list[float]:
    channels = [int(hex_color[i : i + 2], 16) / 255 for i in (1, 3, 5)]
    return [c / 12.92 if c <= 0.04045 else ((c + 0.055) / 1.055) ** 2.4 for c in channels]


def _luminance(rgb: list[float]) -> float:
    return 0.2126 * rgb[0] + 0.7152 * rgb[1] + 0.0722 * rgb[2]


def contrast(a: str, b: str) -> float:
    hi, lo = sorted((_luminance(_linear(color(a))), _luminance(_linear(color(b)))), reverse=True)
    return (hi + 0.05) / (lo + 0.05)


def _lab(rgb: list[float]) -> tuple[float, float, float]:
    r, g, b = (min(1.0, max(0.0, c)) for c in rgb)
    x = (0.4124 * r + 0.3576 * g + 0.1805 * b) / 0.95047
    y = 0.2126 * r + 0.7152 * g + 0.0722 * b
    z = (0.0193 * r + 0.1192 * g + 0.9505 * b) / 1.08883

    def f(t):
        return t ** (1 / 3) if t > 0.008856 else 7.787 * t + 16 / 116

    return 116 * f(y) - 16, 500 * (f(x) - f(y)), 200 * (f(y) - f(z))


def difference(a: str, b: str, vision: str | None = None) -> float:
    """CIELAB distance between two colors as seen with normal vision or the named deficiency."""

    def seen(name):
        rgb = _linear(color(name))
        if vision:
            rgb = [sum(m * c for m, c in zip(row, rgb)) for row in DEFICIENCIES[vision]]
        return _lab(rgb)

    return math.dist(seen(a), seen(b))


# Text and its background: 4.5:1 (WCAG 1.4.3).
TEXT = [
    ("ink", "bg"), ("ink", "surface"), ("ink", "subtle"),
    ("muted", "bg"), ("muted", "surface"), ("muted", "subtle"),
    ("placeholder", "white"),
    ("house", "white"), ("senate", "white"), ("house-dark", "white"), ("senate-dark", "white"),
    ("white", "house"), ("white", "senate"), ("white", "house-dark"), ("white", "senate-dark"),
    ("ink", "house-soft"), ("ink", "senate-soft"), ("muted", "house-soft"), ("muted", "senate-soft"),
    ("house-dark", "house-soft"),
    ("error-ink", "error-bg"),
    ("on-dark", "ink"), ("on-dark-muted", "ink"), ("on-dark", "band-bg"),
    ("white", "ink"),
    ("ink", "house-hover"), ("ink", "senate-hover"),  # district numbers on a district under the mouse
]  # fmt: skip

# Shapes and boundaries people need to see: 3:1 (WCAG 1.4.11).
SHAPES = [
    ("control", "white"),  # text field and select borders
    ("house", "bg"), ("senate", "bg"),  # focus outlines
    ("house", "house-land"), ("senate", "senate-land"),  # map borders, and a picked district against the rest
    ("house", "map-sea"), ("senate", "map-sea"),  # map coastline
    ("house-zip", "map-dim"), ("senate-zip", "map-dim"),  # districts in a ZIP code against the rest
    ("control", "map-dim"), ("control", "map-sea"),  # dimmed district borders
    ("bar-other", "surface"), ("bar-other", "white"),  # comparison bars in an answer
    ("house", "surface"), ("senate", "surface"),  # the asked-about district's bar
    # The outline around a district under the mouse, against every fill and the sea around it
    ("ink", "house-land"), ("ink", "senate-land"), ("ink", "house-hover"), ("ink", "senate-hover"),
    ("ink", "house-zip"), ("ink", "senate-zip"), ("ink", "map-dim"), ("ink", "map-sea"),
    ("house", "house-hover"), ("senate", "senate-hover"),  # a district under the mouse against a picked one
]  # fmt: skip

# Colors that mean different things side by side, which must stay apart with each deficiency.
DISTINCT = [
    ("house", "senate"),
    ("house", "bar-other"), ("senate", "bar-other"),
    ("house-zip", "map-dim"), ("senate-zip", "map-dim"),
    ("house", "house-land"), ("senate", "senate-land"),
    ("house", "house-hover"), ("senate", "senate-hover"),
]  # fmt: skip

# A district under the mouse also changes fill. Its outline is the main cue, so the fill change needs only to be
# clearly visible (delta E 15), with every deficiency.
NOTICEABLE = [("house-hover", "house-land"), ("senate-hover", "senate-land")]


def test_every_checked_color_is_a_token():
    for a, b in TEXT + SHAPES + DISTINCT + NOTICEABLE:
        assert color(a) and color(b)


@pytest.mark.parametrize(("fg", "bg"), TEXT)
def test_text_contrast(fg, bg):
    assert contrast(fg, bg) >= 4.5, f"{fg} on {bg}: {contrast(fg, bg):.2f}:1"


@pytest.mark.parametrize(("fg", "bg"), SHAPES)
def test_shape_contrast(fg, bg):
    assert contrast(fg, bg) >= 3, f"{fg} against {bg}: {contrast(fg, bg):.2f}:1"


@pytest.mark.parametrize("vision", [None, *DEFICIENCIES])
@pytest.mark.parametrize(("a", "b"), DISTINCT)
def test_colors_stay_distinct_with_color_blindness(a, b, vision):
    assert difference(a, b, vision) >= 20, f"{a} and {b} with {vision or 'normal vision'}"


@pytest.mark.parametrize("vision", [None, *DEFICIENCIES])
@pytest.mark.parametrize(("a", "b"), NOTICEABLE)
def test_hover_fill_is_noticeable_with_color_blindness(a, b, vision):
    assert difference(a, b, vision) >= 15, f"{a} and {b} with {vision or 'normal vision'}"


def test_simulation_matches_known_values():
    # Pure red and green look alike to someone with deuteranopia, which this check depends on catching.
    TOKENS["_red"], TOKENS["_green"] = "#d62728", "#2ca02c"
    try:
        assert difference("_red", "_green") > 80
        assert difference("_red", "_green", "deuteranopia") < 20
    finally:
        del TOKENS["_red"], TOKENS["_green"]
