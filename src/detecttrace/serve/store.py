"""The SQLite store behind `detecttrace serve`: raw spans, analyst verdicts, ingest issues.

The service writes acknowledged input here; a worker reads it back as `Span` and `VerdictRow`
objects and runs the same pipeline as the CLI, so the round trip is exact: attribute maps keep
their key order and value types (bool apart from int, NaN, infinities, -0.0, 64-bit ints).
Every write is one transaction committed with synchronous=FULL before the caller acknowledges
it, and nothing is ever deleted automatically. `data_generation` goes up by one for each write
that changed the input, so the worker recomputes only when there is something new.
"""

import hashlib
import json
import os
import sqlite3
import threading
import time
from collections.abc import Callable, Iterator, Sequence
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Literal

from detecttrace.model import Issue, IssueKind, Span, VerdictRow

SCHEMA_VERSION = 1
# Spans and parser issues from the OTLP endpoint have no file to name.
INGEST_SUBJECT = "OTLP/HTTP ingest"

# The trace formats that can share one store: OTLP JSON and JSON Lines hold the same spans.
TraceFamily = Literal["otlp", "langfuse"]


class StoreVersionError(Exception):
    """The database was written with a schema this version can't read."""


class StoreIntegrityError(Exception):
    """The database file is damaged or is not a database at all."""


@dataclass(frozen=True, slots=True)
class AddSpansResult:
    accepted: int
    duplicates: int  # identical to a stored span; nothing stored
    conflicts: int  # same key as a stored span but different; reported, not stored


@dataclass(frozen=True, slots=True)
class PutVerdictsResult:
    added: int  # rows for a case with no verdict before
    replaced: int  # rows that changed a case's verdict
    unchanged: int  # rows equal to the case's current verdict


@dataclass(frozen=True, slots=True)
class StoredIssue:
    issue: Issue
    count: int
    first_seen_ns: int
    last_seen_ns: int


@dataclass(frozen=True, slots=True)
class StoredInputs:
    generation: int
    spans: list[Span]  # ordered by (trace_id, span_id)
    # Ordered by case_id. API rows have no CSV line, so line_number is always 0.
    verdict_rows: list[VerdictRow]
    issues: list[StoredIssue]  # ordered by (kind, subject, detail)


@dataclass(frozen=True, slots=True)
class StoreCounts:
    generation: int
    span_count: int
    verdict_count: int
    last_ingest_ns: int | None  # None until a write changes the input


@dataclass(frozen=True, slots=True)
class Snapshot:
    generation: int
    finished_at_ns: int
    html: str
    results_json: str


