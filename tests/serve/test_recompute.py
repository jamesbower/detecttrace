import json
import multiprocessing
import re
from collections.abc import Iterator
from concurrent.futures import Future, ProcessPoolExecutor
from pathlib import Path
from typing import Any

import pytest
import yaml
from builders import case_root

from detecttrace.model import Issue, IssueKind, VerdictRow
from detecttrace.pipeline import run_check
from detecttrace.runconfig import load_run_config
from detecttrace.serve.auth import hash_token
from detecttrace.serve.recompute import (
    RecomputeCoordinator,
    RecomputeStatus,
    compute_snapshot,
    to_iso_time,
)
from detecttrace.serve.store import INGEST_SUBJECT, Snapshot, Store
from detecttrace.traces import load_spans
from detecttrace.verdicts import read_verdicts

DEMO_DIR = Path(__file__).resolve().parents[2] / "src" / "detecttrace" / "demo_data"
DEBOUNCE = 5.0
SETTLE_SECONDS = 300
SETTLE_NS = SETTLE_SECONDS * 1_000_000_000
CASE_END_NS = 1_700_000_000_000_000_000
# Long after every case, so nothing is held back.
LATE_NS = CASE_END_NS + 10 * SETTLE_NS


class FakeClock:
    def __init__(self) -> None:
        self.now = 1_000.0

    def __call__(self) -> float:
        return self.now

    def advance(self, seconds: float) -> None:
        self.now += seconds


class FakeSubmit:
    """Hands out futures the test completes by hand, and remembers each one."""

    def __init__(self) -> None:
        self.futures: list[Future[Snapshot]] = []

    def __call__(self) -> Future[Snapshot]:
        future: Future[Snapshot] = Future()
        self.futures.append(future)
        return future

    @property
    def count(self) -> int:
        return len(self.futures)

    def succeed(self, generation: int = 1, html: str = "<p>new</p>") -> None:
        self.futures[-1].set_result(Snapshot(generation, 2, html, "{}"))

    def fail(self) -> None:
        self.futures[-1].set_exception(RuntimeError("boom"))


@pytest.fixture
def store(tmp_path: Path) -> Iterator[Store]:
    opened = Store.open(tmp_path / "detecttrace.db")
    yield opened
    opened.close()


@pytest.fixture
def current_store(store: Store) -> Store:
    """A store whose snapshot is up to date, so starting a coordinator schedules nothing."""
    store.write_snapshot(Snapshot(0, 1, "<p>old</p>", "{}"))
    return store


@pytest.fixture
def clock() -> FakeClock:
    return FakeClock()


@pytest.fixture
def submit() -> FakeSubmit:
    return FakeSubmit()


@pytest.fixture
def coordinator(current_store: Store, clock: FakeClock, submit: FakeSubmit) -> RecomputeCoordinator:
    return RecomputeCoordinator(submit, current_store, clock, DEBOUNCE)


def start_run(coordinator: RecomputeCoordinator, clock: FakeClock) -> None:
    coordinator.notify_write()
    clock.advance(DEBOUNCE)
    coordinator.tick()


def fail_runs(
    coordinator: RecomputeCoordinator, clock: FakeClock, submit: FakeSubmit, count: int
) -> None:
    """Fail `count` runs in a row, each started by a write; the clock ends at the last failure."""
    for _ in range(count):
        coordinator.notify_write()
        clock.advance(1_000)
        coordinator.tick()
        submit.fail()
        coordinator.tick()


def succeed_run(coordinator: RecomputeCoordinator, clock: FakeClock, submit: FakeSubmit) -> None:
    coordinator.notify_write()
    clock.advance(1_000)
    coordinator.tick()
    submit.succeed()
    coordinator.tick()


# Debounce


def test_no_run_starts_before_the_quiet_period(
    coordinator: RecomputeCoordinator, clock: FakeClock, submit: FakeSubmit
) -> None:
    coordinator.notify_write()
    clock.advance(DEBOUNCE - 0.1)
    coordinator.tick()

    assert submit.count == 0


