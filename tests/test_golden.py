"""The check gives exactly the numbers in each golden file: the demo's and every fixture's.

The demo also has a golden dashboard page, and the demo and a few fixtures a golden view model
for the React dashboard's tests.

After an intended change, rewrite the golden files with
`uv run python scripts/synthetic/generate.py --update-golden` and review the diff.
"""

import difflib
import gzip
import importlib.util
import json
from pathlib import Path

import fixture_specs
import generate
import pytest
from typer.testing import CliRunner

from detecttrace import __version__, cli
from detecttrace.dashboard import render_dashboard
from detecttrace.model import IssueKind
from detecttrace.pipeline import RunResult, run_check
from detecttrace.runconfig import load_run_config

DEMO_DIR = generate.REPO_ROOT / "src" / "detecttrace" / "demo_data"
FIXTURE_ROOT = fixture_specs.FIXTURE_ROOT
# Outside the package so it doesn't ship in every wheel; unlike fixture goldens it has no `keep`.
DEMO_GOLDEN = FIXTURE_ROOT / "demo" / generate.GOLDEN_NAME
DEMO_GOLDEN_HTML = FIXTURE_ROOT / "demo" / generate.GOLDEN_HTML_NAME
DEMO_GOLDEN_VIEW = FIXTURE_ROOT / "demo" / generate.VIEW_GOLDEN_NAME
FORMATS_DIR = FIXTURE_ROOT / "formats"
NEEDS_ZSTD = pytest.mark.skipif(
    importlib.util.find_spec("zstandard") is None, reason="zstandard is not installed"
)
# Every trace format variant writes the demo's cases, so each must give the demo's numbers.
FORMAT_VARIANTS = (
    "jsonl_rotated",
    "gzip",
    pytest.param("zstd", marks=NEEDS_ZSTD),
    "single_document",
    "openinference",
    "no_operation_name",
)
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
    return DEMO_GOLDEN.read_text(encoding="utf-8")


@pytest.fixture(scope="module")
def demo_results_text() -> str:
    config_path = DEMO_DIR / "detecttrace.yaml"
    results = run_check(load_run_config(config_path), config_path).results
    return generate.to_golden_text(generate.normalize_results(results))


def test_demo_results_match_the_golden_file(demo_results_text: str, golden_text: str) -> None:
    # Compared through a diff: pytest's own diff of two large texts takes minutes.
    assert _first_difference(golden_text, demo_results_text) == ""


def test_demo_results_hold_no_tool_result_text(demo_results_text: str) -> None:
    assert [key for key in RESULT_ONLY_KEYS if f'"{key}' in demo_results_text] == []


def test_demo_traces_carry_the_result_only_keys() -> None:
    # Guards the check above: the keys it looks for must really be in the traces.
    traces = [
        path for path in sorted((DEMO_DIR / "traces").iterdir()) if not path.name.startswith(".")
    ]
    text = "".join(gzip.decompress(path.read_bytes()).decode("utf-8") for path in traces)

    assert [key for key in RESULT_ONLY_KEYS if f'\\"{key}\\"' not in text] == []


@pytest.fixture(scope="module")
def demo_command(tmp_path_factory: pytest.TempPathFactory) -> tuple[int, Path]:
    """The demo command's exit code, and the folder it wrote d.html and d.json into."""
    folder = tmp_path_factory.mktemp("demo_command")
    result = CliRunner().invoke(
        cli.app,
        ["demo", "--out", str(folder / "d.html"), "--json", str(folder / "d.json"), "--quiet"],
    )
    return result.exit_code, folder


def test_demo_command_with_both_outputs_exits_0(demo_command: tuple[int, Path]) -> None:
    assert demo_command[0] == 0


def test_demo_command_writes_the_golden_results(
    demo_command: tuple[int, Path], golden_text: str
) -> None:
    text = (demo_command[1] / "d.json").read_text(encoding="utf-8")
    written = generate.to_golden_text(generate.normalize_results(json.loads(text)))

    assert _first_difference(golden_text, written) == ""


def test_demo_command_writes_the_golden_dashboard(demo_command: tuple[int, Path]) -> None:
    expected = DEMO_GOLDEN_HTML.read_text(encoding="utf-8")
    text = (demo_command[1] / "d.html").read_text(encoding="utf-8")

    assert (
        _first_difference(expected, generate.normalize_dashboard(text), DEMO_GOLDEN_HTML.name) == ""
    )


def test_demo_view_matches_the_golden_view() -> None:
    expected = DEMO_GOLDEN_VIEW.read_text(encoding="utf-8")
    config_path = DEMO_DIR / "detecttrace.yaml"

    actual = generate.to_view_golden_text(
        run_check(load_run_config(config_path), config_path).results
    )

    assert _first_difference(expected, actual, DEMO_GOLDEN_VIEW.name) == ""


@pytest.mark.parametrize("name", fixture_specs.VIEW_FIXTURES)
def test_fixture_view_matches_its_golden_view(name: str) -> None:
    folder = FIXTURE_ROOT / name
    expected = (folder / generate.VIEW_GOLDEN_NAME).read_text(encoding="utf-8")

    actual = fixture_specs.to_view_text(folder)

    assert _first_difference(expected, actual, generate.VIEW_GOLDEN_NAME) == ""


