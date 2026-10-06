"""Every leaf field of the dashboard view is shown on the page that displays it.

`PAGE_OF` maps each field to the route that shows it; `SKIPPED` lists the fields the page reads
but does not print, each with its reason. A new view field must join one of them, or
`test_every_view_field_is_mapped` fails without a browser. The browser tests open each route of
each variant page and look for every mapped value in the visible text:

    uv run --with playwright pytest -m browser -q tests/test_view_coverage.py

A path names a field from the view's root: `.` between fields, `[]` for each item of a list and
`{}` for each value of a mapping. Fields under `classes[]` are checked with their class selected.
"""

import copy
import json
import re
from collections import defaultdict
from collections.abc import Iterator, Mapping
from dataclasses import fields, is_dataclass
from functools import cache
from pathlib import Path
from types import UnionType
from typing import Any, Union, get_args, get_origin, get_type_hints

import pytest
from test_dashboard_view import (
    SERVED,
    WAITING_COUNTS,
    WAITING_NOTE,
    class_data,
    completeness,
    metrics,
    no_checklist_class,
    skipped,
    trend_point,
    unmapped_label_note,
    version_entry,
)

from detecttrace.dashboard import render_dashboard, render_waiting_page, write_dashboard
from detecttrace.dashboard_view import (
    DashboardView,
    build_served_view,
    build_view,
    build_waiting_view,
    to_view_json,
)

ROOT = Path(__file__).resolve().parent.parent
PAGE_SOURCE = ROOT / "dashboard" / "src"

# The route that shows each field.
PAGE_OF = {
    "header.title": "/",
    "header.sources[].name": "/",
    "header.sources[].path": "/",
    "header.cases_text": "/",
    "header.period_text": "/",
    "header.versions_text": "/",
    "header.low_coverage_text": "/",
    "header.generator_text": "/",
    "header.footer_text": "/",
    "classes[].name": "/",
    "classes[].cases_text": "/versions",
    "classes[].versions_text": "/versions",
    "classes[].pooled_note": "/versions",
    "classes[].rows[].label": "/versions",
    "classes[].rows[].cases_text": "/versions",
    "classes[].rows[].few_note": "/versions",
    "classes[].rows[].completeness.value": "/versions",
    "classes[].rows[].completeness.interval": "/versions",
    "classes[].rows[].completeness.note": "/versions",
    "classes[].rows[].completeness.n_text": "/versions",
    "classes[].rows[].completeness.few_note": "/versions",
    "classes[].rows[].agreement.value": "/versions",
    "classes[].rows[].agreement.interval": "/versions",
    "classes[].rows[].agreement.note": "/versions",
    "classes[].rows[].agreement.n_text": "/versions",
    "classes[].rows[].agreement.few_note": "/versions",
    "classes[].rows[].kappa.value": "/versions",
    "classes[].rows[].kappa.interval": "/versions",
    "classes[].rows[].kappa.note": "/versions",
    "classes[].rows[].kappa.n_text": "/versions",
    "classes[].rows[].kappa.few_note": "/versions",
    "classes[].rows[].dangerous_text": "/versions",
    "classes[].rows[].tp_without_agent_text": "/versions",
    "classes[].skipped.steps_text": "/skipped",
    "classes[].skipped.columns[].label": "/skipped",
    "classes[].skipped.columns[].n_text": "/skipped",
    "classes[].skipped.columns[].few_note": "/skipped",
    "classes[].skipped.rows[].item": "/skipped",
    "classes[].skipped.rows[].cells[].rate_text": "/skipped",
    "classes[].skipped.rows[].cells[].count_text": "/skipped",
    "classes[].skipped.rows[].cells[].few_note": "/skipped",
    "classes[].skipped.empty_text": "/skipped",
    "classes[].trend.weeks[]": "/trends",
    "classes[].trend.period_text": "/trends",
    "classes[].trend.completeness.title": "/trends",
    "classes[].trend.completeness.lines[].label": "/trends",
    "classes[].trend.completeness.lines[].counts[]": "/trends",
    "classes[].trend.completeness.table_rows[].week": "/trends",
    "classes[].trend.completeness.table_rows[].cells[]": "/trends",
    "classes[].trend.completeness.empty_text": "/trends",
    "classes[].trend.agreement.title": "/trends",
    "classes[].trend.agreement.lines[].label": "/trends",
    "classes[].trend.agreement.lines[].counts[]": "/trends",
    "classes[].trend.agreement.table_rows[].week": "/trends",
    "classes[].trend.agreement.table_rows[].cells[]": "/trends",
    "classes[].trend.agreement.empty_text": "/trends",
    "classes[].trend.version_first_weeks{}": "/trends",
    "classes[].trend.few_legend_text": "/trends",
    "classes[].confusion.n_text": "/verdicts",
    "classes[].confusion.dangerous_text": "/verdicts",
    "classes[].confusion.column_labels[]": "/verdicts",
    "classes[].confusion.rows[].label": "/verdicts",
    "classes[].confusion.rows[].cells[].count_text": "/verdicts",
    "cases.total_text": "/cases",
    "cases.detail_sentence": "/cases",
    "cases.class_filters[].label": "/cases",
    "coverage[].text": "/data",
    "coverage[].hint": "/data",
    "notes[].severity_label": "/data",
    "notes[].count_text": "/data",
    "notes[].message": "/data",
    "notes[].hint": "/data",
    "notes[].examples[].subject": "/data",
    "notes[].examples[].detail": "/data",
    "notes[].more_text": "/data",
    "limits[].term": "/limits",
    "limits[].text": "/limits",
    "served.held_back_text": "/",
    "waiting.counts[].label": "/",
    "waiting.counts[].value": "/",
    "waiting.notes[].message": "/",
    "waiting.notes[].hint": "/",
    "waiting.next_step_text": "/",
}