def test_one_run_starts_after_the_quiet_period(
    coordinator: RecomputeCoordinator, clock: FakeClock, submit: FakeSubmit
) -> None:
    start_run(coordinator, clock)

    assert submit.count == 1


def test_a_later_write_restarts_the_quiet_period(
    coordinator: RecomputeCoordinator, clock: FakeClock, submit: FakeSubmit
) -> None:
    coordinator.notify_write()
    clock.advance(3)
    coordinator.notify_write()
    clock.advance(3)
    coordinator.tick()

    assert submit.count == 0


def test_nothing_runs_without_a_write(
    coordinator: RecomputeCoordinator, clock: FakeClock, submit: FakeSubmit
) -> None:
    clock.advance(1_000)
    coordinator.tick()

    assert submit.count == 0


# The maximum wait under steady ingest


def run_start_times(
    coordinator: RecomputeCoordinator,
    clock: FakeClock,
    submit: FakeSubmit,
    seconds: int,
    run_seconds: int = 0,
) -> list[float]:
    """Write and tick once a second for `seconds`; each run finishes `run_seconds` after it starts.

    Returns when each run started, in seconds after the first write.
    """
    first_write_at = clock.now
    starts: list[float] = []
    for _ in range(seconds + 1):
        elapsed = clock.now - first_write_at
        if starts and not submit.futures[-1].done() and elapsed - starts[-1] >= run_seconds:
            submit.succeed()
        coordinator.notify_write()
        coordinator.tick()
        if submit.count > len(starts):
            starts.append(elapsed)
        clock.advance(1)
    return starts


def test_steady_writes_start_the_first_run_at_the_maximum_wait(
    coordinator: RecomputeCoordinator, clock: FakeClock, submit: FakeSubmit
) -> None:
    starts = run_start_times(coordinator, clock, submit, 130)

    assert starts[0] == 60.0


def test_steady_writes_start_the_next_run_a_maximum_wait_after_the_first_uncovered_write(
    coordinator: RecomputeCoordinator, clock: FakeClock, submit: FakeSubmit
) -> None:
    # The write at 60 s is covered by the run that starts in the same second; 61 s is next.
    starts = run_start_times(coordinator, clock, submit, 130)

    assert starts == [60.0, 121.0]


def test_a_run_longer_than_the_maximum_wait_is_followed_as_soon_as_it_finishes(
    coordinator: RecomputeCoordinator, clock: FakeClock, submit: FakeSubmit
) -> None:
    starts = run_start_times(coordinator, clock, submit, 160, run_seconds=70)

    assert starts == [60.0, 130.0]


def test_a_single_write_still_runs_after_the_quiet_period_alone(
    current_store: Store, clock: FakeClock, submit: FakeSubmit
) -> None:
    coordinator = RecomputeCoordinator(
        submit, current_store, clock, DEBOUNCE, max_wait_seconds=1_000
    )
    start_run(coordinator, clock)

    assert submit.count == 1


# Single flight and the rerun after a busy run


def test_no_second_run_starts_while_one_runs(
    coordinator: RecomputeCoordinator, clock: FakeClock, submit: FakeSubmit
) -> None:
    start_run(coordinator, clock)
    coordinator.notify_write()
    clock.advance(1_000)
    coordinator.tick()

    assert submit.count == 1


def test_writes_during_a_run_cause_one_more_run_after_it(
    coordinator: RecomputeCoordinator, clock: FakeClock, submit: FakeSubmit
) -> None:
    start_run(coordinator, clock)
    coordinator.notify_write()
    coordinator.notify_write()
    clock.advance(DEBOUNCE)
    submit.succeed()
    coordinator.tick()

    assert submit.count == 2


def test_writes_during_a_run_cause_no_more_than_one_more_run(
    coordinator: RecomputeCoordinator, clock: FakeClock, submit: FakeSubmit
) -> None:
    start_run(coordinator, clock)
    coordinator.notify_write()
    coordinator.notify_write()
    clock.advance(DEBOUNCE)
    submit.succeed()
    coordinator.tick()
    submit.succeed(generation=2)
    clock.advance(1_000)
    coordinator.tick()

    assert submit.count == 2


