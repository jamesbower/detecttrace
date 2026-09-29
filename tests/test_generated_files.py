"""The committed demo dataset is exactly what the generator writes, and it tells the demo story."""

import gzip
import ipaddress
import re
import time
from pathlib import Path

import generate
import pytest

from detecttrace.metrics import SliceMetrics
from detecttrace.pipeline import RunResult, run_check
from detecttrace.runconfig import load_run_config

DEMO_DIR = generate.REPO_ROOT / "src" / "detecttrace" / "demo_data"
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
def demo_text() -> str:
    return "\n".join(_read_decoded(DEMO_DIR, include_golden=True).values())


def test_regenerating_writes_the_same_file_names(generated: tuple[Path, float]) -> None:
    assert _list_files(generated[0]) == _list_files(DEMO_DIR) - {generate.GOLDEN_NAME}


def test_regenerated_plain_files_are_byte_identical(generated: tuple[Path, float]) -> None:
    assert _read_plain(generated[0]) == _read_plain(DEMO_DIR)


def test_regenerated_files_decode_to_the_same_content(
    generated: tuple[Path, float],
) -> None:
    assert _read_decoded(generated[0]) == _read_decoded(DEMO_DIR)


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


def test_every_ip_address_is_in_a_documentation_range(demo_text: str) -> None:
    outside = {
        address
        for address in IPV4.findall(demo_text)
        if not any(ipaddress.ip_address(address) in net for net in DOCUMENTATION_NETWORKS)
    }

    assert outside == set()


def test_every_email_address_is_at_example_com(demo_text: str) -> None:
    assert {
        email for email in EMAIL.findall(demo_text) if not email.endswith("@example.com")
    } == set()


def test_every_as_number_is_in_the_documentation_range(demo_text: str) -> None:
    assert {int(number) for number in AS_NUMBER.findall(demo_text)} - (
        DOCUMENTATION_AS_NUMBERS
    ) == set()


def test_the_safe_value_scans_find_values_to_check(demo_text: str) -> None:
    # Guards the three scans above against a pattern that silently matches nothing.
    counts = [len(pattern.findall(demo_text)) for pattern in (IPV4, EMAIL, AS_NUMBER)]

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


def _read_plain(folder: Path) -> dict[str, bytes]:
    return {
        name: (folder / name).read_bytes()
        for name in _list_files(folder) - {generate.GOLDEN_NAME}
        if not name.endswith(".gz")
    }


def _read_decoded(folder: Path, *, include_golden: bool = False) -> dict[str, str]:
    """Every file as text, gzip files decoded; gzip bytes can differ between zlib builds."""
    skipped = set() if include_golden else {generate.GOLDEN_NAME}
    return {
        name: (
            gzip.decompress((folder / name).read_bytes())
            if name.endswith(".gz")
            else (folder / name).read_bytes()
        ).decode("utf-8")
        for name in _list_files(folder) - skipped
    }
