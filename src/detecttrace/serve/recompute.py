"""Background recompute for `detecttrace serve`: the CLI's pipeline over the stored input.

`compute_snapshot` runs in a worker process, so the CPU-bound pipeline never holds the
server's GIL. The configuration and checklists are read once, at startup, by
`load_recompute_settings`, and sent to the worker with each run, so the recompute and the
verdict API always apply the same label maps; an edit takes effect on restart. `RecomputeCoordinator` lives in the server: request threads tell it about
writes, a timer thread calls `tick` every second, and it decides when the worker runs. No
request ever waits for a recompute; a page reads the last finished snapshot.

A case is held back while its root span ended less than `serve.settle_seconds` ago, because
its tool spans and verdict may still be on their way; its verdict is held back with it, so it
doesn't read as a verdict without a trace. The results of a served run carry a `served` entry
with the generation, the settle window and how many cases were held back. Settling compares the
server's clock with the end time the agent reported, so the worker also says when the first
held-back case will settle, and the coordinator runs again then, with or without new input.

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
from detecttrace.checklist import Checklist
from detecttrace.config import Config
from detecttrace.dashboard import render_dashboard, render_waiting_page
from detecttrace.model import Issue, IssueKind
from detecttrace.pipeline import load_run_checklists, run_stages, to_source
from detecttrace.serve.config import ServeConfig
from detecttrace.serve.store import Snapshot, Store
from detecttrace.served_page import ServedPage, WaitingCounts

logger = logging.getLogger(__name__)

TRACES_SOURCE = "OTLP/HTTP"
VERDICTS_SOURCE = "verdict API"
FIRST_BACKOFF_SECONDS = 5.0
MAX_BACKOFF_SECONDS = 300.0
_NS_PER_SECOND = 1_000_000_000


@dataclass(frozen=True, slots=True)
class RecomputeOutcome:
    snapshot: Snapshot
    held_back_cases: int
    next_settle_at_ns: int | None  # wall-clock time the first held-back case settles


@dataclass(frozen=True, slots=True)
class RecomputeSettings:
    """What the server read at startup that every run uses; it is pickled to the worker."""

    config: Config  # the mapping and the label maps every run applies
    settle_seconds: int
    max_detail_cases: int
    checklists: dict[str, Checklist]
    checklist_issues: list[Issue]
    config_name: str  # the configuration file's name, as the page's source names it
    checklist_source: str | None  # the checklist folder relative to the configuration
    traces_source: str  # where the page's source says the traces came from
    verdicts_source: str


def load_recompute_settings(config: ServeConfig, config_path: Path) -> RecomputeSettings:
    """Load the checklists `config` names; raises ChecklistFileError when they can't be used."""
    checklists, checklist_issues = load_run_checklists(config.checklists, config_path)
    folder = config_path.absolute().parent
    return RecomputeSettings(
        config=Config(
            mapping=config.mapping,
            label_map=config.label_map,
            agent_label_map=config.agent_label_map,
        ),
        settle_seconds=config.serve.settle_seconds,
        max_detail_cases=config.dashboard.max_detail_cases,
        checklists=checklists,
        checklist_issues=checklist_issues,
        config_name=config_path.name,
        checklist_source=None
        if config.checklists is None
        else to_source(config.checklists, folder),
        traces_source=TRACES_SOURCE,
        verdicts_source=VERDICTS_SOURCE,
    )


