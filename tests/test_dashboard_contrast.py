"""Contrast of the stylesheet's color tokens, by the WCAG 2.2 relative luminance formula.

Each pair names a foreground token and a background it is actually drawn on. Text needs 4.5:1,
chart lines, markers and the focus ring need 3:1 against the card surface.
"""

import re
from functools import cache
from pathlib import Path

import pytest

STYLESHEET = Path(__file__).resolve().parent.parent / "src/detecttrace/templates/dashboard.css"
TEXT = 4.5
GRAPHIC = 3.0
PAIRS = [
    # Text on the page, the surfaces, and the hovered row
    ("--text", "--bg", TEXT),
    ("--text", "--bg-glow", TEXT),
    ("--text", "--surface", TEXT),
    ("--text", "--surface-2", TEXT),
    ("--text", "--surface-3", TEXT),
    ("--text", "--row-hover", TEXT),
    ("--muted", "--bg", TEXT),
    ("--muted", "--bg-glow", TEXT),
    ("--muted", "--surface", TEXT),
    ("--muted", "--surface-2", TEXT),
    ("--muted", "--surface-3", TEXT),
    ("--muted", "--row-hover", TEXT),
    ("--danger", "--bg", TEXT),
    ("--danger", "--bg-glow", TEXT),
    ("--danger", "--surface", TEXT),
    ("--danger", "--surface-2", TEXT),
    ("--danger", "--surface-3", TEXT),
    ("--danger", "--pill-bad-bg", TEXT),
    ("--ok", "--bg", TEXT),
    ("--ok", "--bg-glow", TEXT),
    ("--ok", "--surface-3", TEXT),
    ("--ok", "--pill-agree-bg", TEXT),
    ("--warn", "--bg", TEXT),
    ("--warn", "--bg-glow", TEXT),
    ("--warn", "--surface", TEXT),
    ("--warn", "--surface-2", TEXT),
    ("--warn", "--surface-3", TEXT),
    ("--warn", "--banner-bg", TEXT),
    ("--warn", "--note-warn-bg", TEXT),
    ("--link", "--bg", TEXT),
    ("--link", "--bg-glow", TEXT),
    ("--link", "--surface-2", TEXT),
    ("--link", "--banner-bg", TEXT),
    ("--banner-text", "--bg", TEXT),
    ("--banner-text", "--bg-glow", TEXT),
    ("--banner-text", "--banner-bg", TEXT),
    ("--banner-text", "--note-warn-bg", TEXT),
    ("--pill-disagree-fg", "--bg", TEXT),
    ("--pill-disagree-fg", "--bg-glow", TEXT),
    ("--pill-disagree-fg", "--pill-disagree-bg", TEXT),
    # Counts in the confusion-matrix heat cells
    ("--text", "--heat-0", TEXT),
    ("--text", "--heat-ok-1", TEXT),
    ("--text", "--heat-ok-2", TEXT),
    ("--text", "--heat-ok-3", TEXT),
    ("--text", "--heat-ok-4", TEXT),
    ("--text", "--heat-off-1", TEXT),
    ("--text", "--heat-off-2", TEXT),
    ("--text", "--heat-off-3", TEXT),
    ("--text", "--heat-off-4", TEXT),
    # Chart series, the all-versions halo, the focus ring and the danger outline
    ("--s1", "--surface", GRAPHIC),
    ("--s2", "--surface", GRAPHIC),
    ("--s3", "--surface", GRAPHIC),
    ("--s4", "--surface", GRAPHIC),
    ("--s5", "--surface", GRAPHIC),
    ("--s6", "--surface", GRAPHIC),
    ("--s-other", "--surface", GRAPHIC),
    ("--s-none", "--surface", GRAPHIC),
    ("--s-all", "--surface", GRAPHIC),
    ("--s-all-halo", "--surface", GRAPHIC),
    ("--focus", "--surface", GRAPHIC),
    ("--danger", "--surface", GRAPHIC),
]


@cache
def root_tokens() -> dict[str, str]:
    """The six-digit hex colors declared in the first :root block."""
    css = re.sub(r"/\*.*?\*/", "", STYLESHEET.read_text(encoding="utf-8"), flags=re.S)
    root = re.search(r":root\s*\{([^}]*)\}", css)
    assert root is not None
    return dict(re.findall(r"(--[\w-]+)\s*:\s*(#[0-9a-fA-F]{6})\b", root.group(1)))


def contrast(foreground: str, background: str) -> float:
    lighter, darker = sorted((luminance(foreground), luminance(background)), reverse=True)
    return (lighter + 0.05) / (darker + 0.05)


def luminance(color: str) -> float:
    red, green, blue = (_to_linear(int(color[i : i + 2], 16) / 255) for i in (1, 3, 5))
    return 0.2126 * red + 0.7152 * green + 0.0722 * blue


def _to_linear(channel: float) -> float:
    return channel / 12.92 if channel <= 0.04045 else ((channel + 0.055) / 1.055) ** 2.4


def test_contrast_matches_a_known_pair() -> None:
    assert round(contrast("#767676", "#ffffff"), 2) == 4.54


@pytest.mark.parametrize(
    ("foreground", "background", "minimum"),
    PAIRS,
    ids=[f"{fg}-on-{bg}" for fg, bg, _ in PAIRS],
)
def test_the_pair_meets_its_minimum_contrast(
    foreground: str, background: str, minimum: float
) -> None:
    tokens = root_tokens()
    assert contrast(tokens[foreground], tokens[background]) >= minimum