def test_a_run_without_writes_during_it_is_not_repeated(
    coordinator: RecomputeCoordinator, clock: FakeClock, submit: FakeSubmit
) -> None:
    start_run(coordinator, clock)
    submit.succeed()
    clock.advance(1_000)
    coordinator.tick()
    coordinator.tick()

    assert submit.count == 1


def test_the_status_shows_a_run_in_progress(
    coordinator: RecomputeCoordinator, clock: FakeClock
) -> None:
    start_run(coordinator, clock)

    assert coordinator.status.is_running


def test_the_status_shows_no_run_once_it_finished(
    coordinator: RecomputeCoordinator, clock: FakeClock, submit: FakeSubmit
) -> None:
    start_run(coordinator, clock)
    submit.succeed()
    coordinator.tick()

    assert coordinator.status == RecomputeStatus(False, None, None)


# Saving the snapshot


def test_a_finished_run_replaces_the_stored_snapshot(
    coordinator: RecomputeCoordinator, current_store: Store, clock: FakeClock, submit: FakeSubmit
) -> None:
    start_run(coordinator, clock)
    submit.succeed(generation=1, html="<p>new</p>")
    coordinator.tick()

    assert current_store.read_snapshot() == Snapshot(1, 2, "<p>new</p>", "{}")


def test_an_older_result_never_overwrites_a_newer_stored_snapshot(
    store: Store, clock: FakeClock, submit: FakeSubmit
) -> None:
    store.write_snapshot(Snapshot(5, 1, "<p>newer</p>", "{}"))
    coordinator = RecomputeCoordinator(submit, store, clock, DEBOUNCE)
    start_run(coordinator, clock)
    submit.succeed(generation=3, html="<p>older</p>")
    coordinator.tick()

    assert store.read_snapshot() == Snapshot(5, 1, "<p>newer</p>", "{}")


def test_a_result_for_the_same_generation_replaces_the_snapshot(
    store: Store, clock: FakeClock, submit: FakeSubmit
) -> None:
    # A case that settles later is added without a write, so the generation stays.
    store.write_snapshot(Snapshot(5, 1, "<p>first</p>", "{}"))
    coordinator = RecomputeCoordinator(submit, store, clock, DEBOUNCE)
    start_run(coordinator, clock)
    submit.succeed(generation=5, html="<p>settled</p>")
    coordinator.tick()

    assert store.read_snapshot() == Snapshot(5, 2, "<p>settled</p>", "{}")


# Failures and backoff


def test_a_failed_run_sets_the_last_error(
    coordinator: RecomputeCoordinator, clock: FakeClock, submit: FakeSubmit
) -> None:
    fail_runs(coordinator, clock, submit, 1)

    assert coordinator.status.last_error == "RuntimeError: boom"


def test_a_failed_run_records_when_it_failed(
    current_store: Store, clock: FakeClock, submit: FakeSubmit
) -> None:
    coordinator = RecomputeCoordinator(submit, current_store, clock, DEBOUNCE, now_ns=lambda: 1_234)
    fail_runs(coordinator, clock, submit, 1)

    assert coordinator.status.last_error_at_ns == 1_234


def test_a_failed_run_keeps_the_old_snapshot(
    coordinator: RecomputeCoordinator, current_store: Store, clock: FakeClock, submit: FakeSubmit
) -> None:
    fail_runs(coordinator, clock, submit, 1)

    assert current_store.read_snapshot() == Snapshot(0, 1, "<p>old</p>", "{}")


def test_a_failed_run_is_not_retried_without_a_write(
    coordinator: RecomputeCoordinator, clock: FakeClock, submit: FakeSubmit
) -> None:
    fail_runs(coordinator, clock, submit, 1)
    clock.advance(10_000)
    coordinator.tick()

    assert submit.count == 1


BACKOFFS = [(1, 5.0), (2, 10.0), (3, 20.0), (4, 40.0), (6, 160.0), (7, 300.0), (9, 300.0)]