def compute_snapshot(database: Path, settings: RecomputeSettings, now_ns: int) -> RecomputeOutcome:
    """Run in the worker process: read inputs, hold back unsettled cases, run the stages, render.

    `now_ns` is the cut-off for settling and the time the snapshot reports as its own.
    """
    store = Store.open_read_only(database)
    try:
        inputs = store.read_inputs()
    finally:
        store.close()
    trace_cases, case_issues = build_trace_cases(inputs.spans, settings.config.mapping)
    settle_ns = settings.settle_seconds * _NS_PER_SECOND
    settled = [case for case in trace_cases if now_ns - case.end_ns >= settle_ns]
    held_back = [case for case in trace_cases if now_ns - case.end_ns < settle_ns]
    held_back_ids = {case.case_id for case in held_back}
    next_settle_at_ns = min((case.end_ns for case in held_back), default=None)
    if next_settle_at_ns is not None:
        next_settle_at_ns += settle_ns
    verdict_rows = [row for row in inputs.verdict_rows if row.case_id not in held_back_ids]
    # Stored issues first, in the order run_check would meet them: input, cases, checklists.
    issues: list[Issue] = [stored.issue for stored in inputs.issues]
    issues.extend(case_issues)
    issues.extend(settings.checklist_issues)
    # Such a case waits more than a whole settle window, which only a clock ahead of ours explains.
    issues.extend(
        Issue(IssueKind.FUTURE_CASE_END, case.case_id)
        for case in held_back
        if case.end_ns - now_ns > settle_ns
    )
    source = {
        "traces": settings.traces_source,
        "verdicts": settings.verdicts_source,
        "checklists": settings.checklist_source,
        "config": settings.config_name,
    }
    run = run_stages(
        settled,
        verdict_rows,
        settings.checklists,
        settings.config,
        issues=issues,
        source=source,
        max_detail_cases=settings.max_detail_cases,
        config_name=settings.config_name,
        # One row per repeated issue in the store; counting it as one would hide a flood.
        issue_counts=[stored.count for stored in inputs.issues],
    )
    served = ServedPage(inputs.generation, to_iso_time(now_ns), len(held_back_ids))
    if run.case_count == 0:
        counts = WaitingCounts(
            span_count=len(inputs.spans),
            case_count=len(settled),
            held_back_count=len(held_back_ids),
            # Everything stored, held back or not: this line answers "did my verdicts arrive?".
            verdict_count=len(inputs.verdict_rows),
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
        snapshot = Snapshot(
            generation=inputs.generation,
            finished_at_ns=now_ns,
            html=render_waiting_page(counts, run.summary, served),
            results_json=_to_json(waiting),
        )
        return RecomputeOutcome(snapshot, len(held_back_ids), next_settle_at_ns)
    results = {
        **run.results,
        "served": {
            "generation": inputs.generation,
            "settle_seconds": settings.settle_seconds,
            "held_back_cases": len(held_back_ids),
        },
    }
    snapshot = Snapshot(
        generation=inputs.generation,
        finished_at_ns=now_ns,
        html=render_dashboard(results, served=served),
        results_json=_to_json(results),
    )
    return RecomputeOutcome(snapshot, len(held_back_ids), next_settle_at_ns)


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
    held_back_cases: int  # in the last snapshot; cases still inside the settle window


class RecomputeCoordinator:
    """Decides when the worker runs: after a quiet spell, one run at a time.

    A write starts a `debounce_seconds` wait that each later write restarts, but a run starts
    no later than `max_wait_seconds` after the first write no run has covered yet, so steady
    ingest still refreshes the page. Writes during a run cause exactly one more run after it.
    A write that changed nothing stored, such as a retried batch or an identical repost, causes
    no run: the coordinator compares the store's generation with the one the last run read.
    When a run holds cases back, one more run follows when the first of them settles. A failed
    run keeps the last snapshot and is not retried until the next write that changes the input,
    and then no sooner than 5 s after the failure, doubling with each failure in a row up to
    300 s.
    """

    def __init__(
        self,
        submit: Callable[[], Future[RecomputeOutcome]],
        store: Store,
        clock: Callable[[], float],
        debounce_seconds: float = 5.0,
        *,
        max_wait_seconds: float = 60.0,
        now_ns: Callable[[], int] = time.time_ns,
    ) -> None:
        self._submit = submit
        self._store = store
        self._clock = clock
        self._debounce_seconds = debounce_seconds
        self._max_wait_seconds = max_wait_seconds
        self._now_ns = now_ns
        self._lock = threading.Lock()
        self._future: Future[RecomputeOutcome] | None = None
        # None when every write so far is covered by a run that has started.
        self._first_uncovered_write_at: float | None = None
        self._last_write_at = 0.0
        self._settle_due_at: float | None = None
        self._failure_count = 0
        self._retry_at = 0.0
        self._last_error: str | None = None
        self._last_error_at_ns: int | None = None
        # Counted so a tick can tell whether a write arrived while it read the generation.
        self._write_count = 0
        snapshot = store.read_snapshot()
        generation = store.generation()
        held_back_cases = None if snapshot is None else _read_held_back_cases(snapshot)
        # The page served until the next run says this many are settling; the status agrees.
        self._held_back_cases = held_back_cases or 0
        if snapshot is None or generation > snapshot.generation:
            # Nothing to wait out: the input was complete before the server started.
            self._last_write_at = clock() - debounce_seconds
            self._first_uncovered_write_at = self._last_write_at
            # The input generation the last started run read, or the stored snapshot covers.
            self._run_generation = -1
        else:
            self._run_generation = generation
            # Held-back cases may have settled while the server was down, and when is not
            # stored; an unreadable snapshot is replaced by a fresh one.
            if held_back_cases != 0:
                self._settle_due_at = clock()

    def notify_write(self) -> None:
        with self._lock:
            now = self._clock()
            if self._first_uncovered_write_at is None:
                self._first_uncovered_write_at = now
            self._last_write_at = now
            self._write_count += 1

    def tick(self) -> None:
        with self._lock:
            future = self._future
        if future is not None:
            if not future.done():
                return
            # Saved outside the lock, so request threads never wait on the snapshot write.
            result = self._save(future)
            with self._lock:
                self._future = None
                if isinstance(result, RecomputeOutcome):
                    self._record_success(result)
                else:
                    self._record_failure(result)
        with self._lock:
            # Nothing due, nothing read: an unreadable database is not reported every second.
            if not self._is_due(self._clock()):
                return
            write_count = self._write_count
        # Read outside the lock, so a request thread never waits on the database here.
        generation = self._store.generation()
        with self._lock:
            now = self._clock()
            if not self._is_due(now):
                return
            if generation > self._run_generation or self._is_settle_due(now):
                self._start(generation)
            elif self._write_count == write_count:
                # Every write so far left the input as the last run read it: a retried batch,
                # a repeated bad body or an identical repost. A write that arrived during the
                # read may have changed it, so it is checked again on the next tick.
                self._first_uncovered_write_at = None

    @property
    def status(self) -> RecomputeStatus:
        with self._lock:
            return RecomputeStatus(
                self._future is not None,
                self._last_error,
                self._last_error_at_ns,
                self._held_back_cases,
            )

    def _save(self, future: Future[RecomputeOutcome]) -> RecomputeOutcome | Exception:
        try:
            outcome = future.result()
            stored = self._store.read_snapshot()
            # Equal is newer too: the same input, recomputed after more cases settled.
            if stored is None or outcome.snapshot.generation >= stored.generation:
                self._store.write_snapshot(outcome.snapshot)
        except Exception as error:
            return error
        return outcome

    def _record_success(self, outcome: RecomputeOutcome) -> None:
        self._failure_count = 0
        self._last_error = None
        self._last_error_at_ns = None
        self._held_back_cases = outcome.held_back_cases
        self._settle_due_at = self._to_settle_due_at(outcome.next_settle_at_ns)

    def _record_failure(self, error: Exception) -> None:
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
        return self._is_settle_due(now)

    def _is_settle_due(self, now: float) -> bool:
        return self._settle_due_at is not None and now >= self._settle_due_at

    def _start(self, generation: int) -> None:
        self._first_uncovered_write_at = None
        self._run_generation = generation
        if self._is_settle_due(self._clock()):
            self._settle_due_at = None
        try:
            self._future = self._submit()
        except Exception as error:
            self._record_failure(error)

    def _to_settle_due_at(self, next_settle_at_ns: int | None) -> float | None:
        if next_settle_at_ns is None:
            return None
        wait_seconds = (next_settle_at_ns - self._now_ns()) / _NS_PER_SECOND
        # At least a second, so a case on the boundary can't make the coordinator spin.
        return self._clock() + max(1.0, wait_seconds)


def _read_held_back_cases(snapshot: Snapshot) -> int | None:
    """The held-back count a stored snapshot reports, or None when it can't be read."""
    try:
        results = json.loads(snapshot.results_json)
        if results.get("status") == "waiting":
            count = results["held_back_count"]
        else:
            count = results["served"]["held_back_cases"]
    except (ValueError, AttributeError, TypeError, KeyError):
        return None
    # bool is an int subclass, and a count of True means nothing.
    if isinstance(count, bool) or not isinstance(count, int) or count < 0:
        return None
    return count


def _to_json(value: object) -> str:
    return json.dumps(value, ensure_ascii=False, allow_nan=False, separators=(",", ":"))
