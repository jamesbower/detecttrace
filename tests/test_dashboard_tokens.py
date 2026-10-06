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


def share(name: str) -> float:
    """A percentage token as a fraction, following var() chains."""
    value = tokens()[name]
    if var := re.fullmatch(r"var\((--[\w-]+)\)", value):
        return share(var[1])
    return int(value.removesuffix("%")) / 100


def heat_at(level: str) -> Rgb:
    """A heat cell: --heat mixed into --panel at the share the `level` token gives."""
    amount = share(level)
    return tuple(  # type: ignore[return-value]
        a * amount + p * (1 - amount)
        for a, p in zip(colour("--heat"), colour("--panel"), strict=True)
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
# The verdict matrix's four heat levels; a cell with no cases is bare --panel.
HEAT_TOKENS = [f"--heat-{n}" for n in range(1, 5)]
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
    "skipped-step heat at its cap on --panel": lambda: heat_at("--heat-cap"),
    **{token: (lambda token=token: heat_at(token)) for token in HEAT_TOKENS},
    # A flagged dangerous matrix cell: the tint over each heat level
    **{
        f"--danger-tint on {token}": (lambda token=token: over("--danger-tint", heat_at(token)))
        for token in HEAT_TOKENS
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


VERDICT_PILLS = ["--verdict-true-positive", "--verdict-false-positive", "--verdict-benign"]
# A pill sits in a case row: on --panel, on a hovered row, and in an open case's --frame.
PILL_SURFACES = ["--panel", "--accent-hover on --panel", "--frame"]


@pytest.mark.parametrize(
    ("token", "surface"), [(token, surface) for token in VERDICT_PILLS for surface in PILL_SURFACES]
)
def test_verdict_pill_border_meets_3_to_1_on_its_surface(token: str, surface: str) -> None:
    assert contrast(colour(token), SURFACES[surface]()) >= GRAPHIC


@pytest.mark.parametrize("token", VERDICT_PILLS)
def test_verdict_pills_have_colours_of_their_own(token: str) -> None:
    assert re.fullmatch(r"#[0-9a-fA-F]{6}", tokens()[token])


@pytest.mark.parametrize("surface", HEAT_TOKENS)
def test_the_dangerous_outline_meets_3_to_1_on_every_heat_level(surface: str) -> None:
    assert contrast(colour("--danger"), SURFACES[surface]()) >= GRAPHIC


@pytest.mark.parametrize("surface", PAGE_SURFACES)
def test_control_border_meets_3_to_1_on_every_page_surface(surface: str) -> None:
    assert contrast(colour("--control-border"), SURFACES[surface]()) >= GRAPHIC


# Every control whose edge shows where to press or type.
CONTROL_EDGES = [
    ".class-tab",
    ".class-select select",
    ".case-filter select",
    ".case-filter input",
    ".trend-table-toggle",
]


@pytest.mark.parametrize("selector", CONTROL_EDGES)
def test_a_control_draws_its_edge_in_the_control_border(selector: str) -> None:
    assert _declarations(selector)["border"] == "var(--border-thin) solid var(--control-border)"


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


def test_the_focus_ring_is_solid_in_the_focus_colour() -> None:
    assert tokens()["--focus-ring"] == "var(--focus-width) solid var(--focus)"


def test_the_focus_ring_is_at_least_2px_wide() -> None:
    assert int(tokens()["--focus-width"].removesuffix("px")) >= 2


@pytest.mark.parametrize("path", ALL_STYLES, ids=lambda path: path.name)
def test_no_rule_removes_the_outline_without_a_replacement(path: Path) -> None:
    removed = [
        selectors
        for selectors, declarations in _rules(path)
        if declarations.get("outline") in {"none", "0"}
        and "box-shadow" not in declarations
        and not any(selector in RING_REPLACEMENTS for selector in selectors)
    ]
    assert removed == []


# Outlines removed for a mark drawn by another rule: the selector, and the rule that draws it.
RING_REPLACEMENTS = {".trend-point:focus-visible": ".trend-point:focus-visible .trend-point-ring"}


@pytest.mark.parametrize(("removed", "replacement"), RING_REPLACEMENTS.items())
def test_a_removed_outline_is_replaced_by_a_focus_coloured_ring(
    removed: str, replacement: str
) -> None:
    assert _scoped_declarations(replacement)["stroke"] == "var(--focus)"


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
    (".sidebar-link", ("min-height",)),  # navigation links; their width is tested below
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


def test_a_navigation_link_is_44px_wide_unless_eight_would_not_fit() -> None:
    assert tokens()["--nav-link-min-width"] == "min(var(--touch-target), 12.5vw)"


def test_navigation_links_share_the_bar_at_the_link_minimum() -> None:
    assert _scoped_declarations(".sidebar-links > li")["min-width"] == "var(--nav-link-min-width)"


def test_focus_scrolls_an_element_clear_of_the_bottom_bar() -> None:
    assert _scoped_declarations("html")["scroll-padding-bottom"] == (
        "calc(var(--bottom-bar-height) + var(--sp-3))"
    )


def test_the_bottom_bar_is_its_token_high() -> None:
    assert _scoped_declarations(".sidebar")["height"] == "var(--bottom-bar-height)"


def test_the_touch_target_token_is_at_least_44px() -> None:
    assert int(tokens()["--touch-target"].removesuffix("px")) >= MINIMUM_TOUCH_TARGET_PX


def test_the_case_row_height_token_equals_the_windowing_constant() -> None:
    constant = re.search(r"ROW_HEIGHT_PX\s*=\s*(\d+)", CASE_WINDOW.read_text("utf-8"))
    assert constant is not None
    assert tokens()["--case-row-height"] == f"{constant[1]}px"


def test_the_heatmap_cell_tint_is_capped_by_the_cap_token() -> None:
    assert "var(--heat-cap)" in _declarations(".heatmap-cell")["background"]


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


# Rules whose loss no rendering test would notice.

MEDIA_BLOCK = re.compile(r"@media\s*([^{]*)\{((?:[^{}]*\{[^{}]*\})*[^{}]*)\}")
FORCED_COLORS = "(forced-colors: active)"


def _scoped_declarations(selector: str, media: str | None = None) -> dict[str, str]:
    """The merged declarations for `selector` at the top level or, with `media`, only inside the
    `@media` blocks with that exact condition. Later declarations win, as in the cascade."""
    merged: dict[str, str] = {}
    for path in COMPONENT_STYLES:
        css = _strip_comments(path.read_text("utf-8"))
        if media is None:
            scopes = [MEDIA_BLOCK.sub("", css)]
        else:
            scopes = [
                body for condition, body in MEDIA_BLOCK.findall(css) if condition.strip() == media
            ]
        for scope in scopes:
            for selectors, body in re.findall(r"([^{}]+)\{([^{}]*)\}", scope):
                if selector in [part.strip() for part in selectors.split(",")]:
                    pairs = (part.split(":", 1) for part in body.split(";") if ":" in part)
                    merged.update({name.strip(): value.strip() for name, value in pairs})
    return merged


def test_a_dangerous_matrix_cell_has_an_outline() -> None:
    assert (
        _scoped_declarations(".matrix-cell.is-dangerous")["outline"]
        == "var(--outline-danger) solid var(--danger)"
    )


@pytest.mark.parametrize("selector", [".version-table tbody th", ".heatmap-table tbody th"])
def test_a_row_label_breaks_between_words(selector: str) -> None:
    assert _scoped_declarations(selector)["overflow-wrap"] == "break-word"


@pytest.mark.parametrize("selector", [".version-table tbody th", ".heatmap-table tbody th"])
def test_a_row_label_keeps_its_minimum_width(selector: str) -> None:
    assert _scoped_declarations(selector)["min-width"] == "var(--label-min)"


@pytest.mark.parametrize("selector", [".version-table-interval", ".version-table-n", ".heatmap-of"])
def test_intervals_and_counts_in_dense_tables_are_extra_small_not_smaller(selector: str) -> None:
    assert _scoped_declarations(selector)["font-size"] == "var(--fs-xs)"


def test_a_trend_chart_stops_growing_at_its_maximum_width() -> None:
    assert _scoped_declarations(".trend-chart-svg")["max-width"] == "var(--trend-chart-max-width)"


def test_a_skipped_step_rate_with_few_cases_is_italic() -> None:
    assert _scoped_declarations(".is-few .heatmap-rate")["font-style"] == "italic"


@pytest.mark.parametrize("selector", [".case-call-tool", ".case-call-args"])
def test_long_unbroken_tool_call_text_wraps(selector: str) -> None:
    assert _scoped_declarations(selector)["overflow-wrap"] == "anywhere"


def test_a_case_detail_is_no_wider_than_its_table_frame() -> None:
    assert _scoped_declarations(".case-detail")["max-width"] == "100cqi"


def test_the_case_table_frame_is_the_width_container() -> None:
    assert _scoped_declarations(".case-table-scroll")["container-type"] == "inline-size"


def test_the_all_versions_band_uses_its_contrast_token() -> None:
    assert _scoped_declarations(".series-line-all")["stroke"] == "var(--s-all-halo)"


def test_the_all_versions_band_is_opaque() -> None:
    assert "opacity" not in _scoped_declarations(".series-line-all")


def test_trend_grid_lines_use_the_line_token() -> None:
    assert _scoped_declarations(".trend-grid")["stroke"] == "var(--line)"


@pytest.mark.parametrize(
    ("selector", "property_name", "value"),
    [
        (".trend-axis", "fill", "CanvasText"),
        (".trend-count", "fill", "CanvasText"),
        (".trend-vmark-text", "fill", "CanvasText"),
        (".trend-grid", "stroke", "GrayText"),
        (".trend-vmark", "stroke", "GrayText"),
        (".strip-track", "fill", "GrayText"),
        (".strip-range", "fill", "CanvasText"),
        (".strip-point", "fill", "Canvas"),
        (".series-line", "stroke", "CanvasText"),
        (".series-line-all", "stroke", "CanvasText"),
        (".series-marker", "fill", "CanvasText"),
        (".series-marker", "stroke", "CanvasText"),
        (".series-marker.series-all", "fill", "CanvasText"),
        (".series-marker.is-few", "fill", "Canvas"),
        (".trend-point:focus-visible .trend-point-ring", "stroke", "Highlight"),
        (".trend-point:focus-visible", "outline-color", "Highlight"),
    ],
)
def test_forced_colors_keep_chart_parts_visible(
    selector: str, property_name: str, value: str
) -> None:
    assert _scoped_declarations(selector, media=FORCED_COLORS)[property_name] == value


@pytest.mark.parametrize(
    ("selector", "property_name", "value"),
    [
        (".sidebar-link", "border-color", "Canvas"),
        ('.sidebar-link[aria-current="page"]', "border-color", "Highlight"),
        ('.sidebar-link[aria-current="page"]', "font-weight", "var(--fw-bold)"),
        ('.sidebar-link[aria-current="page"]', "text-decoration", "underline"),
        ('.class-tab[aria-selected="true"]', "forced-color-adjust", "none"),
        ('.class-tab[aria-selected="true"]', "background", "Highlight"),
        ('.class-tab[aria-selected="true"]', "color", "HighlightText"),
    ],
)
def test_forced_colors_mark_the_current_page_and_the_selected_class(
    selector: str, property_name: str, value: str
) -> None:
    assert _scoped_declarations(selector, media=FORCED_COLORS)[property_name] == value


def test_a_panel_cuts_its_corner() -> None:
    assert _scoped_declarations(".panel")["clip-path"] == "var(--clip-angled-md)"


def test_a_panel_draws_its_edge_along_the_cut() -> None:
    assert _scoped_declarations(".panel::after")["background"] == "var(--panel-edge)"


def test_a_low_coverage_line_recolours_its_whole_panel_edge() -> None:
    assert (
        _scoped_declarations('.coverage-line[data-low="true"]')["--panel-edge"]
        == "var(--severity-warning)"
    )


def test_matrix_cells_use_the_one_heat_ramp() -> None:
    assert _scoped_declarations(".matrix-cell")["background"] == (
        "color-mix(in srgb, var(--heat) var(--matrix-heat, 0%), var(--panel))"
    )


TABLE_HEADERS = [
    ".version-table thead th",
    ".case-table th",
    ".matrix th",
    ".heatmap-table th",
    ".trend-table thead th",
]


@pytest.mark.parametrize("selector", TABLE_HEADERS)
def test_every_table_header_is_extra_small(selector: str) -> None:
    assert _scoped_declarations(selector)["font-size"] == "var(--fs-xs)"


@pytest.mark.parametrize("selector", TABLE_HEADERS)
def test_every_table_header_is_in_sentence_case(selector: str) -> None:
    assert "text-transform" not in _scoped_declarations(selector)


@pytest.mark.parametrize("property_name", ["text-transform", "letter-spacing"])
def test_a_class_name_keeps_its_case_and_spacing(property_name: str) -> None:
    assert _scoped_declarations(".class-name")[property_name] in {"none", "normal"}


def test_a_dangerous_false_close_in_the_case_table_is_neutral_text() -> None:
    assert _scoped_declarations('.case-result[data-result="dangerous"]')["color"] == "var(--text)"


def test_a_skipped_step_label_sits_in_the_middle_of_its_row() -> None:
    assert _scoped_declarations(".heatmap-table tbody th")["vertical-align"] == "middle"


def test_the_page_heading_ring_hugs_its_words() -> None:
    assert _scoped_declarations(".page-head-title")["width"] == "fit-content"
