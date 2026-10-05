import contextlib
import csv
import gzip
import io
import json
import multiprocessing
import sqlite3
import sys
import time
from collections.abc import Callable, Iterator
from concurrent.futures import ProcessPoolExecutor
from contextlib import closing
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import pytest
from fastapi.testclient import TestClient
from generate import to_yaml
from scale import SCALE_CHECKLISTS, make_scale_cases, write_scale_dataset
from serve.app_support import INGEST_TOKEN, VERDICTS_TOKEN, create_config

from detecttrace import pipeline
from detecttrace.dashboard import render_dashboard, write_dashboard
from detecttrace.metrics import compute_metrics
from detecttrace.results import write_results_json
from detecttrace.runconfig import load_run_config
from detecttrace.serve.app import create_app
from detecttrace.serve.config import load_serve_config
from detecttrace.serve.recompute import (
    RecomputeOutcome,
    RecomputeSettings,
    compute_snapshot,
    load_recompute_settings,
)
from detecttrace.serve.store import Store

GATE_SECONDS = 20
END_TO_END_GATE_SECONDS = 60
RESULTS_GATE_BYTES = 10_000_000
HTML_GATE_BYTES = 10_000_000
SPANS_PER_REQUEST = 512
VERDICTS_PER_REQUEST = 10_000
# Year 2100 is after every case the scale dataset holds, so none is held back.
FAR_FUTURE_NS = 4_102_444_800 * 1_000_000_000
# The names run_check looks up in the pipeline module, timed one by one.
STAGES = (
    "load_spans",
    "build_trace_cases",
    "read_verdicts",
    "join_cases",
    "compute_metrics",
    "build_results",
)


@dataclass(frozen=True, slots=True)
class EndToEndRun:
    seconds: float
    seconds_with_dashboard: float
    results_bytes: int
    html_bytes: int
    render_seconds: float
    write_seconds: float
    case_count: int


@dataclass(frozen=True, slots=True)
class IngestRun:
    config_path: Path
    database: Path
    outcomes: set[tuple[int, int]]  # (status, rejected rows or partial successes) per request


@dataclass(frozen=True, slots=True)
class RecomputeRun:
    seconds: float
    peak_rss_bytes: int
    outcome: RecomputeOutcome


@pytest.mark.benchmark
def test_metrics_stage_meets_the_20_second_gate():
    cases = make_scale_cases()

    started = time.perf_counter()
    compute_metrics(cases, SCALE_CHECKLISTS)
    elapsed = time.perf_counter() - started

    print(f"compute_metrics on {len(cases)} cases: {elapsed:.2f} s")
    assert elapsed <= GATE_SECONDS


@pytest.fixture(scope="module")
def scale_folder(tmp_path_factory: pytest.TempPathFactory) -> Path:
    folder = tmp_path_factory.mktemp("scale")
    started = time.perf_counter()
    write_scale_dataset(folder)
    print(f"\ngenerated the dataset in {time.perf_counter() - started:.1f} s (not timed)")
    return folder


@pytest.fixture(scope="module")
def end_to_end_run(scale_folder: Path) -> EndToEndRun:
    folder = scale_folder
    config_path = folder / "detecttrace.yaml"
    config = load_run_config(config_path)
    stage_seconds: dict[str, float] = {}

    with pytest.MonkeyPatch.context() as patch:
        for name in STAGES:
            patch.setattr(pipeline, name, _timed(getattr(pipeline, name), name, stage_seconds))
        started = time.perf_counter()
        result = pipeline.run_check(config, config_path)
        written = time.perf_counter()
        write_results_json(result.results, folder / "results.json")
        finished = time.perf_counter()
        html = render_dashboard(result.results)
        rendered = time.perf_counter()
        write_dashboard(html, folder / "dashboard.html")
        html_written = time.perf_counter()

    stage_seconds["write_results_json"] = finished - written
    results_bytes = (folder / "results.json").stat().st_size
    print(
        f"run_check + write_results_json on {result.case_count} cases: {finished - started:.1f} s"
    )
    for name, seconds in stage_seconds.items():
        print(f"  {name:<20} {seconds:6.1f} s")
    print(f"results JSON: {results_bytes / 1_000_000:.2f} MB")
    html_bytes = (folder / "dashboard.html").stat().st_size
    render_seconds = rendered - finished
    write_seconds = html_written - rendered
    print(
        f"dashboard HTML: {html_bytes / 1_000_000:.2f} MB, render {render_seconds:.1f} s, "
        f"write {write_seconds:.1f} s"
    )
    print(f"run_check + JSON + HTML: {html_written - started:.1f} s")
    return EndToEndRun(
        seconds=finished - started,
        seconds_with_dashboard=html_written - started,
        results_bytes=results_bytes,
        html_bytes=html_bytes,
        render_seconds=render_seconds,
        write_seconds=write_seconds,
        case_count=result.case_count,
    )