# Fields the page reads without printing them. Each one's name must appear in the page code.
SKIPPED = {
    "view_version": "checked so the page refuses a view it cannot read",
    "mode": "marks the Data page, where a ui page will add its setup steps",
    "classes[].anchor": "names the selected class in the hash and in element ids",
    "classes[].rows[].style": "picks the row's series colour, dash and marker shape",
    "classes[].rows[].is_all": "styles the all-versions row",
    "classes[].rows[].is_dangerous": "puts the warning icon on the dangerous count",
    "classes[].rows[].completeness.strip.range_x": "interval strip geometry",
    "classes[].rows[].completeness.strip.range_width": "interval strip geometry",
    "classes[].rows[].completeness.strip.point_left": "interval strip geometry",
    "classes[].rows[].completeness.strip.point_width": "interval strip geometry",
    "classes[].rows[].agreement.strip.range_x": "interval strip geometry",
    "classes[].rows[].agreement.strip.range_width": "interval strip geometry",
    "classes[].rows[].agreement.strip.point_left": "interval strip geometry",
    "classes[].rows[].agreement.strip.point_width": "interval strip geometry",
    "classes[].rows[].kappa.strip.range_x": "interval strip geometry",
    "classes[].rows[].kappa.strip.range_width": "interval strip geometry",
    "classes[].rows[].kappa.strip.point_left": "interval strip geometry",
    "classes[].rows[].kappa.strip.point_width": "interval strip geometry",
    "classes[].skipped.columns[].style": "picks the column's series swatch",
    "classes[].skipped.rows[].cells[].bar_width": "sets the cell's tint; rate_text is the text",
    "classes[].trend.completeness.lines[].style": "picks the series colour, dash and marker",
    "classes[].trend.completeness.lines[].values[]": "plotted as heights; table cells are the text",
    "classes[].trend.completeness.lines[].few[]": "draws a hollow marker; the legend says why",
    "classes[].trend.agreement.lines[].style": "picks the series colour, dash and marker",
    "classes[].trend.agreement.lines[].values[]": "plotted as heights; table cells are the text",
    "classes[].trend.agreement.lines[].few[]": "draws a hollow marker; the legend says why",
    "classes[].confusion.rows[].cells[].heat": "sets the cell's background",
    "classes[].confusion.rows[].cells[].is_dangerous": "outlines the cell and labels it",
    "classes[].confusion.rows[].cells[].is_flagged": "adds the heavier outline and icon",
    "cases.class_filters[].class_index": "the case rows' class code the filter matches",
    "cases.class_filters[].anchor": "the class filter's value in the hash",
    "coverage[].is_low": "adds the warning label and styling",
    "notes[].severity": "styles the note; severity_label is the text",
    "served.generation": "compared with the server's status to offer a reload",
    "served.updated_at": "compared with the server's status to offer a reload",
}


