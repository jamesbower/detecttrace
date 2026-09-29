"""The HTML dashboard: one self-contained page built from a results object.

The page holds exactly one inline stylesheet and one inline script, both allowed by the SHA-256
hashes in its Content-Security-Policy, and the full results object as a non-executed JSON block
that the case-table script reads. Every other value is rendered by an autoescaping template.
"""

import base64
import hashlib
import json
import stat
from collections.abc import Mapping
from dataclasses import dataclass
from importlib.resources import files
from pathlib import Path

from jinja2 import Environment, PackageLoader, StrictUndefined
from markupsafe import Markup

from detecttrace import __version__
from detecttrace.charts import (
    PLOT_BOTTOM_MARGIN,
    PLOT_LEFT,
    PLOT_RIGHT_MARGIN,
    SHAPE_BY_STYLE,
    Chart,
    SeriesInput,
    create_marker,
    trend_chart,
)
from detecttrace.dashboard_view import TrendMetricView, TrendView, build_view
from detecttrace.results import MARKER_READ_BYTES, write_text_atomically

GENERATOR_PREFIX = "detecttrace"
_MARKER = b'<meta name="generator" content="' + GENERATOR_PREFIX.encode()
_TEMPLATE_NAME = "dashboard.html.j2"
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
class _TrendCharts:
    completeness: Chart | None  # None when the class has no checklist
    agreement: Chart


def render_dashboard(results: Mapping[str, object]) -> str:
    """Render the page for a results object from `results.build_results` (or its JSON).

    Raises ValueError for an unknown schema version, or a NaN or infinity in the results.
    """
    view = build_view(results)
    css = _read_asset("dashboard.css")
    js = _read_asset("dashboard.js")
    template = _create_environment().get_template(_TEMPLATE_NAME)
    return template.render(
        view=view,
        trend_charts=[_to_trend_charts(class_view.trend) for class_view in view.classes],
        frame={
            "left": PLOT_LEFT,
            "right_margin": PLOT_RIGHT_MARGIN,
            "bottom_margin": PLOT_BOTTOM_MARGIN,
        },
        swatches={
            style: create_marker(style, *_SWATCH_CENTER, radius=_SWATCH_RADIUS)
            for style in SHAPE_BY_STYLE
        },
        few_swatch=create_marker("1", *_SWATCH_CENTER, is_few=True, radius=_SWATCH_RADIUS),
        generator=f"{GENERATOR_PREFIX} {__version__}",
        # Markup: the policy is fixed text and base64 hashes. The three blocks are raw text
        # elements, so HTML escaping would corrupt them; the stylesheet and script are packaged
        # files, and the JSON is escaped for a script block.
        csp=Markup(_to_csp(css, js)),
        css=Markup(css),
        js=Markup(js),
        results_json=Markup(to_script_json(results)),
    )


def write_dashboard(html: str, path: Path) -> None:
    """Write the page as UTF-8, replacing `path` only once the file is complete."""
    write_text_atomically(html, path)


def is_dashboard_file(path: Path) -> bool:
    """Whether `path` is a regular file carrying this tool's generator marker near its top.

    Reads at most the first 64 KiB. A path that can't be read raises OSError: "can't tell"
    must not look like "not ours".
    """
    # stat() before open(), so a named pipe never blocks the run.
    if not stat.S_ISREG(path.stat().st_mode):
        return False
    with path.open("rb") as file:
        head = file.read(MARKER_READ_BYTES)
    return _MARKER in head


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


def _to_csp(css: str, js: str) -> str:
    return (
        f"default-src 'none'; script-src '{_to_hash_source(js)}'; "
        f"style-src '{_to_hash_source(css)}'; img-src data:; base-uri 'none'; form-action 'none'"
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
