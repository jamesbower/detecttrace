"""Accessibility rules of the React dashboard's stylesheets, checked from their source.

Colours use the WCAG 2.2 relative luminance formula. Text needs 4.5:1; borders, series colours and
the focus ring need 3:1. Translucent and mixed backgrounds are composited onto the surface they
sit on, so each pair is a colour a reader actually sees.
"""

import re
from functools import cache
from pathlib import Path

import pytest

SOURCE = Path(__file__).resolve().parent.parent / "dashboard/src"
TOKENS = SOURCE / "tokens.css"
BASE = SOURCE / "base.css"
CASE_WINDOW = SOURCE / "case-window.ts"
COMPONENT_STYLES = sorted([*SOURCE.glob("components/*.css"), *SOURCE.glob("pages/*.css"), BASE])
ALL_STYLES = [TOKENS, *COMPONENT_STYLES]
TEXT = 4.5
GRAPHIC = 3.0
MINIMUM_TOUCH_TARGET_PX = 44

Rgb = tuple[float, float, float]


def _strip_comments(css: str) -> str:
    return re.sub(r"/\*.*?\*/", "", css, flags=re.S)


@cache
def tokens() -> dict[str, str]:
    root = re.search(r":root\s*\{(.*?)\n\}", _strip_comments(TOKENS.read_text("utf-8")), re.S)
    assert root is not None
    return {name: value.strip() for name, value in re.findall(r"([\w-]+)\s*:\s*([^;]+);", root[1])}


def resolve(name: str) -> tuple[Rgb, float]:
    """The token's colour and opacity, following var() chains."""
    value = tokens()[name]
    if var := re.fullmatch(r"var\((--[\w-]+)\)", value):
        return resolve(var[1])
    if hexed := re.fullmatch(r"#([0-9a-fA-F]{6})", value):
        digits = hexed[1]
        return (tuple(int(digits[i : i + 2], 16) for i in (0, 2, 4)), 1.0)  # type: ignore[return-value]
    if func := re.fullmatch(r"rgb\((\d+) (\d+) (\d+) / (\d+)%\)", value):
        red, green, blue, alpha = (int(part) for part in func.groups())
        return ((red, green, blue), alpha / 100)
    raise AssertionError(f"{name} is not a colour: {value}")


def colour(name: str) -> Rgb:
    rgb, alpha = resolve(name)
    assert alpha == 1.0, f"{name} is translucent; composite it with over()"
    return rgb


def over(foreground: str, background: Rgb) -> Rgb:
    """A translucent token drawn over an opaque colour."""
    rgb, alpha = resolve(foreground)
    return tuple(f * alpha + b * (1 - alpha) for f, b in zip(rgb, background, strict=True))  # type: ignore[return-value]


def skipped_step_cap() -> Rgb:
    """The strongest skipped-step cell: --heat-skip mixed into --panel at --heat-skip-cap."""
    share = int(tokens()["--heat-skip-cap"].removesuffix("%")) / 100
    return tuple(  # type: ignore[return-value]
        a * share + p * (1 - share)
        for a, p in zip(colour("--heat-skip"), colour("--panel"), strict=True)
    )


def luminance(rgb: Rgb) -> float:
    def linear(channel: float) -> float:
        channel /= 255
        return channel / 12.92 if channel <= 0.04045 else ((channel + 0.055) / 1.055) ** 2.4

    red, green, blue = (linear(channel) for channel in rgb)
    return 0.2126 * red + 0.7152 * green + 0.0722 * blue


def contrast(first: Rgb, second: Rgb) -> float:
    lighter, darker = sorted((luminance(first), luminance(second)), reverse=True)
    return (lighter + 0.05) / (darker + 0.05)


def test_contrast_matches_a_known_pair() -> None:
    assert round(contrast((0x76, 0x76, 0x76), (255, 255, 255)), 2) == 4.54


