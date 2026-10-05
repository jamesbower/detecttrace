"""The committed demo and fixtures are exactly what the generator writes; the demo tells its story."""

import gzip
import ipaddress
import re
import shutil
import time
from pathlib import Path
from typing import Any

import fixture_specs
import generate
import pytest

from detecttrace.metrics import SliceMetrics
from detecttrace.model import IssueKind
from detecttrace.pipeline import RunResult, run_check
from detecttrace.runconfig import load_run_config
from detecttrace.traces import load_spans

DEMO_DIR = generate.REPO_ROOT / "src" / "detecttrace" / "demo_data"
FIXTURE_ROOT = fixture_specs.FIXTURE_ROOT
# Captured from real tools, not generated.
CAPTURED_FIXTURES = ("collector_real/", "console_exporter/", "langfuse_real/")
COMPRESSED_SUFFIXES = (".gz", ".zst")
DOCUMENTATION_NETWORKS = tuple(
    ipaddress.ip_network(network)
    for network in ("192.0.2.0/24", "198.51.100.0/24", "203.0.113.0/24")
)
DOCUMENTATION_AS_NUMBERS = frozenset(range(64496, 64512))
IPV4 = re.compile(r"(?<![0-9.])(?:[0-9]{1,3}\.){3}[0-9]{1,3}(?![0-9.])")
EMAIL = re.compile(r"[\w.+-]+@[\w-]+(?:\.[\w-]+)+")
# Tool results are JSON inside a JSON string, so the quote before the colon may be escaped.
AS_NUMBER = re.compile(r'"asn\\?":\s*([0-9]+)')
TRUE_POSITIVE_CODE = 0
MAX_DEMO_BYTES = 500 * 1024
MAX_GENERATION_SECONDS = 5.0
MAX_FIXTURE_BYTES = 3584 * 1024


@pytest.fixture(scope="module")
def generated(tmp_path_factory: pytest.TempPathFactory) -> tuple[Path, float]:
    """The demo regenerated into a temporary folder, and how long that took."""
    folder = tmp_path_factory.mktemp("demo")
    started = time.perf_counter()
    generate.generate(generate.load_scenario("demo"), folder)
    return folder, time.perf_counter() - started


@pytest.fixture(scope="module")
def demo_run() -> RunResult:
    config_path = DEMO_DIR / "detecttrace.yaml"
    return run_check(load_run_config(config_path), config_path)


@pytest.fixture(scope="module")
def regenerated_fixtures(tmp_path_factory: pytest.TempPathFactory) -> Path:
    """Every fixture regenerated into a temporary folder."""
    pytest.importorskip("zstandard")
    root = tmp_path_factory.mktemp("fixtures")
    for spec in fixture_specs.FIXTURES:
        fixture_specs.write_fixture(spec, root)
    return root


@pytest.fixture(scope="module", params=["demo", "fixtures"])
def generated_text(request: pytest.FixtureRequest) -> str:
    """Every committed file of the demo or of the fixtures as one text, compressed files decoded."""
    folder = DEMO_DIR if request.param == "demo" else FIXTURE_ROOT
    # Lossy decoding keeps the strings inside captured protobuf bodies in the scan.
    return "\n".join(_read_decoded(folder, _list_files(folder), errors="replace").values())


def test_regenerating_writes_the_same_file_names(generated: tuple[Path, float]) -> None:
    # Every committed demo file, so a golden file left in the package fails here too.
    assert _list_files(generated[0]) == _list_files(DEMO_DIR)


def test_regenerated_plain_files_are_byte_identical(generated: tuple[Path, float]) -> None:
    assert _read_plain(generated[0], _list_generated(DEMO_DIR)) == _read_plain(
        DEMO_DIR, _list_generated(DEMO_DIR)
    )


def test_regenerated_files_decode_to_the_same_content(
    generated: tuple[Path, float],
) -> None:
    assert _read_decoded(generated[0], _list_generated(DEMO_DIR)) == _read_decoded(
        DEMO_DIR, _list_generated(DEMO_DIR)
    )