class Store:
    """One connection to the database, safe to share between threads of one process."""

    def __init__(self, connection: sqlite3.Connection, now_ns: Callable[[], int]) -> None:
        self._connection = connection
        self._now_ns = now_ns
        self._lock = threading.Lock()

    @classmethod
    def open(cls, path: Path, *, now_ns: Callable[[], int] = time.time_ns) -> "Store":
        """Open the database at `path` for writing, creating it if missing.

        Raises StoreIntegrityError when the file is damaged, and StoreVersionError when a
        newer detecttrace wrote it. An older schema is migrated in place.
        """
        if not path.exists():
            _create_private_file(path)
        connection = sqlite3.connect(path, isolation_level=None, check_same_thread=False)
        try:
            _set_pragmas(connection)
            problems = _find_integrity_problems(connection)
            if problems:
                raise StoreIntegrityError(_describe_corruption(path, "; ".join(problems)))
            _prepare_schema(connection)
        except sqlite3.DatabaseError as error:
            connection.close()
            if not _is_corruption(error):
                raise
            raise StoreIntegrityError(_describe_corruption(path, str(error))) from None
        except (StoreIntegrityError, StoreVersionError):
            connection.close()
            raise
        return cls(connection, now_ns)

    @classmethod
    def open_read_only(cls, path: Path) -> "Store":
        """Open an existing database for reading; writes through it fail.

        Raises StoreVersionError unless the file has the current schema.
        """
        uri = f"{path.absolute().as_uri()}?mode=ro"
        connection = sqlite3.connect(uri, uri=True, isolation_level=None, check_same_thread=False)
        try:
            connection.execute("PRAGMA busy_timeout = 5000")
            version = _read_schema_version(connection)
            if version != SCHEMA_VERSION:
                raise StoreVersionError(_describe_version(path, version))
        except BaseException:
            connection.close()
            raise
        return cls(connection, time.time_ns)

    def add_spans(
        self,
        spans: Sequence[Span],
        issues: Sequence[Issue],
        *,
        subject: str = INGEST_SUBJECT,
        trace_family: TraceFamily | None = None,
    ) -> AddSpansResult:
        """Store new spans and count `issues`, all in one transaction.

        The first copy of a span key wins, as in the file loader: an identical copy is a
        duplicate, a different one a conflict, reported under `subject`. Only a new span or a
        new kind of issue advances the generation, so a client retrying an acknowledged batch
        does not cause a recompute; a repeated issue just has its count raised. A
        `trace_family` is recorded in the same transaction, as set_trace_family does, but
        only when a span is stored: a batch that stores none says nothing about the format.
        """
        accepted = duplicates = conflicts = 0
        with self._write() as now:
            resource_ids: dict[str, int] = {}
            found: list[Issue] = list(issues)
            for span in spans:
                stored = _find_span(self._connection, span.trace_id, span.span_id)
                if stored is None:
                    _insert_span(self._connection, span, resource_ids, now)
                    accepted += 1
                elif _is_same_span(span, stored):
                    duplicates += 1
                else:
                    conflicts += 1
                    found.append(
                        Issue(
                            IssueKind.CONFLICTING_DUPLICATE_SPAN,
                            subject,
                            f"{span.trace_id}/{span.span_id}",
                        )
                    )
            new_issue_count = _count_issues(self._connection, found, now)
            if accepted or new_issue_count:
                _advance_generation(self._connection, now)
            if trace_family is not None and accepted:
                _write_trace_family(self._connection, trace_family)
        return AddSpansResult(accepted, duplicates, conflicts)

    def put_verdicts(
        self, rows: Sequence[VerdictRow], token_name: str, *, issues: Sequence[Issue] = ()
    ) -> PutVerdictsResult:
        """Store each row as its case's current verdict, keeping any replaced one in history.

        The raw label is stored; mapping it happens at recompute. A row equal to the case's
        current verdict changes nothing, so a client retrying a request causes no recompute.
        Rows are applied in order, so a later row for the same case replaces an earlier one.
        `issues` are counted in the same transaction, as add_issues does.
        """
        added = replaced = unchanged = 0
        with self._write() as now:
            for row in rows:
                current = self._connection.execute(
                    "SELECT alert_class, label FROM verdicts WHERE case_id = ?", (row.case_id,)
                ).fetchone()
                if current == (row.alert_class, row.label):
                    unchanged += 1
                    continue
                if current is None:
                    added += 1
                else:
                    replaced += 1
                self._connection.execute(
                    "INSERT INTO verdict_history"
                    " (case_id, alert_class, label, received_at_ns, replaced_at_ns, token_name)"
                    " SELECT case_id, alert_class, label, received_at_ns, ?, token_name"
                    " FROM verdicts WHERE case_id = ?",
                    (now, row.case_id),
                )
                self._connection.execute(
                    "INSERT OR REPLACE INTO verdicts"
                    " (case_id, alert_class, label, received_at_ns, token_name)"
                    " VALUES (?, ?, ?, ?, ?)",
                    (row.case_id, row.alert_class, row.label, now, token_name),
                )
            new_issue_count = _count_issues(self._connection, issues, now)
            if added or replaced or new_issue_count:
                _advance_generation(self._connection, now)
        return PutVerdictsResult(added, replaced, unchanged)

    def add_issues(self, issues: Sequence[Issue]) -> None:
        """Count each issue, so a client repeating the same bad input can't fill the disk.

        Only an issue not seen before advances the generation.
        """
        with self._write() as now:
            if _count_issues(self._connection, issues, now):
                _advance_generation(self._connection, now)

    def clear(self) -> None:
        """Delete all input, issues, the snapshot and the trace family, in one transaction.

        The generation advances, so the worker recomputes from the empty input.
        """
        with self._write() as now:
            # Spans first: each one references a resource row.
            for table in (
                "spans",
                "resources",
                "verdicts",
                "verdict_history",
                "ingest_issues",
                "snapshot",
            ):
                self._connection.execute(f"DELETE FROM {table}")
            self._connection.execute("DELETE FROM meta WHERE key = 'trace_family'")
            _advance_generation(self._connection, now)

    def mark_changed(self) -> None:
        """Advance the generation alone: a configuration change needs a recompute."""
        with self._write() as now:
            _advance_generation(self._connection, now)

    def set_trace_family(self, family: TraceFamily) -> None:
        """Record the format of the stored spans. It changes no input, so the generation stays."""
        with self._write():
            _write_trace_family(self._connection, family)

    def read_inputs(self) -> StoredInputs:
        """Read everything the pipeline needs, from one consistent snapshot of the database."""
        with self._read() as connection:
            generation = _read_generation(connection)
            resources = {
                resource_id: _decode(attributes)
                for resource_id, attributes in connection.execute(
                    "SELECT id, attributes FROM resources"
                )
            }
            spans = [
                _to_span(row, resources[row[8]])
                for row in connection.execute(
                    "SELECT trace_id, span_id, parent_span_id, name, start_ns, end_ns,"
                    " is_error, attributes, resource_id"
                    " FROM spans ORDER BY trace_id, span_id"
                )
            ]
            verdict_rows = [
                VerdictRow(case_id, alert_class, label, line_number=0)
                for case_id, alert_class, label in connection.execute(
                    "SELECT case_id, alert_class, label FROM verdicts ORDER BY case_id"
                )
            ]
            issues = [
                StoredIssue(Issue(IssueKind(kind), subject, detail), count, first, last)
                for kind, subject, detail, count, first, last in connection.execute(
                    "SELECT kind, subject, detail, count, first_seen_ns, last_seen_ns"
                    " FROM ingest_issues ORDER BY kind, subject, detail"
                )
            ]
        return StoredInputs(generation, spans, verdict_rows, issues)

    def read_counts(self) -> StoreCounts:
        with self._read() as connection:
            generation = _read_generation(connection)
            span_count = connection.execute("SELECT count(*) FROM spans").fetchone()[0]
            verdict_count = connection.execute("SELECT count(*) FROM verdicts").fetchone()[0]
            last_ingest_ns = _read_meta_int(connection, "last_ingest_ns")
        return StoreCounts(generation, span_count, verdict_count, last_ingest_ns)

    def write_snapshot(self, snapshot: Snapshot) -> None:
        """Replace the stored snapshot. It is derived from the input, so the generation stays."""
        with self._write():
            self._connection.execute(
                "INSERT OR REPLACE INTO snapshot"
                " (id, generation, finished_at_ns, html, results_json) VALUES (1, ?, ?, ?, ?)",
                (
                    snapshot.generation,
                    snapshot.finished_at_ns,
                    snapshot.html,
                    snapshot.results_json,
                ),
            )

    def read_snapshot(self) -> Snapshot | None:
        with self._read() as connection:
            row = connection.execute(
                "SELECT generation, finished_at_ns, html, results_json FROM snapshot"
            ).fetchone()
        return None if row is None else Snapshot(*row)

    def read_trace_family(self) -> TraceFamily | None:
        """Return the format recorded by set_trace_family, or None if none is recorded."""
        with self._read() as connection:
            row = connection.execute("SELECT value FROM meta WHERE key = 'trace_family'").fetchone()
        return None if row is None else row[0]

    def generation(self) -> int:
        with self._read() as connection:
            return _read_generation(connection)

    def check_writable(self) -> None:
        """Raise sqlite3.OperationalError unless a write could be made now; nothing changes."""
        with self._lock:
            self._connection.execute("BEGIN IMMEDIATE")
            try:
                # BEGIN IMMEDIATE alone succeeds on a read-only file; a statement that writes
                # does not.
                self._connection.execute(
                    "UPDATE meta SET value = value WHERE key = 'schema_version'"
                )
            finally:
                _roll_back(self._connection)

    def close(self) -> None:
        with self._lock:
            self._connection.close()

    @contextmanager
    def _write(self) -> Iterator[int]:
        """Run the body as one write transaction; yield the time to stamp its rows with."""
        with self._lock:
            # IMMEDIATE takes the write lock up front, so a transaction never fails halfway
            # because another connection wrote between its reads and its writes.
            self._connection.execute("BEGIN IMMEDIATE")
            try:
                yield self._now_ns()
                self._connection.execute("COMMIT")
            except BaseException:
                _roll_back(self._connection)
                raise

    @contextmanager
    def _read(self) -> Iterator[sqlite3.Connection]:
        with self._lock:
            # One transaction, so a writer committing between two queries can't be seen
            # by one query and not the other.
            self._connection.execute("BEGIN")
            try:
                yield self._connection
            except BaseException:
                # Not COMMIT: if the error ended the transaction, a COMMIT would fail and
                # replace it.
                _roll_back(self._connection)
                raise
            self._connection.execute("COMMIT")


