"""The case-table script builds its rows with DOM methods and textContent only."""

from functools import cache
from pathlib import Path

import pytest

SCRIPT = Path(__file__).resolve().parent.parent / "src/detecttrace/templates/dashboard.js"
MAX_SCRIPT_BYTES = 20_000


@cache
def script() -> str:
    return SCRIPT.read_text(encoding="utf-8")


@pytest.mark.parametrize(
    "sink",
    [
        "innerHTML",
        "outerHTML",
        "insertAdjacentHTML",
        "document.write",
        "eval(",
        "Function(",
        'setTimeout("',
    ],
)
def test_the_script_uses_no_html_or_code_sink(sink: str) -> None:
    assert sink not in script()


def test_the_script_stays_small() -> None:
    assert len(script().encode("utf-8")) <= MAX_SCRIPT_BYTES