def test_regenerating_fixtures_writes_the_same_file_names(regenerated_fixtures: Path) -> None:
    assert _list_files(regenerated_fixtures) == _list_generated(FIXTURE_ROOT)


def test_regenerated_fixture_plain_files_are_byte_identical(regenerated_fixtures: Path) -> None:
    names = _list_generated(FIXTURE_ROOT)

    assert _read_plain(regenerated_fixtures, names) == _read_plain(FIXTURE_ROOT, names)


def test_regenerated_fixture_files_decode_to_the_same_content(
    regenerated_fixtures: Path,
) -> None:
    names = _list_generated(FIXTURE_ROOT)

    assert _read_decoded(regenerated_fixtures, names) == _read_decoded(FIXTURE_ROOT, names)


def test_fixtures_are_under_3_5_mb() -> None:
    assert sum(path.stat().st_size for path in FIXTURE_ROOT.rglob("*") if path.is_file()) < (
        MAX_FIXTURE_BYTES
    )


def test_gzip_headers_carry_no_timestamp_or_file_name() -> None:
    headers = [path.read_bytes()[3:8] for path in sorted((DEMO_DIR / "traces").iterdir())]

    # Byte 3 holds the flags (0: no file name) and bytes 4-7 the modification time.
    assert headers == [b"\x00\x00\x00\x00\x00"] * 3


def test_every_trace_file_is_gzip_compressed() -> None:
    assert {path.read_bytes()[:2] for path in (DEMO_DIR / "traces").iterdir()} == {b"\x1f\x8b"}


@pytest.mark.benchmark
def test_generating_the_demo_takes_under_five_seconds(generated: tuple[Path, float]) -> None:
    assert generated[1] < MAX_GENERATION_SECONDS


def test_demo_data_is_under_500_kb() -> None:
    assert sum(path.stat().st_size for path in DEMO_DIR.rglob("*") if path.is_file()) < (
        MAX_DEMO_BYTES
    )


def test_impossible_travel_completeness_drops_by_at_least_0_15_in_v2(
    demo_run: RunResult,
) -> None:
    v1 = _completeness(demo_run, "impossible_travel", "v1")
    v2 = _completeness(demo_run, "impossible_travel", "v2")

    assert v1 - v2 >= 0.15


def test_oauth_consent_completeness_stays_within_0_05_across_versions(
    demo_run: RunResult,
) -> None:
    v1 = _completeness(demo_run, "oauth_consent", "v1")
    v2 = _completeness(demo_run, "oauth_consent", "v2")

    assert abs(v1 - v2) <= 0.05


def test_impossible_travel_v2_has_at_least_two_dangerous_false_closes(
    demo_run: RunResult,
) -> None:
    assert len(_slice(demo_run, "impossible_travel", "v2").dangerous_false_closes) >= 2


def test_impossible_travel_v1_has_no_dangerous_false_closes(demo_run: RunResult) -> None:
    assert _slice(demo_run, "impossible_travel", "v1").dangerous_false_closes == ()


def test_demo_run_reports_no_issues(demo_run: RunResult) -> None:
    assert demo_run.issues == []


def test_a_wrong_tool_arguments_mapping_warns_once_per_item_with_rules(tmp_path: Path) -> None:
    folder = shutil.copytree(DEMO_DIR, tmp_path / "demo")
    config_path = folder / "detecttrace.yaml"
    with config_path.open("a", encoding="utf-8") as file:
        file.write("mapping: {tool_arguments: tool.parameters}\n")

    run = run_check(load_run_config(config_path), config_path)

    assert [
        issue.subject for issue in run.issues if issue.kind is IssueKind.MISSING_TOOL_ARGUMENTS
    ] == [
        "impossible_travel/signin_history",
        "impossible_travel/signin_query",
        "impossible_travel/location_history",
        "oauth_consent/grant_window",
        "oauth_consent/audit_query",
        "oauth_consent/consent_history",
    ]


def test_demo_has_between_180_and_220_cases(demo_run: RunResult) -> None:
    assert 180 <= demo_run.case_count <= 220


