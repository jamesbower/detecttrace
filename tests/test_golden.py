"""The check on the bundled demo gives exactly the numbers in demo_data/expected.json.

After an intended change, rewrite the golden file with
`uv run python scripts/synthetic/generate.py --update-golden` and review the diff.
"""

import gzip
import json
from pathlib import Path

import generate
import pytest
from typer.testing import CliRunner

from detecttrace import cli
from detecttrace.pipeline import run_check
from detecttrace.runconfig import load_run_config

DEMO_DIR = generate.REPO_ROOT / "src" / "detecttrace" / "demo_data"
# Keys that exist only inside tool results, never in arguments or anything else DetectTrace keeps.
RESULT_ONLY_KEYS = (
    "department",
    "mfa_registered",
    "risk_score",
    "usual_countries",
    "publisher_verified",
    "row_count",
)


@pytest.fixture(scope="module")
def golden_text() -> str:
    return (DEMO_DIR / generate.GOLDEN_NAME).read_text(encoding="utf-8")


@pytest.fixture(scope="module")
def demo_results_text() -> str:
    config_path = DEMO_DIR / "detecttrace.yaml"
    results = run_check(load_run_config(config_path), config_path).results
    return generate.to_golden_text(generate.normalize_results(results))


def test_demo_results_match_the_golden_file(demo_results_text: str, golden_text: str) -> None:
    # Compared as text so a failure shows a line diff of the first differences.
    assert demo_results_text == golden_text


def test_demo_results_hold_no_tool_result_text(demo_results_text: str) -> None:
    assert [key for key in RESULT_ONLY_KEYS if f'"{key}' in demo_results_text] == []


def test_demo_traces_carry_the_result_only_keys() -> None:
    # Guards the check above: the keys it looks for must really be in the traces.
    text = "".join(
        gzip.decompress(path.read_bytes()).decode("utf-8")
        for path in sorted((DEMO_DIR / "traces").iterdir())
    )

    assert [key for key in RESULT_ONLY_KEYS if f'\\"{key}\\"' not in text] == []


def test_demo_command_writes_the_golden_results(tmp_path: Path, golden_text: str) -> None:
    out = tmp_path / "d.json"
    result = CliRunner().invoke(cli.app, ["demo", "--out", str(out), "--quiet"])
    written = generate.to_golden_text(
        generate.normalize_results(json.loads(out.read_text(encoding="utf-8")))
    )

    assert (result.exit_code, written) == (0, golden_text)
