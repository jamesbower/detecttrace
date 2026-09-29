import time
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

import pytest
from scale import SCALE_CHECKLISTS, make_scale_cases, write_scale_dataset

from detecttrace import pipeline
from detecttrace.dashboard import render_dashboard, write_dashboard
from detecttrace.metrics import compute_metrics
from detecttrace.results import write_results_json
from detecttrace.runconfig import load_run_config

GATE_SECONDS = 20
END_TO_END_GATE_SECONDS = 60
RESULTS_GATE_BYTES = 10_000_000
HTML_GATE_BYTES = 10_000_000
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


@pytest.mark.benchmark
def test_metrics_stage_meets_the_20_second_gate():
    cases = make_scale_cases()

    started = time.perf_counter()
    compute_metrics(cases, SCALE_CHECKLISTS)
    elapsed = time.perf_counter() - started

    print(f"compute_metrics on {len(cases)} cases: {elapsed:.2f} s")
    assert elapsed <= GATE_SECONDS


@pytest.fixture(scope="module")
def end_to_end_run(tmp_path_factory: pytest.TempPathFactory) -> EndToEndRun:
    folder = tmp_path_factory.mktemp("scale")
    started = time.perf_counter()
    config_path = write_scale_dataset(folder)
    print(f"\ngenerated the dataset in {time.perf_counter() - started:.1f} s (not timed)")
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