def to_field_paths(hint: Any, path: str) -> list[str]:
    """The leaf paths under a view type, with `[]` for list items and `{}` for mapping values."""
    if is_dataclass(hint):
        hints = get_type_hints(hint)
        return [
            leaf
            for field in fields(hint)
            for leaf in to_field_paths(hints[field.name], _join(path, field.name))
        ]
    origin = get_origin(hint)
    args = [arg for arg in get_args(hint) if arg is not type(None)]
    if origin in (Union, UnionType):
        return to_field_paths(args[0], path)
    if origin is tuple:
        return to_field_paths(args[0], f"{path}[]")
    if origin is Mapping:
        return to_field_paths(args[1], f"{path}{{}}")
    return [path]


def _join(path: str, name: str) -> str:
    return f"{path}.{name}" if path else name


# `to_view_json` adds the version before the dataclass fields.
VIEW_FIELDS = frozenset({"view_version", *to_field_paths(DashboardView, "")})


def test_every_view_field_is_mapped() -> None:
    assert sorted(VIEW_FIELDS - PAGE_OF.keys() - SKIPPED.keys()) == []


def test_every_mapped_field_is_a_view_field() -> None:
    assert sorted((PAGE_OF.keys() | SKIPPED.keys()) - VIEW_FIELDS) == []


def test_no_field_is_both_mapped_and_skipped() -> None:
    assert sorted(PAGE_OF.keys() & SKIPPED.keys()) == []


@cache
def page_code() -> str:
    """The page's own TypeScript, without the tests and without the generated view types, which
    name every field."""
    sources = [
        path
        for path in sorted(PAGE_SOURCE.rglob("*.ts*"))
        if ".test." not in path.name and path.name not in {"view.d.ts", "test-fixtures.ts"}
    ]
    return "\n".join(path.read_text(encoding="utf-8") for path in sources)


@pytest.mark.parametrize("path", sorted(SKIPPED))
def test_a_skipped_field_is_read_by_the_page_code(path: str) -> None:
    name = re.sub(r"\[\]|\{\}", "", path.rsplit(".", 1)[-1])
    assert re.search(rf"\b{name}\b", page_code())


# The pages whose text is checked: the demo, the demo with every optional field filled, a page
# from `detecttrace serve`, the page it shows while waiting for data, and the pages from
# `detecttrace ui` while waiting and with results.

DEMO_RESULTS = json.loads((ROOT / "tests/fixtures/demo/expected.json").read_text(encoding="utf-8"))


def create_rich_results() -> dict[str, Any]:
    """The demo plus a low-coverage warning, data notes, a class without a checklist and a class
    with few cases and pooled versions, so the optional fields hold text."""
    results = copy.deepcopy(DEMO_RESULTS)
    results["totals"]["coverage"].update(verdicts_total=500, verdicts_low=True)
    first_class = results["classes"][0]
    first_class["overall"]["true_positives_without_agent_verdict"] = ["DT-IT-0001"]
    first_class["by_version"][0]["metrics"]["true_positives_without_agent_verdict"] = ["DT-IT-0001"]
    first_class["overall"]["kappa"]["dropped_resamples"] = 87
    results["data_notes"].append(unmapped_label_note())
    without_checklist = {**no_checklist_class(), "alert_class": "no_checklist_class"}
    few = metrics(
        case_count=5,
        n=5,
        rate=None,
        kappa_value=None,
        kappa_interval=None,
        kappa_note="no_cases",
        completeness_data=completeness(n=5),
    )
    few_entry = {**version_entry("v1", slice_data=few), "skipped": skipped(("signin",), n=5)}
    pooled = class_data("pooled_class", entries=[few_entry], other_versions=("v7", "v8"))
    for extra in (without_checklist, pooled):
        # The week the version first appears, so the chart marks it.
        extra["trend"] = [
            trend_point("2026-W10", "all", None, 0.8, 5),
            trend_point("2026-W10", "version", "v1", 0.8, 5),
        ]
        results["classes"].append(extra)
        results["case_rows"]["strings"].append(extra["alert_class"])
    return results