@pytest.mark.parametrize(("failures", "backoff"), BACKOFFS)
def test_a_retry_waits_out_the_backoff(
    coordinator: RecomputeCoordinator,
    clock: FakeClock,
    submit: FakeSubmit,
    failures: int,
    backoff: float,
) -> None:
    fail_runs(coordinator, clock, submit, failures)
    coordinator.notify_write()
    clock.advance(backoff - 0.1)
    coordinator.tick()

    assert submit.count == failures


@pytest.mark.parametrize(("failures", "backoff"), BACKOFFS)
def test_a_retry_runs_once_the_backoff_is_over(
    coordinator: RecomputeCoordinator,
    clock: FakeClock,
    submit: FakeSubmit,
    failures: int,
    backoff: float,
) -> None:
    fail_runs(coordinator, clock, submit, failures)
    coordinator.notify_write()
    clock.advance(backoff)
    coordinator.tick()

    assert submit.count == failures + 1


def test_a_success_after_a_failure_clears_the_last_error(
    coordinator: RecomputeCoordinator, clock: FakeClock, submit: FakeSubmit
) -> None:
    fail_runs(coordinator, clock, submit, 1)
    succeed_run(coordinator, clock, submit)

    assert coordinator.status == RecomputeStatus(False, None, None)


def test_a_success_resets_the_backoff(
    coordinator: RecomputeCoordinator, clock: FakeClock, submit: FakeSubmit
) -> None:
    fail_runs(coordinator, clock, submit, 3)
    succeed_run(coordinator, clock, submit)
    fail_runs(coordinator, clock, submit, 1)
    coordinator.notify_write()
    clock.advance(5.0)
    coordinator.tick()

    assert submit.count == 6


def test_a_submit_that_raises_counts_as_a_failed_run(
    current_store: Store, clock: FakeClock
) -> None:
    def broken_submit() -> Future[Snapshot]:
        raise RuntimeError("pool is broken")

    coordinator = RecomputeCoordinator(broken_submit, current_store, clock, DEBOUNCE)
    start_run(coordinator, clock)

    assert coordinator.status.last_error == "RuntimeError: pool is broken"


# Startup


def test_startup_with_input_newer_than_the_snapshot_schedules_a_run(
    store: Store, clock: FakeClock, submit: FakeSubmit
) -> None:
    store.write_snapshot(Snapshot(0, 1, "<p>old</p>", "{}"))
    store.add_issues([Issue(IssueKind.INVALID_SPAN, INGEST_SUBJECT, "x")])
    coordinator = RecomputeCoordinator(submit, store, clock, DEBOUNCE)
    coordinator.tick()

    assert submit.count == 1


def test_startup_without_a_snapshot_schedules_a_run(
    store: Store, clock: FakeClock, submit: FakeSubmit
) -> None:
    coordinator = RecomputeCoordinator(submit, store, clock, DEBOUNCE)
    coordinator.tick()

    assert submit.count == 1


def test_startup_schedules_only_one_run(store: Store, clock: FakeClock, submit: FakeSubmit) -> None:
    coordinator = RecomputeCoordinator(submit, store, clock, DEBOUNCE)
    coordinator.tick()
    submit.succeed(generation=0)
    clock.advance(1_000)
    coordinator.tick()

    assert submit.count == 1


def test_startup_with_a_current_snapshot_schedules_nothing(
    coordinator: RecomputeCoordinator, submit: FakeSubmit
) -> None:
    coordinator.tick()

    assert submit.count == 0


# The run after the settle window


def test_a_run_follows_the_settle_window_after_the_last_write(
    current_store: Store, clock: FakeClock, submit: FakeSubmit
) -> None:
    coordinator = RecomputeCoordinator(
        submit, current_store, clock, DEBOUNCE, settle_seconds=SETTLE_SECONDS
    )
    start_run(coordinator, clock)
    submit.succeed()
    coordinator.tick()
    clock.advance(SETTLE_SECONDS)
    coordinator.tick()

    assert submit.count == 2


def test_no_run_follows_before_the_settle_window_ends(
    current_store: Store, clock: FakeClock, submit: FakeSubmit
) -> None:
    coordinator = RecomputeCoordinator(
        submit, current_store, clock, DEBOUNCE, settle_seconds=SETTLE_SECONDS
    )
    start_run(coordinator, clock)
    submit.succeed()
    coordinator.tick()
    clock.advance(SETTLE_SECONDS - DEBOUNCE - 0.1)
    coordinator.tick()

    assert submit.count == 1


