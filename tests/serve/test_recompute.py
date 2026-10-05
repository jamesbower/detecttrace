import json
import multiprocessing
import re
import shutil
from collections.abc import Callable, Iterator
from concurrent.futures import Future, ProcessPoolExecutor
from pathlib import Path
from typing import Any

import pytest
import yaml
from builders import case_root
from html_tree import has_tag, parse_html, read_terms

from detecttrace.dashboard import ServedPage, WaitingCounts, render_waiting_page
from detecttrace.model import Issue, IssueKind, VerdictRow
from detecttrace.pipeline import run_check
from detecttrace.runconfig import load_run_config
from detecttrace.serve.auth import hash_token
from detecttrace.serve.config import load_serve_config
from detecttrace.serve.recompute import (
    RecomputeCoordinator,
    RecomputeOutcome,
    RecomputeSettings,
    RecomputeStatus,
    compute_snapshot,
    load_recompute_settings,
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
# What a stored dashboard snapshot that held nothing back says about itself.
OLD_RESULTS = '{"served": {"held_back_cases": 0}}'


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
        self.futures: list[Future[RecomputeOutcome]] = []

    def __call__(self) -> Future[RecomputeOutcome]:
        future: Future[RecomputeOutcome] = Future()
        self.futures.append(future)
        return future

    @property
    def count(self) -> int:
        return len(self.futures)

    def succeed(
        self,
        generation: int = 1,
        html: str = "<p>new</p>",
        *,
        held_back_cases: int = 0,
        next_settle_at_ns: int | None = None,
    ) -> None:
        snapshot = Snapshot(generation, 2, html, "{}")
        self.futures[-1].set_result(RecomputeOutcome(snapshot, held_back_cases, next_settle_at_ns))

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
    store.write_snapshot(Snapshot(0, 1, "<p>old</p>", OLD_RESULTS))
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


def write_input(store: Store, coordinator: RecomputeCoordinator) -> None:
    """Change the stored input and tell the coordinator, as a request that stores data does."""
    detail = f"write {store.generation()}"
    store.add_issues([Issue(IssueKind.INVALID_SPAN, INGEST_SUBJECT, detail)])
    coordinator.notify_write()


def start_run(coordinator: RecomputeCoordinator, store: Store, clock: FakeClock) -> None:
    write_input(store, coordinator)
    clock.advance(DEBOUNCE)
    coordinator.tick()


def fail_runs(
    coordinator: RecomputeCoordinator,
    store: Store,
    clock: FakeClock,
    submit: FakeSubmit,
    count: int,
) -> None:
    """Fail `count` runs in a row, each started by a write; the clock ends at the last failure."""
    for _ in range(count):
        write_input(store, coordinator)
        clock.advance(1_000)
        coordinator.tick()
        submit.fail()
        coordinator.tick()


def succeed_run(
    coordinator: RecomputeCoordinator, store: Store, clock: FakeClock, submit: FakeSubmit
) -> None:
    write_input(store, coordinator)
    clock.advance(1_000)
    coordinator.tick()
    submit.succeed()
    coordinator.tick()


# Debounce


def test_no_run_starts_before_the_quiet_period(
    coordinator: RecomputeCoordinator, current_store: Store, clock: FakeClock, submit: FakeSubmit
) -> None:
    write_input(current_store, coordinator)
    clock.advance(DEBOUNCE - 0.1)
    coordinator.tick()

    assert submit.count == 0


def test_one_run_starts_after_the_quiet_period(
    coordinator: RecomputeCoordinator, current_store: Store, clock: FakeClock, submit: FakeSubmit
) -> None:
    start_run(coordinator, current_store, clock)

    assert submit.count == 1


def test_a_later_write_restarts_the_quiet_period(
    coordinator: RecomputeCoordinator, current_store: Store, clock: FakeClock, submit: FakeSubmit
) -> None:
    write_input(current_store, coordinator)
    clock.advance(3)
    write_input(current_store, coordinator)
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
    store: Store,
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
        write_input(store, coordinator)
        coordinator.tick()
        if submit.count > len(starts):
            starts.append(elapsed)
        clock.advance(1)
    return starts


def test_steady_writes_start_the_first_run_at_the_maximum_wait(
    coordinator: RecomputeCoordinator, current_store: Store, clock: FakeClock, submit: FakeSubmit
) -> None:
    starts = run_start_times(coordinator, current_store, clock, submit, 130)

    assert starts[0] == 60.0


def test_steady_writes_start_the_next_run_a_maximum_wait_after_the_first_uncovered_write(
    coordinator: RecomputeCoordinator, current_store: Store, clock: FakeClock, submit: FakeSubmit
) -> None:
    # The write at 60 s is covered by the run that starts in the same second; 61 s is next.
    starts = run_start_times(coordinator, current_store, clock, submit, 130)

    assert starts == [60.0, 121.0]


def test_a_run_longer_than_the_maximum_wait_is_followed_as_soon_as_it_finishes(
    coordinator: RecomputeCoordinator, current_store: Store, clock: FakeClock, submit: FakeSubmit
) -> None:
    starts = run_start_times(coordinator, current_store, clock, submit, 160, run_seconds=70)

    assert starts == [60.0, 130.0]


def test_a_single_write_still_runs_after_the_quiet_period_alone(
    current_store: Store, clock: FakeClock, submit: FakeSubmit
) -> None:
    coordinator = RecomputeCoordinator(
        submit, current_store, clock, DEBOUNCE, max_wait_seconds=1_000
    )
    start_run(coordinator, current_store, clock)

    assert submit.count == 1


# Single flight and the rerun after a busy run


def test_no_second_run_starts_while_one_runs(
    coordinator: RecomputeCoordinator, current_store: Store, clock: FakeClock, submit: FakeSubmit
) -> None:
    start_run(coordinator, current_store, clock)
    write_input(current_store, coordinator)
    clock.advance(1_000)
    coordinator.tick()

    assert submit.count == 1


def test_writes_during_a_run_cause_one_more_run_after_it(
    coordinator: RecomputeCoordinator, current_store: Store, clock: FakeClock, submit: FakeSubmit
) -> None:
    start_run(coordinator, current_store, clock)
    write_input(current_store, coordinator)
    write_input(current_store, coordinator)
    clock.advance(DEBOUNCE)
    submit.succeed()
    coordinator.tick()

    assert submit.count == 2


def test_writes_during_a_run_cause_no_more_than_one_more_run(
    coordinator: RecomputeCoordinator, current_store: Store, clock: FakeClock, submit: FakeSubmit
) -> None:
    start_run(coordinator, current_store, clock)
    write_input(current_store, coordinator)
    write_input(current_store, coordinator)
    clock.advance(DEBOUNCE)
    submit.succeed()
    coordinator.tick()
    submit.succeed(generation=2)
    clock.advance(1_000)
    coordinator.tick()

    assert submit.count == 2


def test_a_run_without_writes_during_it_is_not_repeated(
    coordinator: RecomputeCoordinator, current_store: Store, clock: FakeClock, submit: FakeSubmit
) -> None:
    start_run(coordinator, current_store, clock)
    submit.succeed()
    clock.advance(1_000)
    coordinator.tick()
    coordinator.tick()

    assert submit.count == 1


def test_the_status_shows_a_run_in_progress(
    coordinator: RecomputeCoordinator, current_store: Store, clock: FakeClock
) -> None:
    start_run(coordinator, current_store, clock)

    assert coordinator.status.is_running


def test_the_status_shows_no_run_once_it_finished(
    coordinator: RecomputeCoordinator, current_store: Store, clock: FakeClock, submit: FakeSubmit
) -> None:
    start_run(coordinator, current_store, clock)
    submit.succeed()
    coordinator.tick()

    assert coordinator.status == RecomputeStatus(False, None, None, 0)


# Writes that change nothing stored

STORED_SPAN = case_root("0000000000000001", "DT-1", verdict="TP", trace_id=f"{1:032x}")
STORED_ISSUE = Issue(
    IssueKind.REFUSED_TRACE_REQUEST, INGEST_SUBJECT, "HTTP 400: the request body is empty"
)
STORED_VERDICT = VerdictRow("DT-1", "impossible_travel", "TP", 0)


@pytest.fixture
def covered(current_store: Store, clock: FakeClock, submit: FakeSubmit) -> RecomputeCoordinator:
    """A coordinator whose last run covered a stored span, issue and verdict."""
    coordinator = RecomputeCoordinator(submit, current_store, clock, DEBOUNCE)
    current_store.add_spans([STORED_SPAN], [])
    current_store.add_issues([STORED_ISSUE])
    current_store.put_verdicts([STORED_VERDICT], "soar")
    coordinator.notify_write()
    clock.advance(DEBOUNCE)
    coordinator.tick()
    submit.succeed(generation=current_store.generation())
    coordinator.tick()
    return coordinator


def retry_the_span_batch(store: Store) -> None:
    store.add_spans([STORED_SPAN], [])


def repeat_the_bad_body(store: Store) -> None:
    store.add_issues([STORED_ISSUE])


def repost_the_verdict(store: Store) -> None:
    store.put_verdicts([STORED_VERDICT], "soar")


@pytest.mark.parametrize("repeat", [retry_the_span_batch, repeat_the_bad_body, repost_the_verdict])
def test_a_write_that_changes_nothing_stored_starts_no_run(
    covered: RecomputeCoordinator,
    current_store: Store,
    clock: FakeClock,
    submit: FakeSubmit,
    repeat: Callable[[Store], None],
) -> None:
    repeat(current_store)
    covered.notify_write()
    clock.advance(1_000)
    covered.tick()

    assert submit.count == 1


def test_a_write_that_changes_the_input_starts_one_run(
    covered: RecomputeCoordinator, current_store: Store, clock: FakeClock, submit: FakeSubmit
) -> None:
    current_store.put_verdicts([VerdictRow("DT-1", "impossible_travel", "FP", 0)], "soar")
    covered.notify_write()
    clock.advance(1_000)
    covered.tick()
    clock.advance(1_000)
    covered.tick()

    assert submit.count == 2


def test_a_write_that_changed_nothing_does_not_shorten_the_next_quiet_period(
    covered: RecomputeCoordinator, current_store: Store, clock: FakeClock, submit: FakeSubmit
) -> None:
    repost_the_verdict(current_store)
    covered.notify_write()
    clock.advance(1_000)
    covered.tick()
    write_input(current_store, covered)
    clock.advance(DEBOUNCE - 0.1)
    covered.tick()

    assert submit.count == 1


def test_a_failed_run_is_not_retried_after_a_write_that_changed_nothing(
    coordinator: RecomputeCoordinator, current_store: Store, clock: FakeClock, submit: FakeSubmit
) -> None:
    fail_runs(coordinator, current_store, clock, submit, 1)
    coordinator.notify_write()
    clock.advance(1_000)
    coordinator.tick()

    assert submit.count == 1


# Saving the snapshot


def test_a_finished_run_replaces_the_stored_snapshot(
    coordinator: RecomputeCoordinator, current_store: Store, clock: FakeClock, submit: FakeSubmit
) -> None:
    start_run(coordinator, current_store, clock)
    submit.succeed(generation=1, html="<p>new</p>")
    coordinator.tick()

    assert current_store.read_snapshot() == Snapshot(1, 2, "<p>new</p>", "{}")


def test_an_older_result_never_overwrites_a_newer_stored_snapshot(
    store: Store, clock: FakeClock, submit: FakeSubmit
) -> None:
    store.write_snapshot(Snapshot(5, 1, "<p>newer</p>", "{}"))
    coordinator = RecomputeCoordinator(submit, store, clock, DEBOUNCE)
    start_run(coordinator, store, clock)
    submit.succeed(generation=3, html="<p>older</p>")
    coordinator.tick()

    assert store.read_snapshot() == Snapshot(5, 1, "<p>newer</p>", "{}")


def test_a_result_for_the_same_generation_replaces_the_snapshot(
    store: Store, clock: FakeClock, submit: FakeSubmit
) -> None:
    # A case that settles later is added without a write, so the generation stays.
    store.write_snapshot(Snapshot(5, 1, "<p>first</p>", "{}"))
    coordinator = RecomputeCoordinator(submit, store, clock, DEBOUNCE)
    start_run(coordinator, store, clock)
    submit.succeed(generation=5, html="<p>settled</p>")
    coordinator.tick()

    assert store.read_snapshot() == Snapshot(5, 2, "<p>settled</p>", "{}")


# Failures and backoff


def test_a_failed_run_sets_the_last_error(
    coordinator: RecomputeCoordinator, current_store: Store, clock: FakeClock, submit: FakeSubmit
) -> None:
    fail_runs(coordinator, current_store, clock, submit, 1)

    assert coordinator.status.last_error == "RuntimeError: boom"


def test_a_failed_run_records_when_it_failed(
    current_store: Store, clock: FakeClock, submit: FakeSubmit
) -> None:
    coordinator = RecomputeCoordinator(submit, current_store, clock, DEBOUNCE, now_ns=lambda: 1_234)
    fail_runs(coordinator, current_store, clock, submit, 1)

    assert coordinator.status.last_error_at_ns == 1_234


def test_a_failed_run_keeps_the_old_snapshot(
    coordinator: RecomputeCoordinator, current_store: Store, clock: FakeClock, submit: FakeSubmit
) -> None:
    fail_runs(coordinator, current_store, clock, submit, 1)

    assert current_store.read_snapshot() == Snapshot(0, 1, "<p>old</p>", OLD_RESULTS)


def test_a_failed_run_is_not_retried_without_a_write(
    coordinator: RecomputeCoordinator, current_store: Store, clock: FakeClock, submit: FakeSubmit
) -> None:
    fail_runs(coordinator, current_store, clock, submit, 1)
    clock.advance(10_000)
    coordinator.tick()

    assert submit.count == 1


BACKOFFS = [(1, 5.0), (2, 10.0), (3, 20.0), (4, 40.0), (6, 160.0), (7, 300.0), (9, 300.0)]


@pytest.mark.parametrize(("failures", "backoff"), BACKOFFS)
def test_a_retry_waits_out_the_backoff(
    coordinator: RecomputeCoordinator,
    current_store: Store,
    clock: FakeClock,
    submit: FakeSubmit,
    failures: int,
    backoff: float,
) -> None:
    fail_runs(coordinator, current_store, clock, submit, failures)
    write_input(current_store, coordinator)
    clock.advance(backoff - 0.1)
    coordinator.tick()

    assert submit.count == failures


@pytest.mark.parametrize(("failures", "backoff"), BACKOFFS)
def test_a_retry_runs_once_the_backoff_is_over(
    coordinator: RecomputeCoordinator,
    current_store: Store,
    clock: FakeClock,
    submit: FakeSubmit,
    failures: int,
    backoff: float,
) -> None:
    fail_runs(coordinator, current_store, clock, submit, failures)
    write_input(current_store, coordinator)
    clock.advance(backoff)
    coordinator.tick()

    assert submit.count == failures + 1


def test_a_success_after_a_failure_clears_the_last_error(
    coordinator: RecomputeCoordinator, current_store: Store, clock: FakeClock, submit: FakeSubmit
) -> None:
    fail_runs(coordinator, current_store, clock, submit, 1)
    succeed_run(coordinator, current_store, clock, submit)

    assert coordinator.status == RecomputeStatus(False, None, None, 0)


def test_a_success_resets_the_backoff(
    coordinator: RecomputeCoordinator, current_store: Store, clock: FakeClock, submit: FakeSubmit
) -> None:
    fail_runs(coordinator, current_store, clock, submit, 3)
    succeed_run(coordinator, current_store, clock, submit)
    fail_runs(coordinator, current_store, clock, submit, 1)
    write_input(current_store, coordinator)
    clock.advance(5.0)
    coordinator.tick()

    assert submit.count == 6


def test_a_submit_that_raises_counts_as_a_failed_run(
    current_store: Store, clock: FakeClock
) -> None:
    def broken_submit() -> Future[RecomputeOutcome]:
        raise RuntimeError("pool is broken")

    coordinator = RecomputeCoordinator(broken_submit, current_store, clock, DEBOUNCE)
    start_run(coordinator, current_store, clock)

    assert coordinator.status.last_error == "RuntimeError: pool is broken"


# Startup


def test_startup_with_input_newer_than_the_snapshot_schedules_a_run(
    store: Store, clock: FakeClock, submit: FakeSubmit
) -> None:
    store.write_snapshot(Snapshot(0, 1, "<p>old</p>", OLD_RESULTS))
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


# The run when held-back cases settle

SETTLE_DUE_NS = 10 * 1_000_000_000


@pytest.fixture
def settling(current_store: Store, clock: FakeClock, submit: FakeSubmit) -> RecomputeCoordinator:
    """A coordinator whose first run held 2 cases back, the first settling at wall time 10 s."""
    coordinator = RecomputeCoordinator(submit, current_store, clock, DEBOUNCE, now_ns=lambda: 0)
    start_run(coordinator, current_store, clock)
    submit.succeed(held_back_cases=2, next_settle_at_ns=SETTLE_DUE_NS)
    coordinator.tick()
    return coordinator


def test_a_run_follows_when_the_first_held_back_case_settles(
    settling: RecomputeCoordinator, clock: FakeClock, submit: FakeSubmit
) -> None:
    clock.advance(10)
    settling.tick()

    assert submit.count == 2


def test_no_run_follows_before_the_first_held_back_case_settles(
    settling: RecomputeCoordinator, clock: FakeClock, submit: FakeSubmit
) -> None:
    clock.advance(9.9)
    settling.tick()

    assert submit.count == 1


def test_the_status_shows_how_many_cases_were_held_back(settling: RecomputeCoordinator) -> None:
    assert settling.status.held_back_cases == 2


def test_no_run_follows_when_nothing_is_held_back(
    coordinator: RecomputeCoordinator, current_store: Store, clock: FakeClock, submit: FakeSubmit
) -> None:
    start_run(coordinator, current_store, clock)
    submit.succeed(next_settle_at_ns=None)
    coordinator.tick()
    clock.advance(10_000)
    coordinator.tick()

    assert submit.count == 1


def test_a_settle_time_already_past_still_waits_a_second(
    current_store: Store, clock: FakeClock, submit: FakeSubmit
) -> None:
    coordinator = RecomputeCoordinator(submit, current_store, clock, DEBOUNCE, now_ns=lambda: 0)
    start_run(coordinator, current_store, clock)
    submit.succeed(held_back_cases=1, next_settle_at_ns=-SETTLE_DUE_NS)
    coordinator.tick()

    assert submit.count == 1


def test_a_failure_after_a_settle_was_scheduled_is_not_retried_without_a_write(
    settling: RecomputeCoordinator, current_store: Store, clock: FakeClock, submit: FakeSubmit
) -> None:
    # The write-triggered run starts before the settle time and fails; the settle run is dropped.
    write_input(current_store, settling)
    clock.advance(DEBOUNCE)
    settling.tick()
    submit.fail()
    settling.tick()
    clock.advance(10_000)
    settling.tick()

    assert submit.count == 2


@pytest.mark.parametrize(
    "results_json",
    [
        pytest.param('{"served": {"held_back_cases": 2}}', id="dashboard"),
        pytest.param('{"status": "waiting", "held_back_count": 1}', id="waiting"),
    ],
)
def test_startup_with_held_back_cases_in_the_snapshot_schedules_a_run(
    store: Store, clock: FakeClock, submit: FakeSubmit, results_json: str
) -> None:
    store.write_snapshot(Snapshot(0, 1, "<p>old</p>", results_json))
    coordinator = RecomputeCoordinator(submit, store, clock, DEBOUNCE)
    coordinator.tick()

    assert submit.count == 1


UNREADABLE_RESULTS = [
    pytest.param("not json", id="not-json"),
    pytest.param("[]", id="list"),
    pytest.param('{"served": null}', id="served-null"),
    pytest.param('{"held_back_count": "3"}', id="no-status"),
    pytest.param('{"status": "waiting", "held_back_count": "3"}', id="text-count"),
    pytest.param('{"served": {"held_back_cases": true}}', id="bool-count"),
    pytest.param('{"served": {"held_back_cases": -1}}', id="negative-count"),
]


@pytest.mark.parametrize("results_json", UNREADABLE_RESULTS)
def test_startup_with_an_unreadable_snapshot_schedules_a_run(
    store: Store, clock: FakeClock, submit: FakeSubmit, results_json: str
) -> None:
    store.write_snapshot(Snapshot(0, 1, "<p>old</p>", results_json))
    coordinator = RecomputeCoordinator(submit, store, clock, DEBOUNCE)
    coordinator.tick()

    assert submit.count == 1


@pytest.mark.parametrize("results_json", UNREADABLE_RESULTS)
def test_an_unreadable_snapshot_reports_no_held_back_cases(
    store: Store, clock: FakeClock, submit: FakeSubmit, results_json: str
) -> None:
    store.write_snapshot(Snapshot(0, 1, "<p>old</p>", results_json))
    coordinator = RecomputeCoordinator(submit, store, clock, DEBOUNCE)

    assert coordinator.status.held_back_cases == 0


def test_startup_with_input_newer_than_the_snapshot_reports_its_held_back_cases(
    store: Store, clock: FakeClock, submit: FakeSubmit
) -> None:
    # The served page says the cases are settling; the status must agree before the next run.
    store.write_snapshot(Snapshot(0, 1, "<p>old</p>", '{"served": {"held_back_cases": 2}}'))
    store.add_issues([Issue(IssueKind.INVALID_SPAN, INGEST_SUBJECT, "x")])
    coordinator = RecomputeCoordinator(submit, store, clock, DEBOUNCE)

    assert coordinator.status.held_back_cases == 2


def tick_each_second(coordinator: RecomputeCoordinator, clock: FakeClock, seconds: int) -> None:
    for _ in range(seconds):
        clock.advance(1)
        coordinator.tick()


def test_a_case_ending_ahead_of_the_server_clock_is_scored_without_a_new_write(
    tmp_path: Path, store: Store, clock: FakeClock
) -> None:
    # The agent's clock runs 2 s ahead of the server's, so its case ends "in the future".
    add_case(store, 1, CASE_END_NS)
    store.write_snapshot(Snapshot(store.generation(), 1, "<p>old</p>", OLD_RESULTS))
    config_path = write_serve_config(tmp_path)
    started_at = clock.now

    def wall_ns() -> int:
        return CASE_END_NS - 2 * 1_000_000_000 + round((clock.now - started_at) * 1e9)

    def submit_now() -> Future[RecomputeOutcome]:
        future: Future[RecomputeOutcome] = Future()
        future.set_result(
            compute_snapshot(tmp_path / "detecttrace.db", to_settings(config_path), wall_ns())
        )
        return future

    coordinator = RecomputeCoordinator(submit_now, store, clock, DEBOUNCE, now_ns=wall_ns)
    start_run(coordinator, store, clock)
    tick_each_second(coordinator, clock, 2 * SETTLE_SECONDS)
    snapshot = store.read_snapshot()
    results = json.loads(snapshot.results_json) if snapshot is not None else {}

    assert results["case_rows"]["columns"]["case_id"] == ["DT-1"]


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


def to_settings(config_path: Path) -> RecomputeSettings:
    return load_recompute_settings(load_serve_config(config_path), config_path)


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
    outcome = compute_snapshot(
        tmp_path / "detecttrace.db", to_settings(write_serve_config(tmp_path)), now_ns
    )
    return json.loads(outcome.snapshot.results_json)


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


def next_settle_at(tmp_path: Path, now_ns: int) -> int | None:
    database = tmp_path / "detecttrace.db"
    return compute_snapshot(
        database, to_settings(write_serve_config(tmp_path)), now_ns
    ).next_settle_at_ns


def test_the_outcome_says_when_the_held_back_case_settles(
    tmp_path: Path, boundary_store: Store
) -> None:
    assert next_settle_at(tmp_path, CASE_END_NS + SETTLE_NS - 1) == CASE_END_NS + SETTLE_NS


def test_the_outcome_says_when_the_first_of_several_held_back_cases_settles(
    tmp_path: Path, boundary_store: Store
) -> None:
    add_case(boundary_store, 3, CASE_END_NS + 5 * 1_000_000_000)

    assert next_settle_at(tmp_path, CASE_END_NS + SETTLE_NS - 1) == CASE_END_NS + SETTLE_NS


def test_the_outcome_has_no_settle_time_when_nothing_is_held_back(
    tmp_path: Path, boundary_store: Store
) -> None:
    assert next_settle_at(tmp_path, LATE_NS) is None


# A case ends more than a settle window after the server's now: its agent's clock runs ahead.
FUTURE_NOW_NS = CASE_END_NS - SETTLE_NS - 1
FUTURE_NOTE = (
    "1 case ends more than serve.settle_seconds after the server's current time, "
    "so it is not counted yet."
)


def test_a_future_dated_case_gets_a_data_note_on_the_dashboard(
    tmp_path: Path, boundary_store: Store
) -> None:
    results = results_at(tmp_path, boundary_store, FUTURE_NOW_NS)

    assert [note["message"] for note in results["data_notes"]] == [FUTURE_NOTE]


def test_a_future_dated_case_gets_a_data_note_on_the_waiting_page(
    tmp_path: Path, store: Store
) -> None:
    add_case(store, 1, CASE_END_NS)
    results = results_at(tmp_path, store, FUTURE_NOW_NS)

    assert results["data_notes"] == [FUTURE_NOTE]


def test_the_waiting_page_shows_the_future_dated_case_note(tmp_path: Path, store: Store) -> None:
    add_case(store, 1, CASE_END_NS)
    database = tmp_path / "detecttrace.db"
    outcome = compute_snapshot(database, to_settings(write_serve_config(tmp_path)), FUTURE_NOW_NS)

    assert FUTURE_NOTE in parse_html(outcome.snapshot.html).find(has_tag("ol")).text()


def test_a_case_exactly_a_settle_window_ahead_gets_no_data_note(
    tmp_path: Path, store: Store
) -> None:
    add_case(store, 1, CASE_END_NS)
    results = results_at(tmp_path, store, CASE_END_NS - SETTLE_NS)

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


def test_the_served_results_say_what_was_held_back(tmp_path: Path, boundary_store: Store) -> None:
    results = results_at(tmp_path, boundary_store, CASE_END_NS + SETTLE_NS - 1)

    assert results["served"] == {
        "generation": boundary_store.generation(),
        "settle_seconds": SETTLE_SECONDS,
        "held_back_cases": 1,
    }


def test_the_dashboard_says_a_case_is_still_settling(tmp_path: Path, boundary_store: Store) -> None:
    snapshot = compute_snapshot(
        tmp_path / "detecttrace.db",
        to_settings(write_serve_config(tmp_path)),
        CASE_END_NS + SETTLE_NS - 1,
    ).snapshot

    assert "1 case still settling is not counted yet." in snapshot.html


def test_the_served_results_name_a_nested_checklist_folder_as_written(
    tmp_path: Path, boundary_store: Store
) -> None:
    folder = tmp_path / "rules" / "checklists"
    folder.mkdir(parents=True)
    shutil.copy(DEMO_DIR / "checklists" / "impossible_travel.yaml", folder)
    config_path = write_serve_config(tmp_path, checklists="rules/checklists")
    results = json.loads(
        compute_snapshot(
            tmp_path / "detecttrace.db", to_settings(config_path), LATE_NS
        ).snapshot.results_json
    )

    assert results["source"]["checklists"] == "rules/checklists"


def test_the_snapshot_carries_the_stores_generation(tmp_path: Path, boundary_store: Store) -> None:
    snapshot = compute_snapshot(
        tmp_path / "detecttrace.db", to_settings(write_serve_config(tmp_path)), LATE_NS
    ).snapshot

    assert snapshot.generation == boundary_store.generation()


def test_the_page_carries_its_generation(tmp_path: Path, boundary_store: Store) -> None:
    snapshot = compute_snapshot(
        tmp_path / "detecttrace.db", to_settings(write_serve_config(tmp_path)), LATE_NS
    ).snapshot

    assert f'data-generation="{boundary_store.generation()}"' in snapshot.html


def test_the_page_carries_the_time_it_was_computed(tmp_path: Path, boundary_store: Store) -> None:
    snapshot = compute_snapshot(
        tmp_path / "detecttrace.db", to_settings(write_serve_config(tmp_path)), LATE_NS
    ).snapshot

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


def test_a_run_uses_the_configuration_read_at_startup_not_the_file(
    tmp_path: Path, boundary_store: Store
) -> None:
    config_path = write_serve_config(tmp_path)
    settings = to_settings(config_path)
    config_path.write_text("not: [a valid configuration", encoding="utf-8")
    results = json.loads(
        compute_snapshot(tmp_path / "detecttrace.db", settings, LATE_NS).snapshot.results_json
    )

    assert results["source"]["config"] == "serve.yaml"


def test_to_iso_time_keeps_microseconds_in_utc() -> None:
    assert to_iso_time(1_700_000_000_123_456_789) == "2023-11-14T22:13:20.123456Z"


# The waiting page

UNMATCHED_VERDICT_HINT = (
    "Check mapping.case_id in serve.yaml and that the traces cover the same cases."
)


def test_no_scorable_case_gives_waiting_results(tmp_path: Path, store: Store) -> None:
    add_case(store, 1, CASE_END_NS)
    results = results_at(tmp_path, store, CASE_END_NS)

    assert results == {
        "status": "waiting",
        "generation": store.generation(),
        "span_count": 1,
        "case_count": 0,
        "held_back_count": 1,
        "verdict_count": 1,
        "data_notes": [],
    }


def test_an_empty_store_gives_the_waiting_page(tmp_path: Path, store: Store) -> None:
    snapshot = compute_snapshot(
        tmp_path / "detecttrace.db", to_settings(write_serve_config(tmp_path)), LATE_NS
    ).snapshot

    assert "Waiting for data." in snapshot.html


def test_the_waiting_page_shows_why_nothing_joined(tmp_path: Path, store: Store) -> None:
    store.put_verdicts([VerdictRow("DT-9", "impossible_travel", "TP", 0)], "test")
    snapshot = compute_snapshot(
        tmp_path / "detecttrace.db", to_settings(write_serve_config(tmp_path)), LATE_NS
    ).snapshot

    assert "1 verdict has no matching trace." in snapshot.html


def test_the_waiting_page_carries_the_stores_generation(tmp_path: Path, store: Store) -> None:
    store.put_verdicts([VerdictRow("DT-9", "impossible_travel", "TP", 0)], "test")
    snapshot = compute_snapshot(
        tmp_path / "detecttrace.db", to_settings(write_serve_config(tmp_path)), LATE_NS
    ).snapshot

    assert f'data-generation="{store.generation()}"' in snapshot.html


def test_the_waiting_page_shows_each_notes_fix_hint(tmp_path: Path, store: Store) -> None:
    store.put_verdicts([VerdictRow("DT-9", "impossible_travel", "TP", 0)], "test")
    snapshot = compute_snapshot(
        tmp_path / "detecttrace.db", to_settings(write_serve_config(tmp_path)), LATE_NS
    ).snapshot
    hints = parse_html(snapshot.html).find_all(lambda node: "hint" in node.classes())

    assert [hint.text() for hint in hints] == [UNMATCHED_VERDICT_HINT]


@pytest.mark.parametrize(
    ("label", "value"),
    [
        ("Spans received", "11"),
        ("Cases settled", "2"),
        ("Cases still settling", "3"),
        ("Verdicts received", "5"),
    ],
)
def test_the_waiting_page_shows_each_count_under_its_label(label: str, value: str) -> None:
    counts = WaitingCounts(span_count=11, case_count=2, held_back_count=3, verdict_count=5)
    html = render_waiting_page(counts, [], ServedPage(1, "", 0))

    assert read_terms(parse_html(html).find(has_tag("dl", **{"class": "meta"})))[label] == value


def test_the_waiting_page_checks_for_new_data(tmp_path: Path, store: Store) -> None:
    snapshot = compute_snapshot(
        tmp_path / "detecttrace.db", to_settings(write_serve_config(tmp_path)), LATE_NS
    ).snapshot

    assert "connect-src 'self'" in snapshot.html


def test_the_waiting_page_loads_nothing_from_elsewhere(tmp_path: Path, store: Store) -> None:
    snapshot = compute_snapshot(
        tmp_path / "detecttrace.db", to_settings(write_serve_config(tmp_path)), LATE_NS
    ).snapshot

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
        outcome = executor.submit(
            compute_snapshot, tmp_path / "detecttrace.db", to_settings(config_path), 2**62
        ).result(timeout=120)
    served = json.loads(outcome.snapshot.results_json)
    expected = run_check(
        load_run_config(DEMO_DIR / "detecttrace.yaml"), DEMO_DIR / "detecttrace.yaml"
    ).results

    assert {**served, "source": None, "served": None} == {
        **expected,
        "source": None,
        "served": None,
    }