# Text-free integer form for SQLite, whose integers are signed 64-bit: OTLP times are unsigned,
# so a value past the signed limit wraps to a negative number, which no real time is.
_UINT64_RANGE = 2**64
_INT64_LIMIT = 2**63

_SCHEMA = (
    "CREATE TABLE meta (key TEXT PRIMARY KEY, value)",
    "CREATE TABLE resources ("
    " id INTEGER PRIMARY KEY,"
    " digest TEXT NOT NULL UNIQUE,"
    " attributes TEXT NOT NULL)",
    "CREATE TABLE spans ("
    " trace_id TEXT NOT NULL,"
    " span_id TEXT NOT NULL,"
    " parent_span_id TEXT,"
    " name TEXT NOT NULL,"
    " start_ns INTEGER NOT NULL,"
    " end_ns INTEGER NOT NULL,"
    " is_error INTEGER NOT NULL,"
    " attributes TEXT NOT NULL,"
    " resource_id INTEGER NOT NULL REFERENCES resources (id),"
    " received_at_ns INTEGER NOT NULL,"
    " PRIMARY KEY (trace_id, span_id)"
    ") WITHOUT ROWID",
    "CREATE TABLE verdicts ("
    " case_id TEXT PRIMARY KEY,"
    " alert_class TEXT NOT NULL,"
    " label TEXT NOT NULL,"
    " received_at_ns INTEGER NOT NULL,"
    " token_name TEXT NOT NULL)",
    "CREATE TABLE verdict_history ("
    " id INTEGER PRIMARY KEY,"
    " case_id TEXT NOT NULL,"
    " alert_class TEXT NOT NULL,"
    " label TEXT NOT NULL,"
    " received_at_ns INTEGER NOT NULL,"
    " replaced_at_ns INTEGER NOT NULL,"
    " token_name TEXT NOT NULL)",
    "CREATE TABLE ingest_issues ("
    " kind TEXT NOT NULL,"
    " subject TEXT NOT NULL,"
    " detail TEXT NOT NULL,"
    " count INTEGER NOT NULL,"
    " first_seen_ns INTEGER NOT NULL,"
    " last_seen_ns INTEGER NOT NULL,"
    " UNIQUE (kind, subject, detail))",
    "CREATE TABLE snapshot ("
    " id INTEGER PRIMARY KEY CHECK (id = 1),"
    " generation INTEGER NOT NULL,"
    " finished_at_ns INTEGER NOT NULL,"
    " html TEXT NOT NULL,"
    " results_json TEXT NOT NULL)",
    f"INSERT INTO meta (key, value) VALUES ('schema_version', {SCHEMA_VERSION})",
    "INSERT INTO meta (key, value) VALUES ('data_generation', 0)",
)

