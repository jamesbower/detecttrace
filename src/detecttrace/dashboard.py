"""The HTML dashboard: one self-contained page built from a results object.

The page holds exactly one inline stylesheet and one inline script, both allowed by the SHA-256
hashes in its Content-Security-Policy, and the full results object as a non-executed JSON block
that the case-table script reads. Every other value is rendered by an autoescaping template.

A page served by `detecttrace serve` adds a second inline script, which asks the same server
whether newer results exist; its policy allows that one request target and nothing else.
"""

import base64
import hashlib
import json
import re
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from importlib.resources import files
from pathlib import Path

from jinja2 import Environment, PackageLoader, StrictUndefined
from markupsafe import Markup

from detecttrace import __version__
from detecttrace.charts import SHAPE_BY_STYLE, Chart, SeriesInput, create_marker, trend_chart
from detecttrace.dashboard_view import (
    GENERATOR_PREFIX,
    TrendMetricView,
    TrendView,
    build_view,
    format_count,
    format_held_back_text,
)
from detecttrace.files import MARKER_READ_BYTES, read_head, write_text_atomically
from detecttrace.served_page import ServedPage, WaitingCounts
from detecttrace.summary import SummaryLine, to_visible_text

_DOCTYPE = b"<!DOCTYPE html>"
_HEAD_END = b"</head>"
# The prefix, one space, then a version; "detecttrace-like" or a bare prefix is someone else's.
_MARKER = re.compile(
    rb'<meta name="generator" content="' + re.escape(GENERATOR_PREFIX.encode()) + rb' [^"]+">'
)
_TEMPLATE_NAME = "dashboard.html.j2"
_WAITING_TEMPLATE_NAME = "waiting.html.j2"
# Standard JSON leaves these as they are. Inside <script>, "</script>" or "<!--" in any string
# would end or change the block, and U+2028/U+2029 end a line in older JavaScript parsers.
_JSON_ESCAPES = {
    "<": "\\u003c",
    ">": "\\u003e",
    "&": "\\u0026",
    "\u2028": "\\u2028",
    "\u2029": "\\u2029",
}
_JSON_ESCAPE_TABLE = str.maketrans(_JSON_ESCAPES)
_SWATCH_CENTER = (15.0, 6.0)
_SWATCH_RADIUS = 4.0


@dataclass(frozen=True, slots=True)
class _NoteText:
    message: str
    hint: str


@dataclass(frozen=True, slots=True)
class _TrendCharts:
    completeness: Chart | None  # None when the class has no checklist
    agreement: Chart


def render_dashboard(results: Mapping[str, object], *, served: ServedPage | None = None) -> str:
    """Render the page for a results object from `results.build_results` (or its JSON).

    With `served`, the page is for `detecttrace serve`: it carries its generation and the
    script that checks for newer results. Without it, the page is the offline one.
    Raises ValueError for an unknown schema version, or a NaN or infinity in the results.
    """
    view = build_view(results)
    css = _read_asset("dashboard.css")
    js = _read_asset("dashboard.js")
    serve_js = None if served is None else _read_asset("dashboard-serve.js")
    scripts = [js] if serve_js is None else [js, serve_js]
    template = _create_environment().get_template(_TEMPLATE_NAME)
    return template.render(
        view=view,
        trend_charts=[_to_trend_charts(class_view.trend) for class_view in view.classes],
        swatches={
            style: create_marker(style, *_SWATCH_CENTER, radius=_SWATCH_RADIUS)
            for style in SHAPE_BY_STYLE
        },
        few_swatch=create_marker("1", *_SWATCH_CENTER, is_few=True, radius=_SWATCH_RADIUS),
        generator=f"{GENERATOR_PREFIX} {__version__}",
        # Markup: the policy is fixed text and base64 hashes. The three blocks are raw text
        # elements, so HTML escaping would corrupt them; the stylesheet and script are packaged
        # files, and the JSON is escaped for a script block.
        csp=Markup(_to_csp(css, scripts, can_connect=served is not None)),
        css=Markup(css),
        js=Markup(js),
        results_json=Markup(to_script_json(results)),
        served=served,
        serve_js=None if serve_js is None else Markup(serve_js),
        settling_text=None if served is None else format_held_back_text(served.held_back_cases),
    )