def test_demo_true_positive_share_is_between_10_and_20_percent(demo_run: RunResult) -> None:
    analyst = _case_columns(demo_run)["analyst"]

    assert 0.10 <= analyst.count(TRUE_POSITIVE_CODE) / len(analyst) <= 0.20


def test_demo_spans_six_iso_weeks(demo_run: RunResult) -> None:
    assert len(set(_case_strings(demo_run, "week"))) == 6


def test_demo_runs_v1_only_in_the_first_two_weeks(demo_run: RunResult) -> None:
    weeks = _case_strings(demo_run, "week")
    versions = _case_strings(demo_run, "version")

    v1_weeks = {week for week, version in zip(weeks, versions, strict=True) if version == "v1"}

    assert sorted(v1_weeks) == sorted(set(weeks))[:2]


def test_demo_has_exactly_one_sub_agent_case() -> None:
    spans, _ = load_spans(DEMO_DIR / "traces")

    assert [
        span.name
        for span in spans
        if span.attributes.get("gen_ai.operation.name") == "invoke_agent" and span.parent_span_id
    ] == ["invoke_agent ip-enrichment-agent"]


def test_every_ip_address_is_in_a_documentation_range(generated_text: str) -> None:
    outside = {
        address
        for address in IPV4.findall(generated_text)
        if not any(ipaddress.ip_address(address) in net for net in DOCUMENTATION_NETWORKS)
    }

    assert outside == set()


def test_every_email_address_is_at_example_com(generated_text: str) -> None:
    assert {
        email for email in EMAIL.findall(generated_text) if not email.endswith("@example.com")
    } == set()


def test_every_as_number_is_in_the_documentation_range(generated_text: str) -> None:
    assert {int(number) for number in AS_NUMBER.findall(generated_text)} - (
        DOCUMENTATION_AS_NUMBERS
    ) == set()


def test_the_safe_value_scans_find_values_to_check(generated_text: str) -> None:
    # Guards the three scans above against a pattern that silently matches nothing.
    counts = [len(pattern.findall(generated_text)) for pattern in (IPV4, EMAIL, AS_NUMBER)]

    assert min(counts) > 0


def _case_columns(run: RunResult) -> dict[str, list[Any]]:
    rows: Any = run.results["case_rows"]
    return rows["columns"]


def _case_strings(run: RunResult, column: str) -> list[str]:
    """A string-table column of the case rows, resolved to its text; the demo has no nulls."""
    rows: Any = run.results["case_rows"]
    return [rows["strings"][index] for index in rows["columns"][column]]


def _slice(run: RunResult, alert_class: str, version: str) -> SliceMetrics:
    report = next(report for report in run.report.classes if report.alert_class == alert_class)
    return report.by_version[version]


def _completeness(run: RunResult, alert_class: str, version: str) -> float:
    completeness = _slice(run, alert_class, version).completeness
    assert completeness is not None, f"{alert_class} has no checklist"
    return completeness.mean


def _list_files(folder: Path) -> set[str]:
    return {path.relative_to(folder).as_posix() for path in folder.rglob("*") if path.is_file()}


def _list_generated(folder: Path) -> set[str]:
    """The files the generator writes: all but golden files and captured fixtures."""
    return {
        name
        for name in _list_files(folder)
        if Path(name).name
        not in (generate.GOLDEN_NAME, generate.GOLDEN_HTML_NAME, generate.INIT_GOLDEN_NAME)
        and not name.startswith(CAPTURED_FIXTURES)
    }


def _read_plain(folder: Path, names: set[str]) -> dict[str, bytes]:
    return {
        name: (folder / name).read_bytes()
        for name in names
        if not name.endswith(COMPRESSED_SUFFIXES)
    }


def _read_decoded(folder: Path, names: set[str], errors: str = "strict") -> dict[str, str]:
    """The files as text, compressed ones decoded; compressed bytes can differ between builds."""
    return {name: _decode(folder / name).decode("utf-8", errors) for name in names}


def _decode(path: Path) -> bytes:
    if path.suffix == ".gz":
        return gzip.decompress(path.read_bytes())
    if path.suffix == ".zst":
        import zstandard

        return zstandard.ZstdDecompressor().decompress(path.read_bytes())
    return path.read_bytes()