# compute_snapshot


def write_serve_config(folder: Path, **changes: Any) -> Path:
    # A dict per role: one shared dict would be dumped as a YAML alias, which the loader refuses.
    token = {"name": "test", "hash": hash_token("example-token")}
    document: dict[str, Any] = {
        "serve": {"database": "detecttrace.db", "settle_seconds": SETTLE_SECONDS},
        "tokens": {role: [dict(token)] for role in ("ingest", "verdicts", "read")},
        "label_map": {"TP": "true_positive", "FP": "false_positive", "Benign": "benign"},
    }
    document.update(changes)
    path = folder / "serve.yaml"
    path.write_text(yaml.safe_dump(document), encoding="utf-8")
    return path


def add_case(store: Store, number: int, end_ns: int) -> None:
    case_id = f"DT-{number}"
    root = case_root(
        f"{number:016x}",
        case_id,
        verdict="TP",
        trace_id=f"{number:032x}",
        start_ns=end_ns - 1_000,
        end_ns=end_ns,
        attributes={"detecttrace.alert_class": "impossible_travel"},
    )
    store.add_spans([root], [])
    store.put_verdicts([VerdictRow(case_id, "impossible_travel", "TP", 0)], "test")


def results_at(tmp_path: Path, store: Store, now_ns: int) -> dict[str, Any]:
    snapshot = compute_snapshot(tmp_path / "detecttrace.db", write_serve_config(tmp_path), now_ns)
    return json.loads(snapshot.results_json)


@pytest.fixture
def boundary_store(store: Store) -> Store:
    """One case settled long ago and one that ends at CASE_END_NS."""
    add_case(store, 1, CASE_END_NS - 10 * SETTLE_NS)
    add_case(store, 2, CASE_END_NS)
    return store


@pytest.mark.parametrize(
    ("now_ns", "held_back"),
    [
        pytest.param(CASE_END_NS + SETTLE_NS - 1, 1, id="just-before"),
        pytest.param(CASE_END_NS + SETTLE_NS, 0, id="exactly-at"),
        pytest.param(CASE_END_NS + SETTLE_NS + 1, 0, id="just-after"),
    ],
)
def test_a_case_is_held_back_until_it_settles(
    tmp_path: Path, boundary_store: Store, now_ns: int, held_back: int
) -> None:
    results = results_at(tmp_path, boundary_store, now_ns)

    assert results["served"]["held_back_cases"] == held_back


def test_a_held_back_case_is_not_scored(tmp_path: Path, boundary_store: Store) -> None:
    results = results_at(tmp_path, boundary_store, CASE_END_NS + SETTLE_NS - 1)

    assert results["case_rows"]["columns"]["case_id"] == ["DT-1"]


def test_a_held_back_cases_verdict_is_held_back_too(tmp_path: Path, boundary_store: Store) -> None:
    results = results_at(tmp_path, boundary_store, CASE_END_NS + SETTLE_NS - 1)

    assert results["data_notes"] == []


def test_the_served_results_name_where_the_input_came_from(
    tmp_path: Path, boundary_store: Store
) -> None:
    results = results_at(tmp_path, boundary_store, LATE_NS)

    assert results["source"] == {
        "traces": "OTLP/HTTP",
        "verdicts": "verdict API",
        "checklists": None,
        "config": "serve.yaml",
    }


def test_the_snapshot_carries_the_stores_generation(tmp_path: Path, boundary_store: Store) -> None:
    snapshot = compute_snapshot(tmp_path / "detecttrace.db", write_serve_config(tmp_path), LATE_NS)

    assert snapshot.generation == boundary_store.generation()


def test_the_page_carries_its_generation(tmp_path: Path, boundary_store: Store) -> None:
    snapshot = compute_snapshot(tmp_path / "detecttrace.db", write_serve_config(tmp_path), LATE_NS)

    assert f'data-generation="{boundary_store.generation()}"' in snapshot.html


