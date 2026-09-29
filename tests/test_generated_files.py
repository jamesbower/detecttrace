"""The committed demo and fixtures are exactly what the generator writes; the demo tells its story."""

import gzip
import ipaddress
import re
import time
from pathlib import Path

import fixture_specs
import generate
import pytest

from detecttrace.metrics import SliceMetrics
from detecttrace.pipeline import RunResult, run_check
from detecttrace.runconfig import load_run_config

DEMO_DIR = generate.REPO_ROOT / "src" / "detecttrace" / "demo_data"
FIXTURE_ROOT = fixture_specs.FIXTURE_ROOT
# Captured from the OpenTelemetry SDK, not generated.
CAPTURED_FIXTURES = "console_exporter/"
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
MAX_DEMO_BYTES = 500 * 1024
MAX_GENERATION_SECONDS = 5.0
MAX_FIXTURE_BYTES = 3 * 1024 * 1024


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
    return "\n".join(_read_decoded(folder, _list_files(folder)).values())


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


def test_fixtures_are_under_3_mb() -> None:
    assert sum(path.stat().st_size for path in FIXTURE_ROOT.rglob("*") if path.is_file()) < (
        MAX_FIXTURE_BYTES
    )


def test_gzip_headers_carry_no_timestamp_or_file_name() -> None:
    headers = [path.read_bytes()[3:8] for path in sorted((DEMO_DIR / "traces").iterdir())]

    # Byte 3 holds the flags (0: no file name) and bytes 4-7 the modification time.
    assert headers == [b"\x00\x00\x00\x00\x00"] * 3


def test_every_trace_file_is_gzip_compressed() -> None:
    assert {path.read_bytes()[:2] for path in (DEMO_DIR / "traces").iterdir()} == {b"\x1f\x8b"}


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
        if Path(name).name != generate.GOLDEN_NAME and not name.startswith(CAPTURED_FIXTURES)
    }


def _read_plain(folder: Path, names: set[str]) -> dict[str, bytes]:
    return {
        name: (folder / name).read_bytes()
        for name in names
        if not name.endswith(COMPRESSED_SUFFIXES)
    }


def _read_decoded(folder: Path, names: set[str]) -> dict[str, str]:
    """The files as text, compressed ones decoded; compressed bytes can differ between builds."""
    return {name: _decode(folder / name).decode("utf-8") for name in names}


def _decode(path: Path) -> bytes:
    if path.suffix == ".gz":
        return gzip.decompress(path.read_bytes())
    if path.suffix == ".zst":
        import zstandard

        return zstandard.ZstdDecompressor().decompress(path.read_bytes())
    return path.read_bytes()
