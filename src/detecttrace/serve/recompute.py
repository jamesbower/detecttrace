"""Background recompute for `detecttrace serve`: the CLI's pipeline over the stored input.

`compute_snapshot` runs in a worker process, so the CPU-bound pipeline never holds the
server's GIL. `RecomputeCoordinator` lives in the server: request threads tell it about
writes, a timer thread calls `tick` every second, and it decides when the worker runs. No
request ever waits for a recompute; a page reads the last finished snapshot.

A case is held back while its root span ended less than `serve.settle_seconds` ago, because
its tool spans and verdict may still be on their way; its verdict is held back with it, so it
doesn't read as a verdict without a trace. The results of a served run carry a `served` entry
with the generation, the settle window and how many cases were held back.

With no case to score, the snapshot is a small waiting page, and its results JSON is
`{"status": "waiting", "generation": ..., "span_count": ..., "case_count": ...,
"held_back_count": ..., "verdict_count": ..., "data_notes": [...]}`.
"""

import json
import logging
import threading
import time
from collections.abc import Callable
from concurrent.futures import Future
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path

from detecttrace.cases import build_trace_cases
from detecttrace.dashboard import ServedPage, WaitingCounts, render_dashboard, render_waiting_page
from detecttrace.model import Issue
from detecttrace.pipeline import load_run_checklists, run_stages, to_source
from detecttrace.serve.config import load_serve_config
from detecttrace.serve.store import Snapshot, Store

logger = logging.getLogger(__name__)

TRACES_SOURCE = "OTLP/HTTP"
VERDICTS_SOURCE = "verdict API"
FIRST_BACKOFF_SECONDS = 5.0
MAX_BACKOFF_SECONDS = 300.0
_NS_PER_SECOND = 1_000_000_000


def compute_snapshot(database: Path, config_path: Path, now_ns: int) -> Snapshot:
    """Run in the worker process: read inputs, hold back unsettled cases, run the stages, render.

    `now_ns` is the cut-off for settling and the time the snapshot reports as its own.
    """
    config = load_serve_config(config_path)
    checklists, checklist_issues = load_run_checklists(config.checklists, config_path)
    store = Store.open_read_only(database)
    try:
        inputs = store.read_inputs()
    finally:
        store.close()
    trace_cases, case_issues = build_trace_cases(inputs.spans, config.mapping)
    settle_ns = config.serve.settle_seconds * _NS_PER_SECOND
    settled = [case for case in trace_cases if now_ns - case.end_ns >= settle_ns]
    held_back_ids = {case.case_id for case in trace_cases} - {case.case_id for case in settled}
    verdict_rows = [row for row in inputs.verdict_rows if row.case_id not in held_back_ids]
    # Stored issues first, in the order run_check would meet them: input, cases, checklists.
    issues: list[Issue] = [stored.issue for stored in inputs.issues]
    issues.extend(case_issues)
    issues.extend(checklist_issues)
    folder = config_path.absolute().parent
    source = {
        "traces": TRACES_SOURCE,
        "verdicts": VERDICTS_SOURCE,
        "checklists": None if config.checklists is None else to_source(config.checklists, folder),
        "config": config_path.name,
    }
    run = run_stages(
        settled,
        verdict_rows,
        checklists,
        config,
        issues=issues,
        source=source,
        max_detail_cases=config.dashboard.max_detail_cases,
        config_name=config_path.name,
        # One row per repeated issue in the store; counting it as one would hide a flood.
        issue_counts=[stored.count for stored in inputs.issues],
    )
    served = ServedPage(inputs.generation, to_iso_time(now_ns))
    if run.case_count == 0:
        counts = WaitingCounts(
            span_count=len(inputs.spans),
            case_count=len(settled),
            held_back_count=len(held_back_ids),
            verdict_count=len(verdict_rows),
        )
        waiting = {
            "status": "waiting",
            "generation": inputs.generation,
            "span_count": counts.span_count,
            "case_count": counts.case_count,
            "held_back_count": counts.held_back_count,
            "verdict_count": counts.verdict_count,
            "data_notes": [line.message for line in run.summary],
        }
        return Snapshot(
            generation=inputs.generation,
            finished_at_ns=now_ns,
            html=render_waiting_page(counts, run.summary, served),
            results_json=_to_json(waiting),
        )
    results = {
        **run.results,
        "served": {
            "generation": inputs.generation,
            "settle_seconds": config.serve.settle_seconds,
            "held_back_cases": len(held_back_ids),
        },
    }
    return Snapshot(
        generation=inputs.generation,
        finished_at_ns=now_ns,
        html=render_dashboard(results, served=served),
        results_json=_to_json(results),
    )


