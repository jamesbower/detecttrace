import json
import shutil
import subprocess
from pathlib import Path

import pytest

from detecttrace.summary import to_visible_text

_ROOT = Path(__file__).resolve().parent.parent
_SCRIPT = _ROOT / "src" / "detecttrace" / "templates" / "dashboard.js"

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
    "Café é",
]


@pytest.mark.skipif(shutil.which("node") is None, reason="node is not installed")
def test_case_table_script_escapes_like_to_visible_text() -> None:
    program = (
        f"const {{ toVisibleText }} = require({json.dumps(str(_SCRIPT))});"
        "const input = JSON.parse(require('fs').readFileSync(0, 'utf8'));"
        "process.stdout.write(JSON.stringify(input.map(toVisibleText)));"
    )
    completed = subprocess.run(
        ["node", "-e", program],
        input=json.dumps(_TRICKY),
        capture_output=True,
        text=True,
        encoding="utf-8",
        check=True,
    )
    assert json.loads(completed.stdout) == [to_visible_text(text) for text in _TRICKY]