def test_the_page_carries_the_time_it_was_computed(tmp_path: Path, boundary_store: Store) -> None:
    snapshot = compute_snapshot(tmp_path / "detecttrace.db", write_serve_config(tmp_path), LATE_NS)

    assert f'data-updated-at="{to_iso_time(LATE_NS)}"' in snapshot.html


def test_a_stored_issue_count_reaches_the_data_notes(tmp_path: Path, boundary_store: Store) -> None:
    issue = Issue(IssueKind.INVALID_SPAN, INGEST_SUBJECT, "bad span")
    boundary_store.add_issues([issue])
    boundary_store.add_issues([issue])
    boundary_store.add_issues([issue])
    results = results_at(tmp_path, boundary_store, LATE_NS)

    assert [note["message"] for note in results["data_notes"]] == [
        "3 spans are malformed and were skipped."
    ]


def test_to_iso_time_keeps_microseconds_in_utc() -> None:
    assert to_iso_time(1_700_000_000_123_456_789) == "2023-11-14T22:13:20.123456Z"


# The waiting page


def test_no_scorable_case_gives_waiting_results(tmp_path: Path, store: Store) -> None:
    add_case(store, 1, CASE_END_NS)
    results = results_at(tmp_path, store, CASE_END_NS)

    assert results == {
        "status": "waiting",
        "generation": store.generation(),
        "span_count": 1,
        "case_count": 0,
        "held_back_count": 1,
        "verdict_count": 0,
        "data_notes": [],
    }


def test_an_empty_store_gives_the_waiting_page(tmp_path: Path, store: Store) -> None:
    snapshot = compute_snapshot(tmp_path / "detecttrace.db", write_serve_config(tmp_path), LATE_NS)

    assert "Waiting for data." in snapshot.html


def test_the_waiting_page_shows_why_nothing_joined(tmp_path: Path, store: Store) -> None:
    store.put_verdicts([VerdictRow("DT-9", "impossible_travel", "TP", 0)], "test")
    snapshot = compute_snapshot(tmp_path / "detecttrace.db", write_serve_config(tmp_path), LATE_NS)

    assert "1 verdict has no matching trace." in snapshot.html


def test_the_waiting_page_checks_for_new_data(tmp_path: Path, store: Store) -> None:
    snapshot = compute_snapshot(tmp_path / "detecttrace.db", write_serve_config(tmp_path), LATE_NS)

    assert "connect-src 'self'" in snapshot.html


def test_the_waiting_page_loads_nothing_from_elsewhere(tmp_path: Path, store: Store) -> None:
    snapshot = compute_snapshot(tmp_path / "detecttrace.db", write_serve_config(tmp_path), LATE_NS)

    assert re.findall(r"(?:https?:)?//[\w.-]+", snapshot.html) == []


# The worker process gives the CLI's numbers


def test_a_spawned_worker_gives_the_clis_results_on_the_demo(tmp_path: Path) -> None:
    store = Store.open(tmp_path / "detecttrace.db")
    spans, span_issues = load_spans(DEMO_DIR / "traces")
    store.add_spans(spans, span_issues)
    verdict_rows, _ = read_verdicts(DEMO_DIR / "verdicts.csv")
    store.put_verdicts(verdict_rows, "test")
    store.close()
    demo_config = yaml.safe_load((DEMO_DIR / "detecttrace.yaml").read_text(encoding="utf-8"))
    config_path = write_serve_config(
        tmp_path,
        label_map=demo_config["label_map"],
        checklists=str(DEMO_DIR / "checklists"),
    )
    context = multiprocessing.get_context("spawn")
    with ProcessPoolExecutor(max_workers=1, mp_context=context) as executor:
        snapshot = executor.submit(
            compute_snapshot, tmp_path / "detecttrace.db", config_path, 2**62
        ).result(timeout=120)
    served = json.loads(snapshot.results_json)
    expected = run_check(
        load_run_config(DEMO_DIR / "detecttrace.yaml"), DEMO_DIR / "detecttrace.yaml"
    ).results

    assert {**served, "source": None, "served": None} == {
        **expected,
        "source": None,
        "served": None,
    }