@pytest.fixture(scope="module")
def normalized_demo_page() -> str:
    results = json.loads(DEMO_GOLDEN.read_text(encoding="utf-8"))
    return generate.normalize_dashboard(render_dashboard(results))


def test_the_golden_page_holds_a_placeholder_for_the_stylesheet(normalized_demo_page: str) -> None:
    assert (
        generate.STYLE_OPEN_TAG + generate.STYLESHEET_PLACEHOLDER + "</style>"
    ) in normalized_demo_page


def test_the_golden_page_holds_a_placeholder_for_the_script(normalized_demo_page: str) -> None:
    assert (
        generate.SCRIPT_OPEN_TAG + generate.SCRIPT_PLACEHOLDER + "</script>"
    ) in normalized_demo_page


def test_the_golden_page_holds_a_placeholder_for_the_view(normalized_demo_page: str) -> None:
    assert (
        generate.VIEW_OPEN_TAG + generate.VIEW_PLACEHOLDER + "</script>"
    ) in normalized_demo_page


def test_the_golden_page_holds_a_placeholder_for_the_results(normalized_demo_page: str) -> None:
    assert (
        generate.RESULTS_OPEN_TAG + generate.RESULTS_PLACEHOLDER + "</script>"
    ) in normalized_demo_page


def test_the_golden_page_holds_placeholders_for_the_policy_hashes(
    normalized_demo_page: str,
) -> None:
    assert (
        f"script-src '{generate.SCRIPT_HASH_PLACEHOLDER}'; "
        f"style-src '{generate.STYLE_HASH_PLACEHOLDER}';"
    ) in normalized_demo_page


def test_the_golden_page_has_no_version(normalized_demo_page: str) -> None:
    assert __version__ not in normalized_demo_page


def _fixture_param(folder: Path) -> object:
    name = folder.relative_to(FIXTURE_ROOT).as_posix()
    return pytest.param(folder, id=name, marks=[NEEDS_ZSTD] if "zstd" in name else [])


FIXTURE_FOLDERS = sorted(
    path.parent for path in FIXTURE_ROOT.rglob(generate.GOLDEN_NAME) if path != DEMO_GOLDEN
)


@pytest.mark.parametrize("folder", [_fixture_param(folder) for folder in FIXTURE_FOLDERS])
def test_fixture_matches_its_golden_file(folder: Path) -> None:
    expected = (folder / generate.GOLDEN_NAME).read_text(encoding="utf-8")
    keep = json.loads(expected)["keep"]

    actual = generate.to_golden_text(fixture_specs.check_fixture(folder, keep))

    assert _first_difference(expected, actual) == ""


def test_every_registered_fixture_has_a_golden_file() -> None:
    assert sorted(spec.name for spec in fixture_specs.FIXTURES) == [
        folder.relative_to(FIXTURE_ROOT).as_posix() for folder in FIXTURE_FOLDERS
    ]


@pytest.mark.parametrize("variant", FORMAT_VARIANTS)
def test_format_variant_gives_the_demo_classes(variant: str, golden_text: str) -> None:
    expected = generate.to_golden_text(json.loads(golden_text)["classes"])
    config_path = FORMATS_DIR / variant / fixture_specs.CONFIG_NAME

    results = run_check(load_run_config(config_path), config_path).results
    actual = generate.to_golden_text(results["classes"])

    assert _first_difference(expected, actual) == ""


@pytest.fixture(scope="module")
def console_run() -> RunResult:
    config_path = FORMATS_DIR / "console_exporter" / fixture_specs.CONFIG_NAME
    return run_check(load_run_config(config_path), config_path)


def test_console_exporter_variant_joins_no_case(console_run: RunResult) -> None:
    assert console_run.case_count == 0


def test_console_exporter_variant_reports_console_output(console_run: RunResult) -> None:
    assert IssueKind.CONSOLE_EXPORTER_OUTPUT in {issue.kind for issue in console_run.issues}


def test_first_difference_is_empty_for_equal_texts() -> None:
    assert _first_difference("same\n", "same\n") == ""


def test_first_difference_shows_only_the_first_hunk() -> None:
    expected = "".join(f"line {number}\n" for number in range(40))
    actual = expected.replace("line 5\n", "changed 5\n").replace("line 30\n", "changed 30\n")

    assert "changed 30" not in _first_difference(expected, actual)


def _first_difference(expected: str, actual: str, name: str = generate.GOLDEN_NAME) -> str:
    """A unified diff cut after its first hunk, so a failure points at one place; "" if equal."""
    lines = list(
        difflib.unified_diff(
            expected.splitlines(keepends=True),
            actual.splitlines(keepends=True),
            name,
            "actual",
        )
    )
    hunks = [index for index, line in enumerate(lines) if line.startswith("@@")]
    end = hunks[1] if len(hunks) > 1 else len(lines)
    return "".join(lines[:end])