# Each entry upgrades a database from the keyed version to the next one, in one transaction.
_MIGRATIONS: dict[int, Callable[[sqlite3.Connection], None]] = {}


def _create_private_file(path: Path) -> None:
    # The file holds investigation data, so it is private from the first byte; SQLite would
    # create it with the umask's default mode.
    descriptor = os.open(path, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
    os.close(descriptor)


def _set_pragmas(connection: sqlite3.Connection) -> None:
    connection.execute("PRAGMA busy_timeout = 5000")
    connection.execute("PRAGMA journal_mode = WAL")
    # FULL syncs the WAL on every commit, so an acknowledged write survives a power loss.
    connection.execute("PRAGMA synchronous = FULL")
    connection.execute("PRAGMA foreign_keys = ON")


def _find_integrity_problems(connection: sqlite3.Connection) -> list[str]:
    results = [str(row[0]) for row in connection.execute("PRAGMA quick_check")]
    return [] if results == ["ok"] else results[:5]


def _prepare_schema(connection: sqlite3.Connection) -> None:
    connection.execute("BEGIN IMMEDIATE")
    try:
        version = _read_schema_version(connection)
        if version is None:
            for statement in _SCHEMA:
                connection.execute(statement)
        elif version > SCHEMA_VERSION or not _has_upgrade_path(version):
            raise StoreVersionError(_describe_version(None, version))
        else:
            for from_version in range(version, SCHEMA_VERSION):
                _MIGRATIONS[from_version](connection)
            connection.execute(
                "UPDATE meta SET value = ? WHERE key = 'schema_version'", (SCHEMA_VERSION,)
            )
        connection.execute("COMMIT")
    except BaseException:
        _roll_back(connection)
        raise


def _roll_back(connection: sqlite3.Connection) -> None:
    # Some errors, such as a full disk, end the transaction inside SQLite already; a ROLLBACK
    # then would fail and hide the real error. A failed COMMIT can leave it open instead.
    if connection.in_transaction:
        connection.execute("ROLLBACK")


def _read_schema_version(connection: sqlite3.Connection) -> int | None:
    has_meta = connection.execute(
        "SELECT 1 FROM sqlite_master WHERE type = 'table' AND name = 'meta'"
    ).fetchone()
    return None if has_meta is None else _read_meta_int(connection, "schema_version")


def _has_upgrade_path(version: int) -> bool:
    return all(step in _MIGRATIONS for step in range(version, SCHEMA_VERSION))


def _describe_version(path: Path | None, version: int | None) -> str:
    where = "The database" if path is None else f"The database {path}"
    if version is None:
        return f"{where} holds no detecttrace data yet. Start detecttrace serve on it first."
    if version > SCHEMA_VERSION:
        cause = "a newer detecttrace wrote it. Upgrade detecttrace to use it."
    elif _has_upgrade_path(version):
        cause = "an older detecttrace wrote it. Start detecttrace serve once to upgrade it."
    else:
        cause = "this detecttrace has no upgrade from it. Restore a backup or start a new file."
    return (
        f"{where} has schema version {version}, but this detecttrace reads schema version "
        f"{SCHEMA_VERSION}; {cause}"
    )


def _is_corruption(error: sqlite3.DatabaseError) -> bool:
    # sqlite_errorcode is the extended code; its low byte is the primary one. A busy or
    # locked database is an OperationalError worth reporting as itself.
    code = getattr(error, "sqlite_errorcode", None)
    return code is not None and code & 0xFF in (sqlite3.SQLITE_CORRUPT, sqlite3.SQLITE_NOTADB)


def _describe_corruption(path: Path, reason: str) -> str:
    return (
        f"The database {path} is damaged or is not a detecttrace database ({reason}). "
        "Stop detecttrace serve, move the file aside, and restore it from a backup, such as "
        f'one taken with: sqlite3 {path} ".backup {path}.backup"'
    )


def _read_generation(connection: sqlite3.Connection) -> int:
    return connection.execute("SELECT value FROM meta WHERE key = 'data_generation'").fetchone()[0]


def _read_meta_int(connection: sqlite3.Connection, key: str) -> int | None:
    row = connection.execute("SELECT value FROM meta WHERE key = ?", (key,)).fetchone()
    return None if row is None else int(row[0])


def _find_span(connection: sqlite3.Connection, trace_id: str, span_id: str) -> Span | None:
    row = connection.execute(
        "SELECT spans.trace_id, spans.span_id, parent_span_id, name, start_ns, end_ns,"
        " is_error, spans.attributes, resources.attributes"
        " FROM spans JOIN resources ON resources.id = spans.resource_id"
        " WHERE trace_id = ? AND span_id = ?",
        (trace_id, span_id),
    ).fetchone()
    return None if row is None else _to_span(row, _decode(row[8]))


def _is_same_span(span: Span, stored: Span) -> bool:
    # As in the file loader: the repr() fallback covers NaN, which never equals itself.
    return span == stored or repr(span) == repr(stored)


def _insert_span(
    connection: sqlite3.Connection, span: Span, resource_ids: dict[str, int], now: int
) -> None:
    resource_text = _encode(span.resource_attributes)
    digest = hashlib.sha256(resource_text.encode("utf-8")).hexdigest()
    resource_id = resource_ids.get(digest)
    if resource_id is None:
        connection.execute(
            "INSERT INTO resources (digest, attributes) VALUES (?, ?)"
            " ON CONFLICT (digest) DO NOTHING",
            (digest, resource_text),
        )
        resource_id = connection.execute(
            "SELECT id FROM resources WHERE digest = ?", (digest,)
        ).fetchone()[0]
        resource_ids[digest] = resource_id
    connection.execute(
        "INSERT INTO spans (trace_id, span_id, parent_span_id, name, start_ns, end_ns,"
        " is_error, attributes, resource_id, received_at_ns)"
        " VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
        (
            span.trace_id,
            span.span_id,
            span.parent_span_id,
            span.name,
            _to_signed(span.start_ns),
            _to_signed(span.end_ns),
            int(span.is_error),
            _encode(span.attributes),
            resource_id,
            now,
        ),
    )


def _count_issues(connection: sqlite3.Connection, issues: Sequence[Issue], now: int) -> int:
    """Count each issue in its (kind, subject, detail) row; return how many rows are new."""
    new_count = 0
    for issue in issues:
        key = (str(issue.kind), issue.subject, issue.detail)
        updated = connection.execute(
            "UPDATE ingest_issues SET count = count + 1, last_seen_ns = ?"
            " WHERE kind = ? AND subject = ? AND detail = ?",
            (now, *key),
        )
        if updated.rowcount == 0:
            connection.execute(
                "INSERT INTO ingest_issues"
                " (kind, subject, detail, count, first_seen_ns, last_seen_ns)"
                " VALUES (?, ?, ?, 1, ?, ?)",
                (*key, now, now),
            )
            new_count += 1
    return new_count


def _write_trace_family(connection: sqlite3.Connection, family: TraceFamily) -> None:
    connection.execute(
        "INSERT OR REPLACE INTO meta (key, value) VALUES ('trace_family', ?)", (family,)
    )


def _advance_generation(connection: sqlite3.Connection, now: int) -> None:
    connection.execute("UPDATE meta SET value = value + 1 WHERE key = 'data_generation'")
    connection.execute(
        "INSERT OR REPLACE INTO meta (key, value) VALUES ('last_ingest_ns', ?)", (now,)
    )


# SQLite rows are untyped; the schema fixes what each column holds.
def _to_span(row: tuple[Any, ...], resource_attributes: dict[str, object]) -> Span:
    trace_id, span_id, parent_span_id, name, start_ns, end_ns, is_error, attributes = row[:8]
    return Span(
        trace_id=trace_id,
        span_id=span_id,
        parent_span_id=parent_span_id,
        name=name,
        start_ns=_to_unsigned(start_ns),
        end_ns=_to_unsigned(end_ns),
        is_error=bool(is_error),
        attributes=_decode(attributes),
        resource_attributes=resource_attributes,
    )


def _encode(attributes: dict[str, object]) -> str:
    # allow_nan writes NaN and the infinities as JSON's NaN/Infinity, which json.loads reads.
    return json.dumps(attributes, ensure_ascii=False, allow_nan=True, separators=(",", ":"))


def _decode(text: str) -> dict[str, object]:
    return json.loads(text)


def _to_signed(value: int) -> int:
    return value - _UINT64_RANGE if value >= _INT64_LIMIT else value


def _to_unsigned(value: int) -> int:
    return value + _UINT64_RANGE if value < 0 else value
