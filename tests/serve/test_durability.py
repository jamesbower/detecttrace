"""Acknowledged writes survive a restart and a crash, and a retried batch is stored once.

Each test runs `detecttrace serve` as a real process on a temporary database.
"""

import sqlite3
import sys
import threading
from dataclasses import dataclass
from pathlib import Path

import httpx
import pytest

from detecttrace.model import IssueKind
from detecttrace.serve.receiver import parse_traces_body
from detecttrace.serve.store import Store, StoredInputs
from serve.served_process import post_demo_data, split_demo_traces, start_server

pytestmark = pytest.mark.skipif(
    sys.platform == "win32", reason="stops the server with SIGTERM and SIGKILL, which need POSIX"
)

DATABASE_NAME = "detecttrace.db"
SPANS_PER_BATCH = 60
# Enough to be sure the server is busy taking batches when it is killed, with more to come.
KILL_AFTER_ACKNOWLEDGED = 20
RECOMPUTE_SECONDS = 60
COUNT_KEYS = ("generation", "updated_at", "span_count", "verdict_count", "last_ingest_at")


@dataclass(frozen=True)
class Restart:
    status_before: dict[str, object]
    status_after: dict[str, object]
    results_before: str
    results_after: str


@pytest.fixture(scope="module")
def restart(tmp_path_factory: pytest.TempPathFactory) -> Restart:
    """Post the demo data, wait for its snapshot, stop with SIGTERM, and start again."""
    folder = tmp_path_factory.mktemp("restart")
    first = start_server(folder)
    try:
        post_demo_data(first)
        generation = read_generation(folder / DATABASE_NAME)
        status_before = first.wait_for_generation(generation, RECOMPUTE_SECONDS)
        results_before = first.get("/api/results.json").text
    finally:
        first.stop()
    second = start_server(folder)
    try:
        # No wait: the snapshot of unchanged input is stored, so it is served from the start.
        status_after = second.get("/api/status").json()
        results_after = second.get("/api/results.json").text
    finally:
        second.stop()
    return Restart(status_before, status_after, results_before, results_after)


def test_a_restart_keeps_every_count(restart: Restart) -> None:
    assert to_counts(restart.status_after) == to_counts(restart.status_before)


def test_a_restart_keeps_the_results(restart: Restart) -> None:
    # Byte for byte: the input is unchanged, so the stored snapshot is served again as it is.
    assert restart.results_after == restart.results_before


def test_the_restart_compares_a_snapshot_of_the_demo_data(restart: Restart) -> None:
    # Guards the two checks above: equal empty stores would prove nothing.
    assert restart.status_before["span_count"] == 2129


@dataclass(frozen=True)
class Kill:
    batch_count: int
    acknowledged: list[int]  # indexes of the batches answered with 200
    missing_spans: set[tuple[str, str]]  # acknowledged but not stored
    quick_check: list[str]


@pytest.fixture(scope="module")
def kill(tmp_path_factory: pytest.TempPathFactory) -> Kill:
    """Post batches from a thread, SIGKILL the server mid-stream, restart it, and look."""
    folder = tmp_path_factory.mktemp("kill")
    batches = split_demo_traces(SPANS_PER_BATCH)
    served = start_server(folder)
    acknowledged: list[int] = []
    is_enough = threading.Event()

    def post_batches() -> None:
        for index, body in enumerate(batches):
            try:
                if served.post_traces(body).status_code == 200:
                    acknowledged.append(index)
            except httpx.TransportError:
                pass  # the server is gone; this batch was never acknowledged
            if len(acknowledged) >= KILL_AFTER_ACKNOWLEDGED:
                is_enough.set()
        is_enough.set()

    poster = threading.Thread(target=post_batches)
    poster.start()
    try:
        is_enough.wait(RECOMPUTE_SECONDS)
        served.process.kill()
        served.process.wait()
        poster.join()
    finally:
        served.close()
    # Starting again recovers the write-ahead log, as an operator's restart would.
    start_server(folder).stop()
    database = folder / DATABASE_NAME
    expected = {key for index in acknowledged for key in to_span_keys(batches[index])}
    stored = {(span.trace_id, span.span_id) for span in read_inputs(database).spans}
    with sqlite3.connect(database) as connection:
        quick_check = [str(row[0]) for row in connection.execute("PRAGMA quick_check")]
    connection.close()
    return Kill(len(batches), acknowledged, expected - stored, quick_check)


def test_a_killed_server_leaves_a_sound_database(kill: Kill) -> None:
    assert kill.quick_check == ["ok"]


def test_a_killed_server_keeps_every_acknowledged_span(kill: Kill) -> None:
    assert kill.missing_spans == set()


def test_the_kill_comes_before_the_last_batch(kill: Kill) -> None:
    # Guards the check above: a kill after every batch was acknowledged tests no crash.
    assert len(kill.acknowledged) < kill.batch_count


@pytest.fixture(scope="module")
def retried(tmp_path_factory: pytest.TempPathFactory) -> tuple[int, StoredInputs]:
    """Post one batch twice, as a client does when the first reply is lost; return what's stored.

    Also returns how many spans the batch holds.
    """
    folder = tmp_path_factory.mktemp("retry")
    batch = split_demo_traces(SPANS_PER_BATCH)[0]
    served = start_server(folder)
    try:
        served.post_traces(batch).raise_for_status()
        served.post_traces(batch).raise_for_status()
    finally:
        served.stop()
    return len(to_span_keys(batch)), read_inputs(folder / DATABASE_NAME)


def test_a_retried_batch_is_stored_once(retried: tuple[int, StoredInputs]) -> None:
    span_count, stored = retried
    assert len(stored.spans) == span_count


def test_a_retried_batch_is_no_conflict(retried: tuple[int, StoredInputs]) -> None:
    kinds = {stored.issue.kind for stored in retried[1].issues}
    assert IssueKind.CONFLICTING_DUPLICATE_SPAN not in kinds


def test_a_retried_batch_advances_the_generation_once(
    retried: tuple[int, StoredInputs],
) -> None:
    assert retried[1].generation == 1


def to_counts(status: dict[str, object]) -> dict[str, object]:
    return {key: status[key] for key in COUNT_KEYS}


def to_span_keys(body: bytes) -> list[tuple[str, str]]:
    """The (trace_id, span_id) keys the service stores for this body, read by its own parser."""
    spans, _, _ = parse_traces_body(body, "application/json", None)
    return [(span.trace_id, span.span_id) for span in spans]


def read_inputs(database: Path) -> StoredInputs:
    store = Store.open_read_only(database)
    try:
        return store.read_inputs()
    finally:
        store.close()


def read_generation(database: Path) -> int:
    store = Store.open_read_only(database)
    try:
        return store.generation()
    finally:
        store.close()
