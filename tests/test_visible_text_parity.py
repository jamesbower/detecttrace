"""The page shows a case ID exactly as the CLI summary's `to_visible_text` writes it.

Runs the built page in a real browser, so it checks the escaping the shipped script does:

    uv run --with playwright pytest -m browser -q tests/test_visible_text_parity.py
"""

import copy
import json
from pathlib import Path
from typing import Any

import pytest

from detecttrace.dashboard import render_dashboard, write_dashboard
from detecttrace.summary import to_visible_text

# Playwright is not a project dependency, so Pyright can't see its types; pages and
# browsers are typed `Any` here.
sync_api = pytest.importorskip(
    "playwright.sync_api",
    reason="Playwright is not installed; run `uv run --with playwright pytest -m browser`",
)

pytestmark = pytest.mark.browser

_DEMO_RESULTS = json.loads(
    (Path(__file__).parent / "fixtures" / "demo" / "expected.json").read_text(encoding="utf-8")
)
# Only characters both sides can hold: a lone surrogate from JSON stays one in JavaScript,
# but the Python loader has already replaced it with U+FFFD. Assigned characters only, so
# the two Unicode databases agree.
_TRICKY = [
    "plain case-001",
    "a\x1b[2Kb",
    "\x1b]52;c;ZXZpbA==\x07",
    "a\r\nb",
    "a\tb",
    "\x00",
    "a\x7fb\x85c",
    "user\N{RIGHT-TO-LEFT OVERRIDE}gnp.exe",
    "a\N{ZERO WIDTH SPACE}b",
    "a\N{LINE SEPARATOR}b\N{PARAGRAPH SEPARATOR}c",
    "\N{ZERO WIDTH NO-BREAK SPACE}case",
    "a\U0001f600b",
    "\U000e0001",
    "Café é",
]


def _with_tricky_case_ids() -> Any:
    """The demo results with the tricky IDs on the cases the table shows first: the newest
    week's, in column order."""
    results = copy.deepcopy(_DEMO_RESULTS)
    rows = results["case_rows"]
    weeks = [rows["strings"][index] for index in rows["columns"]["week"]]
    newest = [position for position, week in enumerate(weeks) if week == max(weeks)]
    for position, case_id in zip(newest, _TRICKY, strict=False):
        rows["columns"]["case_id"][position] = case_id
    return results


def test_the_case_table_shows_ids_as_to_visible_text_writes_them(tmp_path: Path) -> None:
    path = tmp_path / "tricky.html"
    write_dashboard(render_dashboard(_with_tricky_case_ids()), path)
    with sync_api.sync_playwright() as playwright:
        browser = playwright.chromium.launch()
        page = browser.new_page()
        page.goto(path.as_uri() + "#/cases")
        page.wait_for_selector(".case-toggle")
        shown = page.locator(".case-toggle .case-cell-text").all_text_contents()
        browser.close()
    assert shown[: len(_TRICKY)] == [to_visible_text(text) for text in _TRICKY]