def to_iso_time(time_ns: int) -> str:
    """`time_ns` as ISO 8601 in UTC to the microsecond, the form the page and status share."""
    seconds, nanoseconds = divmod(time_ns, _NS_PER_SECOND)
    moment = datetime.fromtimestamp(seconds, UTC).replace(microsecond=nanoseconds // 1_000)
    return moment.isoformat(timespec="microseconds").replace("+00:00", "Z")


@dataclass(frozen=True, slots=True)
class RecomputeStatus:
    is_running: bool
    last_error: str | None  # exception type and message; the traceback goes to the log
    last_error_at_ns: int | None


class RecomputeCoordinator:
    """Decides when the worker runs: after a quiet spell, one run at a time.

    A write starts a `debounce_seconds` wait that each later write restarts, but a run starts
    no later than `max_wait_seconds` after the first write no run has covered yet, so steady
    ingest still refreshes the page. Writes during a run cause exactly one more run after it. A failed run keeps the last snapshot and is not
    retried until the next write, and then no sooner than 5 s after the failure, doubling with
    each failure in a row up to 300 s. With `settle_seconds`, one more run follows that long
    after the last write, so cases held back as unsettled appear without waiting for new input.
    """

    def __init__(
        self,
        submit: Callable[[], Future[Snapshot]],
        store: Store,
        clock: Callable[[], float],
        debounce_seconds: float = 5.0,
        *,
        max_wait_seconds: float = 60.0,
        settle_seconds: float = 0.0,
        now_ns: Callable[[], int] = time.time_ns,
    ) -> None:
        self._submit = submit
        self._store = store
        self._clock = clock
        self._debounce_seconds = debounce_seconds
        self._max_wait_seconds = max_wait_seconds
        self._settle_seconds = settle_seconds
        self._now_ns = now_ns
        self._lock = threading.Lock()
        self._future: Future[Snapshot] | None = None
        # None when every write so far is covered by a run that has started.
        self._first_uncovered_write_at: float | None = None
        self._last_write_at = 0.0
        self._settle_due_at: float | None = None
        self._failure_count = 0
        self._retry_at = 0.0
        self._last_error: str | None = None
        self._last_error_at_ns: int | None = None
        snapshot = store.read_snapshot()
        generation = store.generation()
        if snapshot is None or generation > snapshot.generation:
            # Nothing to wait out: the input was complete before the server started.
            self._last_write_at = clock() - debounce_seconds
            self._first_uncovered_write_at = self._last_write_at
        if generation > 0 and settle_seconds > 0:
            # The last snapshot may hold back cases that have settled while the server was down.
            self._settle_due_at = clock() + settle_seconds

    def notify_write(self) -> None:
        with self._lock:
            now = self._clock()
            if self._first_uncovered_write_at is None:
                self._first_uncovered_write_at = now
            self._last_write_at = now
            if self._settle_seconds > 0:
                self._settle_due_at = now + self._settle_seconds

    def tick(self) -> None:
        with self._lock:
            future = self._future
        if future is not None:
            if not future.done():
                return
            # Saved outside the lock, so request threads never wait on the snapshot write.
            error = self._save(future)
            with self._lock:
                self._future = None
                self._record(error)
        with self._lock:
            if self._is_due(self._clock()):
                self._start()

    @property
    def status(self) -> RecomputeStatus:
        with self._lock:
            return RecomputeStatus(
                self._future is not None, self._last_error, self._last_error_at_ns
            )

    def _save(self, future: Future[Snapshot]) -> BaseException | None:
        try:
            snapshot = future.result()
            stored = self._store.read_snapshot()
            # Equal is newer too: the same input, recomputed after more cases settled.
            if stored is None or snapshot.generation >= stored.generation:
                self._store.write_snapshot(snapshot)
        except Exception as error:
            return error
        return None

    def _record(self, error: BaseException | None) -> None:
        if error is None:
            self._failure_count = 0
            self._last_error = None
            self._last_error_at_ns = None
            return
        logger.error("Recomputing the dashboard failed; keeping the last one", exc_info=error)
        self._failure_count += 1
        backoff = min(FIRST_BACKOFF_SECONDS * 2 ** (self._failure_count - 1), MAX_BACKOFF_SECONDS)
        self._retry_at = self._clock() + backoff
        self._last_error = f"{type(error).__name__}: {error}"
        self._last_error_at_ns = self._now_ns()
        # Only a new write earns a retry: the same input would most likely fail the same way.
        self._settle_due_at = None

    def _is_due(self, now: float) -> bool:
        if self._future is not None or now < self._retry_at:
            return False
        first_write_at = self._first_uncovered_write_at
        if first_write_at is not None and (
            now - self._last_write_at >= self._debounce_seconds
            or now - first_write_at >= self._max_wait_seconds
        ):
            return True
        return self._settle_due_at is not None and now >= self._settle_due_at

    def _start(self) -> None:
        self._first_uncovered_write_at = None
        if self._settle_due_at is not None and self._clock() >= self._settle_due_at:
            self._settle_due_at = None
        try:
            self._future = self._submit()
        except Exception as error:
            self._record(error)


def _to_json(value: object) -> str:
    return json.dumps(value, ensure_ascii=False, allow_nan=False, separators=(",", ":"))