@pytest.mark.benchmark
def test_end_to_end_run_meets_the_60_second_gate(end_to_end_run: EndToEndRun):
    assert end_to_end_run.seconds <= END_TO_END_GATE_SECONDS


@pytest.mark.benchmark
def test_end_to_end_run_with_dashboard_meets_the_60_second_gate(end_to_end_run: EndToEndRun):
    assert end_to_end_run.seconds_with_dashboard <= END_TO_END_GATE_SECONDS


@pytest.mark.benchmark
def test_end_to_end_results_json_stays_under_10_mb(end_to_end_run: EndToEndRun):
    assert end_to_end_run.results_bytes <= RESULTS_GATE_BYTES


@pytest.mark.benchmark
def test_end_to_end_dashboard_html_stays_under_10_mb(end_to_end_run: EndToEndRun):
    assert end_to_end_run.html_bytes <= HTML_GATE_BYTES


@pytest.fixture(scope="module")
def ingest_run(scale_folder: Path) -> IngestRun:
    database = scale_folder / "serve" / "detecttrace.db"
    database.parent.mkdir()
    config_path = scale_folder / "serve" / "serve.yaml"
    document = {
        "serve": {"database": str(database), "settle_seconds": 0},
        "label_map": {
            "TP": "true_positive",
            "Malicious": "true_positive",
            "FP": "false_positive",
            "Benign": "benign",
            "Closed - Benign": "benign",
        },
        "checklists": str(scale_folder / "checklists"),
        "tokens": create_config(database).tokens.model_dump(),
    }
    config_path.write_text(to_yaml(document), encoding="utf-8")
    # The app reads the same file the worker does, so both see one label map.
    config = load_serve_config(config_path)
    requests = []
    store = Store.open(database)
    with TestClient(create_app(config, store, lambda: None)) as client:
        started = time.perf_counter()
        span_count = 0
        for batch, count in _read_span_batches(scale_folder / "traces"):
            span_count += count
            requests.append(
                client.post(
                    "/v1/traces",
                    content=gzip.compress(json.dumps(batch).encode("utf-8"), compresslevel=1),
                    headers={
                        "Authorization": f"Bearer {INGEST_TOKEN}",
                        "Content-Type": "application/json",
                        "Content-Encoding": "gzip",
                    },
                )
            )
        span_requests = len(requests)
        for body in _read_verdict_bodies(scale_folder / "verdicts.csv"):
            requests.append(
                client.post(
                    "/api/verdicts",
                    content=body,
                    headers={
                        "Authorization": f"Bearer {VERDICTS_TOKEN}",
                        "Content-Type": "text/csv",
                    },
                )
            )
        elapsed = time.perf_counter() - started
    store.close()
    # Fold the write-ahead log in so the size reported is the whole database.
    with closing(sqlite3.connect(database)) as connection:
        connection.execute("PRAGMA wal_checkpoint(TRUNCATE)")
    size = sum(
        path.stat().st_size for path in database.parent.glob("detecttrace.db*") if path.is_file()
    )
    print(
        f"ingest: {span_count} spans in {span_requests} requests "
        f"(+ {len(requests) - span_requests} verdict requests): {elapsed:.1f} s, "
        f"{span_count / elapsed:.0f} spans/s, database {size / 1_000_000:.1f} MB"
    )
    # A 200 can still carry rejected rows or a partial success, so the body counts too.
    outcomes = {
        (
            response.status_code,
            len(response.json().get("rejected", [])) + ("partialSuccess" in response.json()),
        )
        for response in requests
    }
    return IngestRun(config_path, database, outcomes)


@pytest.mark.benchmark
def test_ingest_requests_all_succeed_without_rejections(ingest_run: IngestRun):
    assert ingest_run.outcomes == {(200, 0)}


