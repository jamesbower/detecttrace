"""The HTML dashboard: the packaged page, filled with a view and a results object.

The page is built from `dashboard/` and committed as `templates/dashboard.html`. It holds exactly
one inline script and one inline stylesheet, whose SHA-256 hashes the build writes beside it, and
two empty, non-executed JSON blocks. Rendering fills those blocks with the view model and the
results, and composes the Content-Security-Policy that allows exactly the two inline blocks.

A page served by `detecttrace serve` asks the same server whether newer results exist, so its
policy also allows requests to its own origin, and nothing else.
"""

import json
import re
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from functools import cache
from importlib.resources import files
from pathlib import Path

from detecttrace.dashboard_view import (
    GENERATOR,
    GENERATOR_PREFIX,
    DashboardView,
    build_served_view,
    build_view,
    build_waiting_view,
    to_view_json,
)
from detecttrace.files import MARKER_READ_BYTES, read_head, write_text_atomically
from detecttrace.served_page import ServedPage, WaitingCounts
from detecttrace.summary import SummaryLine

VIEW_BLOCK_ID = "dt-view"
RESULTS_BLOCK_ID = "dt-results"
_PAGE_NAME = "dashboard.html"
_HASHES_NAME = "dashboard.hashes.json"
_HASH_SOURCE = re.compile(r"sha256-[A-Za-z0-9+/]{43}=")
_DOCTYPE = b"<!doctype html>"
# The prefix, one space, then a version; "detecttrace-like" or a bare prefix is someone else's.
# The built page closes its void elements with " />", the Jinja page did not.
_MARKER = re.compile(
    rb'<meta name="generator" content="'
    + re.escape(GENERATOR_PREFIX.encode())
    + rb' [^"]+"(?: /)?>'
)
# The page's inline script sits in its head and is far longer than the bytes read, so the marker
# must come before the head's first script or stylesheet as well as before its end.
_MARKER_LIMIT = re.compile(rb"</head>|<script|<style")
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


@dataclass(frozen=True, slots=True)
class _Page:
    # The built page split at its four slots: the policy, the generator, the view block and the
    # results block, in document order, so filling one can never touch another.
    parts: tuple[str, str, str, str, str]
    script_hash: str
    style_hash: str


def render_dashboard(results: Mapping[str, object], *, served: ServedPage | None = None) -> str:
    """Render the page for a results object from `results.build_results` (or its JSON).

    With `served`, the page is for `detecttrace serve`: its view carries its generation, and its
    policy lets it ask the same server for newer results. Without it, the page is the offline
    one. Raises ValueError for an unknown schema version, or a NaN or infinity in the results.
    """
    view = build_view(results) if served is None else build_served_view(results, served)
    return _fill_page(view, to_script_json(results), can_connect=served is not None)


def render_waiting_page(
    counts: WaitingCounts, notes: Sequence[SummaryLine], served: ServedPage
) -> str:
    """Render the page `detecttrace serve` shows until at least one case can be scored.

    `notes` are the run's issue lines, which often say why nothing joined yet. The page has no
    results, so its results block stays empty.
    """
    return _fill_page(build_waiting_view(counts, notes, served), "", can_connect=True)


def write_dashboard(html: str, path: Path) -> None:
    """Write the page as UTF-8, replacing `path` only once the file is complete."""
    write_text_atomically(html, path)


def is_dashboard_file(path: Path) -> bool:
    """Whether `path` is a regular file that starts with the doctype and carries this tool's
    generator marker before the head's first script, stylesheet or end, all within its first
    64 KiB.

    A path that can't be read raises OSError: "can't tell" must not look like "not ours".
    """
    head = read_head(path, MARKER_READ_BYTES)
    # HTML spells the doctype in any case; the built page writes it in lower case.
    if head is None or head[: len(_DOCTYPE)].lower() != _DOCTYPE:
        return False
    limit = _MARKER_LIMIT.search(head)
    return limit is not None and _MARKER.search(head, 0, limit.start()) is not None


def to_script_json(results: Mapping[str, object]) -> str:
    """Compact JSON that is safe inside a <script type="application/json"> element.

    The escaped characters can only occur inside JSON strings, so the text still parses to the
    same object.
    """
    text = json.dumps(results, ensure_ascii=False, allow_nan=False, separators=(",", ":"))
    return text.translate(_JSON_ESCAPE_TABLE)


def _fill_page(view: DashboardView, results_text: str, *, can_connect: bool) -> str:
    page = _load_page()
    fills = (
        _to_csp(page.script_hash, page.style_hash, can_connect=can_connect),
        GENERATOR,
        _to_data_block(VIEW_BLOCK_ID, to_script_json(to_view_json(view))),
        _to_data_block(RESULTS_BLOCK_ID, results_text),
    )
    pieces = [page.parts[0]]
    for fill, part in zip(fills, page.parts[1:], strict=True):
        pieces += (fill, part)
    return "".join(pieces)


@cache
def _load_page() -> _Page:
    # Read once per process: the packaged files never change while it runs.
    templates = files("detecttrace").joinpath("templates")
    html = templates.joinpath(_PAGE_NAME).read_text(encoding="utf-8")
    hashes = json.loads(templates.joinpath(_HASHES_NAME).read_text(encoding="utf-8"))
    slots = (
        "__DT_CSP__",
        "__DT_GENERATOR__",
        _to_data_block(VIEW_BLOCK_ID, ""),
        _to_data_block(RESULTS_BLOCK_ID, ""),
    )
    parts: list[str] = []
    rest = html
    for slot in slots:
        # A slot missing or repeated means a broken build; filling it anyway would ship a page
        # with no policy, or data where the page never reads it.
        if html.count(slot) != 1:
            raise RuntimeError(f"the packaged {_PAGE_NAME} must hold {slot!r} exactly once")
        before, found, rest = rest.partition(slot)
        if not found:
            raise RuntimeError(f"the packaged {_PAGE_NAME} holds {slot!r} out of order")
        parts.append(before)
    head, after_csp, after_generator, after_view = parts
    return _Page(
        parts=(head, after_csp, after_generator, after_view, rest),
        script_hash=_read_hash_source(hashes, "script"),
        style_hash=_read_hash_source(hashes, "style"),
    )


def _read_hash_source(hashes: Mapping[str, object], key: str) -> str:
    value = hashes.get(key)
    if not isinstance(value, str) or _HASH_SOURCE.fullmatch(value) is None:
        raise RuntimeError(f"the packaged {_HASHES_NAME} has no SHA-256 hash for its {key}")
    return value


def _to_data_block(block_id: str, text: str) -> str:
    return f'<script type="application/json" id="{block_id}">{text}</script>'


def _to_csp(script_hash: str, style_hash: str, *, can_connect: bool) -> str:
    policy = (
        f"default-src 'none'; script-src '{script_hash}'; style-src '{style_hash}'; "
        "base-uri 'none'; form-action 'none'"
    )
    # Only a served page asks anything of a server, and only of the one that sent it.
    return f"{policy}; connect-src 'self'" if can_connect else policy