def render_waiting_page(
    counts: WaitingCounts, notes: Sequence[SummaryLine], served: ServedPage
) -> str:
    """Render the page `detecttrace serve` shows until at least one case can be scored.

    `notes` are the run's issue lines, which often say why nothing joined yet.
    """
    css = _read_asset("dashboard.css")
    serve_js = _read_asset("dashboard-serve.js")
    template = _create_environment().get_template(_WAITING_TEMPLATE_NAME)
    return template.render(
        counts=counts,
        notes=[
            _NoteText(to_visible_text(line.message), to_visible_text(line.hint)) for line in notes
        ],
        served=served,
        generator=f"{GENERATOR_PREFIX} {__version__}",
        csp=Markup(_to_csp(css, [serve_js], can_connect=True)),
        css=Markup(css),
        serve_js=Markup(serve_js),
        format_count=format_count,
    )


def write_dashboard(html: str, path: Path) -> None:
    """Write the page as UTF-8, replacing `path` only once the file is complete."""
    write_text_atomically(html, path)


def is_dashboard_file(path: Path) -> bool:
    """Whether `path` is a regular file that starts with the doctype and carries this tool's
    generator marker before `</head>`, all within its first 64 KiB.

    A path that can't be read raises OSError: "can't tell" must not look like "not ours".
    """
    head = read_head(path, MARKER_READ_BYTES)
    if head is None or not head.startswith(_DOCTYPE):
        return False
    head_end = head.find(_HEAD_END)
    return head_end != -1 and _MARKER.search(head, 0, head_end) is not None


def to_script_json(results: Mapping[str, object]) -> str:
    """Compact JSON that is safe inside a <script type="application/json"> element.

    The escaped characters can only occur inside JSON strings, so the text still parses to the
    same object.
    """
    text = json.dumps(results, ensure_ascii=False, allow_nan=False, separators=(",", ":"))
    return text.translate(_JSON_ESCAPE_TABLE)


def _read_asset(name: str) -> str:
    return files("detecttrace").joinpath("templates", name).read_text(encoding="utf-8")


def _create_environment() -> Environment:
    # Only the packaged templates folder is searched, never a path from the user.
    environment = Environment(
        loader=PackageLoader("detecttrace", "templates"),
        autoescape=True,
        undefined=StrictUndefined,
        trim_blocks=True,
        lstrip_blocks=True,
        keep_trailing_newline=True,
    )
    environment.filters["coord"] = _format_coordinate
    return environment


def _format_coordinate(value: float) -> str:
    # One decimal, as the chart geometry is rounded; also hides float noise such as 252.00000000000003.
    return f"{value:.1f}"


def _to_csp(css: str, scripts: Sequence[str], *, can_connect: bool) -> str:
    script_sources = " ".join(f"'{_to_hash_source(script)}'" for script in scripts)
    # Only a served page asks anything of a server, and only of the one that sent it.
    connect = " connect-src 'self';" if can_connect else ""
    return (
        f"default-src 'none'; script-src {script_sources}; "
        f"style-src '{_to_hash_source(css)}'; img-src data:;{connect} base-uri 'none'; "
        "form-action 'none'"
    )


def _to_hash_source(text: str) -> str:
    digest = hashlib.sha256(text.encode("utf-8")).digest()
    return "sha256-" + base64.b64encode(digest).decode("ascii")


def _to_trend_charts(trend: TrendView) -> _TrendCharts:
    return _TrendCharts(
        completeness=_to_chart(trend, trend.completeness) if trend.completeness.lines else None,
        agreement=_to_chart(trend, trend.agreement),
    )


def _to_chart(trend: TrendView, metric: TrendMetricView) -> Chart:
    return trend_chart(
        trend.weeks,
        [SeriesInput(line.style, line.values, line.counts, line.few) for line in metric.lines],
        trend.version_first_weeks,
    )