@pytest.fixture(scope="module")
def recompute_run(ingest_run: IngestRun) -> RecomputeRun:
    # Spawn, not fork: the server's worker starts clean, and the peak RSS is its own.
    context = multiprocessing.get_context("spawn")
    with ProcessPoolExecutor(max_workers=1, mp_context=context) as pool:
        started = time.perf_counter()
        outcome, peak_rss = pool.submit(
            measure_snapshot,
            ingest_run.database,
            # Loaded once in the parent and pickled to the worker, as the server does.
            load_recompute_settings(
                load_serve_config(ingest_run.config_path), ingest_run.config_path
            ),
            FAR_FUTURE_NS,
        ).result()
        seconds = time.perf_counter() - started
    print(
        f"recompute in a spawned process: {seconds:.1f} s, peak RSS {peak_rss / 1_000_000:.0f} MB"
    )
    return RecomputeRun(seconds, peak_rss, outcome)


@pytest.mark.benchmark
def test_recompute_meets_the_60_second_gate(recompute_run: RecomputeRun):
    assert recompute_run.seconds <= END_TO_END_GATE_SECONDS


@pytest.mark.benchmark
def test_recompute_scores_the_cases_check_scores(
    recompute_run: RecomputeRun, end_to_end_run: EndToEndRun
):
    results = json.loads(recompute_run.outcome.snapshot.results_json)
    assert results["totals"]["cases"] == end_to_end_run.case_count


def measure_snapshot(
    database: Path, settings: RecomputeSettings, now_ns: int
) -> tuple[RecomputeOutcome, int]:
    """Run in the child: the snapshot and the child's peak memory in bytes."""
    if sys.platform == "linux":
        # Linux keeps ru_maxrss across fork and exec, so a spawned child would report the
        # pytest parent's peak, which holds the whole dataset. Writing 5 to clear_refs resets
        # this process's own high-water mark, which VmHWM reports. Some containers forbid the
        # write; VmHWM then still covers only this process since its exec.
        with contextlib.suppress(OSError):
            Path("/proc/self/clear_refs").write_text("5", encoding="ascii")
        outcome = compute_snapshot(database, settings, now_ns)
        return outcome, _read_peak_rss_bytes_on_linux()
    outcome = compute_snapshot(database, settings, now_ns)
    if sys.platform == "win32":
        # Windows has no resource module, so peak memory is not measured there.
        return outcome, 0
    import resource

    peak = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss
    # macOS reports bytes, other systems kibibytes.
    return outcome, peak if sys.platform == "darwin" else peak * 1024


def _read_peak_rss_bytes_on_linux() -> int:
    status = Path("/proc/self/status").read_text(encoding="ascii")
    [line] = [line for line in status.splitlines() if line.startswith("VmHWM:")]
    return int(line.split()[1]) * 1024  # reported in kB


def _read_span_batches(traces: Path) -> Iterator[tuple[dict[str, Any], int]]:
    """Yield OTLP documents of at most SPANS_PER_REQUEST spans, each with its span count."""
    groups: list[dict[str, Any]] = []
    count = 0
    for path in sorted(traces.glob("*.jsonl.gz"), key=lambda path: path.as_posix()):
        with gzip.open(path, "rt", encoding="utf-8") as lines:
            for line in lines:
                for resource_spans in json.loads(line)["resourceSpans"]:
                    for scope_spans in resource_spans["scopeSpans"]:
                        spans = scope_spans["spans"]
                        position = 0
                        while position < len(spans):
                            part = spans[position : position + SPANS_PER_REQUEST - count]
                            position += len(part)
                            count += len(part)
                            groups.append(
                                {
                                    "resource": resource_spans["resource"],
                                    "scopeSpans": [{**scope_spans, "spans": part}],
                                }
                            )
                            if count == SPANS_PER_REQUEST:
                                yield {"resourceSpans": groups}, count
                                groups, count = [], 0
    if groups:
        yield {"resourceSpans": groups}, count


def _read_verdict_bodies(path: Path) -> Iterator[bytes]:
    with path.open(encoding="utf-8", newline="") as file:
        reader = csv.reader(file)
        header = next(reader)
        rows = list(reader)
    for start in range(0, len(rows), VERDICTS_PER_REQUEST):
        buffer = io.StringIO()
        writer = csv.writer(buffer, lineterminator="\n")
        writer.writerows([header, *rows[start : start + VERDICTS_PER_REQUEST]])
        yield buffer.getvalue().encode("utf-8")


def _timed(
    function: Callable[..., Any], name: str, seconds: dict[str, float]
) -> Callable[..., Any]:
    def run(*args: Any, **kwargs: Any) -> Any:
        started = time.perf_counter()
        try:
            return function(*args, **kwargs)
        finally:
            seconds[name] = seconds.get(name, 0.0) + time.perf_counter() - started

    return run