# Surfaces text is drawn on, by name.
HEAT_TOKENS = [
    "--heat-0",
    *(f"--heat-ok-{n}" for n in range(1, 5)),
    *(f"--heat-off-{n}" for n in range(1, 5)),
]
PAGE_SURFACES = {
    "--bg": lambda: colour("--bg"),  # page and detail areas
    "--panel": lambda: colour("--panel"),  # tables, sidebar, notes, analysis panels
    "--frame": lambda: colour("--frame"),  # tiles, KPIs, banners, case detail rows
}
SURFACES = {
    **PAGE_SURFACES,
    # Case row hover and the "all versions" row
    "--accent-hover on --panel": lambda: over("--accent-hover", colour("--panel")),
    # The current navigation link
    "--accent-tint on --panel": lambda: over("--accent-tint", colour("--panel")),
    # The skipped-step cell at its strongest
    "skipped-step heat at its cap on --panel": skipped_step_cap,
    **{token: (lambda token=token: colour(token)) for token in HEAT_TOKENS},
    # A flagged dangerous matrix cell: the tint over each off-diagonal heat
    **{
        f"--danger-tint on {token}": (lambda token=token: over("--danger-tint", colour(token)))
        for token in HEAT_TOKENS
        if "off" in token
    },
}

TEXT_PAIRS = [
    # Every text token on every page surface
    *(
        (token, surface)
        for token in ("--text", "--muted", "--accent-text", "--danger", "--severity-warning")
        for surface in PAGE_SURFACES
    ),
    # Body and secondary text on hover, tint and heat backgrounds
    *(
        (token, surface)
        for token in ("--text", "--muted")
        for surface in (
            "--accent-hover on --panel",
            "--accent-tint on --panel",
            "skipped-step heat at its cap on --panel",
        )
    ),
    # The current and hovered navigation links
    ("--accent-text", "--accent-hover on --panel"),
    ("--accent-text", "--accent-tint on --panel"),
    # Counts in verdict matrix cells, flagged or not
    *(
        ("--text", surface)
        for surface in SURFACES
        if "heat" in surface and "skipped" not in surface
    ),
    # The word on an accent or danger fill, and on the reload button's hover
    ("--on-accent", "--accent"),
    ("--on-accent", "--accent-text"),
    ("--on-danger", "--danger"),
]


@pytest.mark.parametrize(
    ("token", "surface"), TEXT_PAIRS, ids=[f"{token}-on-{surface}" for token, surface in TEXT_PAIRS]
)
def test_text_meets_4_5_to_1_on_its_surface(token: str, surface: str) -> None:
    background = SURFACES[surface]() if surface in SURFACES else colour(surface)
    assert contrast(colour(token), background) >= TEXT


SERIES = [
    "--s1",
    "--s2",
    "--s3",
    "--s4",
    "--s5",
    "--s6",
    "--s-other",
    "--s-none",
    "--s-all",
    "--s-all-halo",
]


@pytest.mark.parametrize("token", SERIES)
def test_series_colour_meets_3_to_1_on_the_panel(token: str) -> None:
    assert contrast(colour(token), colour("--panel")) >= GRAPHIC


@pytest.mark.parametrize(
    "token", ["--verdict-true-positive", "--verdict-false-positive", "--verdict-benign"]
)
def test_verdict_pill_border_meets_3_to_1_on_the_panel(token: str) -> None:
    assert contrast(colour(token), colour("--panel")) >= GRAPHIC


@pytest.mark.parametrize("surface", [*PAGE_SURFACES, *HEAT_TOKENS])
def test_focus_ring_meets_3_to_1_on_every_surface(surface: str) -> None:
    background = SURFACES[surface]()
    assert contrast(colour("--focus"), background) >= GRAPHIC


def test_color_scheme_is_dark() -> None:
    assert tokens()["color-scheme"] == "dark"


def _rules(path: Path) -> list[tuple[list[str], dict[str, str]]]:
    """Every innermost rule as (selectors, declarations), media blocks flattened."""
    css = _strip_comments(path.read_text("utf-8"))
    found = []
    for selectors, body in re.findall(r"([^{}]+)\{([^{}]*)\}", css):
        pairs = (part.split(":", 1) for part in body.split(";") if ":" in part)
        found.append(
            (
                [s.strip() for s in selectors.split(",")],
                {name.strip(): value.strip() for name, value in pairs},
            )
        )
    return found


