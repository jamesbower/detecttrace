"""Stylesheet rules whose loss would go unnoticed by every rendering test."""

import re
from functools import cache
from pathlib import Path

import pytest

STYLESHEET = Path(__file__).resolve().parent.parent / "src/detecttrace/templates/dashboard.css"
FORCED_COLORS = "(forced-colors:active)"


@cache
def stylesheet() -> str:
    return re.sub(r"/\*.*?\*/", "", STYLESHEET.read_text(encoding="utf-8"), flags=re.S)


def declarations(selector: str, media: str | None = None) -> dict[str, str]:
    """The merged declarations of every rule listing `selector`, at the top level or in the
    `@media` block with that exact condition. Later declarations win, as in the cascade."""
    merged: dict[str, str] = {}
    for selectors, body in re.findall(r"([^{}]+)\{([^{}]*)\}", _scope(media)):
        if selector in [part.strip() for part in selectors.split(",")]:
            merged.update(_parse_declarations(body))
    return merged


def _scope(media: str | None) -> str:
    media_block = r"@media\s*([^{]*)\{((?:[^{}]*\{[^{}]*\})*[^{}]*)\}"
    if media is None:
        return re.sub(media_block, "", stylesheet())
    blocks = re.findall(media_block, stylesheet())
    return next(body for condition, body in blocks if condition.strip() == media)


def _parse_declarations(body: str) -> dict[str, str]:
    pairs = (part.split(":", 1) for part in body.split(";") if ":" in part)
    return {name.strip(): value.strip() for name, value in pairs}


def test_a_dangerous_confusion_cell_has_an_outline() -> None:
    assert declarations(".cm td.is-danger")["outline"] == "2px solid var(--danger)"


def test_a_value_with_few_cases_is_italic() -> None:
    assert declarations(".is-few .val")["font-style"] == "italic"


@pytest.mark.parametrize("selector", [".row-toggle", ".call-tool", ".call-args"])
def test_long_unbroken_text_wraps(selector: str) -> None:
    assert declarations(selector)["overflow-wrap"] == "anywhere"


def test_the_case_table_class_column_wraps() -> None:
    assert declarations(".cases td.mono")["white-space"] == "normal"


def test_the_case_table_class_column_breaks_anywhere() -> None:
    assert declarations(".cases td.mono")["overflow-wrap"] == "anywhere"


def test_a_case_detail_is_no_wider_than_its_table_frame() -> None:
    assert declarations(".detail-body")["max-width"] == "100cqi"


def test_the_table_frame_is_the_width_container() -> None:
    assert declarations(".tbl-wrap")["container-type"] == "inline-size"


@pytest.mark.parametrize("selector", [".series-all", ".sw-all"])
def test_the_all_versions_halo_uses_its_contrast_token(selector: str) -> None:
    assert declarations(selector)["stroke"] == "var(--s-all-halo)"


@pytest.mark.parametrize("selector", [".series-all", ".sw-all"])
def test_the_all_versions_halo_is_opaque(selector: str) -> None:
    assert "opacity" not in declarations(selector)


def test_grid_lines_use_the_rule_token() -> None:
    assert declarations(".grid-line")["stroke"] == "var(--faint)"


@pytest.mark.parametrize(
    ("selector", "prop", "value"),
    [
        (".axis-text", "fill", "CanvasText"),
        (".n-text", "fill", "CanvasText"),
        (".vmark-text", "fill", "CanvasText"),
        (".grid-line", "stroke", "GrayText"),
        (".vmark", "stroke", "GrayText"),
        (".ci-track", "fill", "GrayText"),
        (".bar-track", "fill", "GrayText"),
        (".ci-point", "fill", "CanvasText"),
        (".series-all", "stroke", "CanvasText"),
        (".mk.c-all", "fill", "CanvasText"),
        (".mk.is-few-point", "fill", "Canvas"),
    ],
)
def test_forced_colors_keep_chart_parts_visible(selector: str, prop: str, value: str) -> None:
    assert declarations(selector, media=FORCED_COLORS)[prop] == value