RICH_RESULTS = create_rich_results()
# name: (view JSON, page HTML)
VARIANTS = {
    "demo": (to_view_json(build_view(DEMO_RESULTS)), lambda: render_dashboard(DEMO_RESULTS)),
    "rich": (to_view_json(build_view(RICH_RESULTS)), lambda: render_dashboard(RICH_RESULTS)),
    "served": (
        to_view_json(build_served_view(DEMO_RESULTS, SERVED)),
        lambda: render_dashboard(DEMO_RESULTS, served=SERVED),
    ),
    "waiting": (
        to_view_json(build_waiting_view(WAITING_COUNTS, [WAITING_NOTE], SERVED)),
        lambda: render_waiting_page(WAITING_COUNTS, [WAITING_NOTE], SERVED),
    ),
    "ui-waiting": (
        to_view_json(build_waiting_view(WAITING_COUNTS, [WAITING_NOTE], SERVED, mode="ui")),
        lambda: render_waiting_page(WAITING_COUNTS, [WAITING_NOTE], SERVED, mode="ui"),
    ),
    "ui": (
        to_view_json(build_served_view(DEMO_RESULTS, SERVED, mode="ui")),
        lambda: render_dashboard(DEMO_RESULTS, served=SERVED, mode="ui"),
    ),
}


def to_leaves(value: object, path: str) -> Iterator[tuple[str, object]]:
    if isinstance(value, dict):
        is_mapping = any(field.startswith(f"{path}{{}}") for field in VIEW_FIELDS)
        for key, item in value.items():
            yield from to_leaves(item, f"{path}{{}}" if is_mapping else _join(path, key))
    elif isinstance(value, list):
        for item in value:
            yield from to_leaves(item, f"{path}[]")
    else:
        yield path, value


def to_expected_text(view: dict[str, Any]) -> dict[str, list[str]]:
    """Each route, with its class selected, and the text the view expects it to show."""
    unscoped = {name: value for name, value in view.items() if name != "classes"}
    leaves = [("", path, value) for path, value in to_leaves(unscoped, "")]
    for class_view in view["classes"]:
        query = f"?class={class_view['anchor']}"
        # A class without a checklist shows its empty text in place of the step count.
        hidden = {"classes[].skipped.steps_text"} if class_view["skipped"]["empty_text"] else set()
        leaves += [
            (query, path, value)
            for path, value in to_leaves(class_view, "classes[]")
            if path not in hidden
        ]
    expected: dict[str, set[str]] = defaultdict(set)
    for query, path, value in leaves:
        if path in PAGE_OF and value not in (None, ""):
            expected[PAGE_OF[path] + query].add(str(value))
    return {route: sorted(texts) for route, texts in sorted(expected.items())}


ROUTE_CHECKS = [
    (variant, route, texts)
    for variant, (view, _) in VARIANTS.items()
    for route, texts in to_expected_text(view).items()
]


def normalize(text: str) -> str:
    # innerText applies text-transform, so case is ignored; line breaks are layout, not text.
    return " ".join(text.split()).casefold()


@pytest.fixture(scope="module")
def browser() -> Iterator[Any]:
    # Playwright is not a project dependency, so Pyright can't see its types.
    sync_api: Any = pytest.importorskip(
        "playwright.sync_api",
        reason="Playwright is not installed; run `uv run --with playwright pytest -m browser`",
    )
    with sync_api.sync_playwright() as playwright:
        browser = playwright.chromium.launch()
        yield browser
        browser.close()


@pytest.fixture(scope="module")
def page_paths(tmp_path_factory: pytest.TempPathFactory) -> dict[str, Path]:
    folder = tmp_path_factory.mktemp("view-coverage")
    paths = {}
    for variant, (_, render) in VARIANTS.items():
        paths[variant] = folder / f"{variant}.html"
        write_dashboard(render(), paths[variant])
    return paths


def read_visible_text(browser: Any, path: Path, route: str) -> str:
    """The page's visible text at `route`, with every trend table opened."""
    page = browser.new_page()
    try:
        page.goto(f"{path.as_uri()}#{route}")
        page.wait_for_selector("h1")
        for toggle in page.locator(".trend-table-toggle").all():
            toggle.click()
        return page.inner_text("body")
    finally:
        page.close()


@pytest.mark.browser
@pytest.mark.parametrize(
    ("variant", "route", "texts"),
    ROUTE_CHECKS,
    ids=[f"{variant}-{route}" for variant, route, _ in ROUTE_CHECKS],
)
def test_the_route_shows_every_view_value_it_maps(
    browser: Any, page_paths: dict[str, Path], variant: str, route: str, texts: list[str]
) -> None:
    shown = normalize(read_visible_text(browser, page_paths[variant], route))
    assert [text for text in texts if normalize(text) not in shown] == []