def _declarations(selector: str) -> dict[str, str]:
    merged: dict[str, str] = {}
    for path in COMPONENT_STYLES:
        for selectors, declarations in _rules(path):
            if selector in selectors:
                merged.update(declarations)
    return merged


def test_a_focus_visible_rule_draws_the_focus_ring() -> None:
    assert _declarations(":focus-visible")["outline"] == "var(--focus-ring)"


def test_the_focus_ring_is_at_least_2px_solid() -> None:
    assert re.fullmatch(r"([2-9]|\d{2,})px solid var\(--focus\)", tokens()["--focus-ring"])


@pytest.mark.parametrize("path", ALL_STYLES, ids=lambda path: path.name)
def test_no_rule_removes_the_outline_without_a_replacement(path: Path) -> None:
    removed = [
        selectors
        for selectors, declarations in _rules(path)
        if declarations.get("outline") in {"none", "0"} and "box-shadow" not in declarations
    ]
    assert removed == []


def _reduced_motion_block() -> str:
    css = _strip_comments(BASE.read_text("utf-8"))
    start = css.index("@media (prefers-reduced-motion: reduce)")
    depth = 0
    for index in range(css.index("{", start), len(css)):
        depth += {"{": 1, "}": -1}.get(css[index], 0)
        if depth == 0:
            return css[start : index + 1]
    raise AssertionError("unclosed prefers-reduced-motion block")


@pytest.mark.parametrize("property_name", ["transition-duration", "animation-duration"])
def test_reduced_motion_shortens_every_transition_and_animation(property_name: str) -> None:
    block = _reduced_motion_block()
    assert re.search(rf"\*\s*,.*?{property_name}:\s*0\.01ms", block, re.S)


# Every control a reader can press, type into or follow, with the sizes it must keep.
TOUCH_TARGETS = [
    (".sidebar-link", ("min-width", "min-height")),  # navigation links
    (".class-tab", ("min-height",)),  # class tabs
    (".class-select select", ("min-height",)),  # class dropdown
    (".case-filter select", ("min-height",)),  # case filters
    (".case-filter input", ("min-height",)),  # case search
    (".case-toggle", ("min-height",)),  # case row buttons
    (".trend-table-toggle", ("min-width", "min-height")),  # chart table toggle
    (".status-bar-reload", ("min-height",)),  # reload button
    (".overview-tile", ("min-height",)),  # overview links
    (".overview-alert a", ("min-height",)),  # overview alert link
]


@pytest.mark.parametrize(
    ("selector", "property_name"),
    [(selector, name) for selector, names in TOUCH_TARGETS for name in names],
)
def test_control_keeps_the_touch_target_size(selector: str, property_name: str) -> None:
    assert _declarations(selector)[property_name] == "var(--touch-target)"


def test_the_touch_target_token_is_at_least_44px() -> None:
    assert int(tokens()["--touch-target"].removesuffix("px")) >= MINIMUM_TOUCH_TARGET_PX


def test_the_case_row_height_token_equals_the_windowing_constant() -> None:
    constant = re.search(r"ROW_HEIGHT_PX\s*=\s*(\d+)", CASE_WINDOW.read_text("utf-8"))
    assert constant is not None
    assert tokens()["--case-row-height"] == f"{constant[1]}px"


def test_the_heatmap_cell_tint_is_capped_by_the_cap_token() -> None:
    assert "var(--heat-skip-cap)" in _declarations(".heatmap-cell")["background"]


RAW_COLOUR = re.compile(
    r"#[0-9a-fA-F]{3,8}\b|\b(?:rgba?|hsla?|hwb|lab|lch|oklab|oklch|color)\(", re.I
)


@pytest.mark.parametrize("path", COMPONENT_STYLES, ids=lambda path: path.name)
def test_component_styles_have_no_raw_colour_literals(path: Path) -> None:
    assert RAW_COLOUR.findall(_strip_comments(path.read_text("utf-8"))) == []


@pytest.mark.parametrize("path", ALL_STYLES, ids=lambda path: path.name)
def test_styles_use_no_important_outside_the_reduced_motion_block(path: Path) -> None:
    css = _strip_comments(path.read_text("utf-8"))
    if path == BASE:
        css = css.replace(_reduced_motion_block(), "")
    assert "!important" not in css
