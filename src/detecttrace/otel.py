"""Write OpenTelemetry spans from an agent straight to OTLP JSON lines, without a Collector.

    from opentelemetry.sdk.trace import TracerProvider
    from opentelemetry.sdk.trace.export import BatchSpanProcessor
    from detecttrace.otel import FileSpanExporter

    provider = TracerProvider()
    provider.add_span_processor(BatchSpanProcessor(FileSpanExporter("traces/{date}-{pid}.jsonl")))

Each export appends one line holding one OTLP `ExportTraceServiceRequest` in JSON, the same
format the Collector file exporter writes, so `detecttrace check` reads the folder directly.
Needs the OpenTelemetry SDK: `pip install detecttrace[otel]`.
"""

import base64
import contextlib
import io
import json
import logging
import math
import os
import stat
import string
import threading
import time
import weakref
from collections.abc import Callable, Mapping, Sequence
from datetime import UTC, datetime
from typing import Any

try:
    from opentelemetry.sdk.resources import Resource
    from opentelemetry.sdk.trace import ReadableSpan
    from opentelemetry.sdk.trace.export import SpanExporter, SpanExportResult
    from opentelemetry.sdk.util.instrumentation import InstrumentationScope
    from opentelemetry.trace import Link, SpanKind, StatusCode
except ImportError as error:
    raise ImportError(
        "FileSpanExporter needs the OpenTelemetry SDK: pip install detecttrace[otel]"
    ) from error

__all__ = ["FileSpanExporter"]

_logger = logging.getLogger("detecttrace.otel")
_PLACEHOLDERS = frozenset({"pid", "date"})
_WARNING_INTERVAL_SECONDS = 60.0
# O_NOFOLLOW: a symlink planted at the path cannot redirect spans into another file.
# O_NONBLOCK: opening a FIFO planted there returns at once instead of waiting for a reader.
_O_NONBLOCK = getattr(os, "O_NONBLOCK", 0)
_OPEN_FLAGS = (
    os.O_RDWR
    | os.O_APPEND
    | os.O_CREAT
    | getattr(os, "O_NOFOLLOW", 0)
    | getattr(os, "O_BINARY", 0)
    | _O_NONBLOCK
)
# The Python enum counts from 0; OTLP keeps 0 for "unspecified" and counts from 1.
_OTLP_KINDS = {
    SpanKind.INTERNAL: 1,
    SpanKind.SERVER: 2,
    SpanKind.CLIENT: 3,
    SpanKind.PRODUCER: 4,
    SpanKind.CONSUMER: 5,
}
_OTLP_STATUS_CODES = {StatusCode.UNSET: 0, StatusCode.OK: 1, StatusCode.ERROR: 2}
# OTLP JSON spells the non-finite doubles as strings, as protobuf's JSON mapping does.
_NON_FINITE = {math.inf: "Infinity", -math.inf: "-Infinity"}

_Json = dict[str, Any]


class FileSpanExporter(SpanExporter):
    """A SpanExporter that appends each batch of spans to a file as one OTLP JSON line.

    `path` may contain `{pid}` (the process ID, so each worker process writes its own
    file) and `{date}` (the UTC day, as 2026-09-30, for one file per day); write `{{` and
    `}}` for literal braces. The path is worked out again at every export, so a forked
    child or a new day moves to a new file. The folder must already exist.

    A new file is created readable by its owner only; a symlink or anything else that is
    not a regular file at the path is refused.

    Exports never raise: a file that cannot be written, or an export after shutdown,
    returns FAILURE and logs a warning on the "detecttrace.otel" logger at most once a
    minute, with the number of failed exports since the last warning. Failures left
    unreported are logged at the next successful export (at most once a minute) or at
    shutdown. `clock` gives seconds since the epoch, for tests.
    """

    def __init__(self, path: str | os.PathLike[str], *, clock: Callable[[], float] = time.time):
        self._template = os.fspath(path)
        _check_template(self._template)
        self._clock = clock
        self._lock = threading.Lock()
        self._file: io.FileIO | None = None
        self._file_path: str | None = None
        self._is_shut_down = False
        self._last_warning: float | None = None
        self._last_recovery: float | None = None
        self._suppressed = 0
        if hasattr(os, "register_at_fork"):
            # A weak reference, so the hook never keeps a discarded exporter alive.
            this: weakref.ref[FileSpanExporter] = weakref.ref(self)
            os.register_at_fork(after_in_child=lambda: _reset_in_child(this))

    def export(self, spans: Sequence[ReadableSpan]) -> SpanExportResult:
        """Append `spans` as one line; return FAILURE, never raise, when that fails."""
        if not spans:
            return SpanExportResult.SUCCESS
        try:
            line = _to_line(spans)
        except (TypeError, ValueError, RecursionError) as error:
            with self._lock:
                warning = self._count_failure(
                    f"could not encode spans for {self._template}: {error!r}"
                )
            _log(warning)
            return SpanExportResult.FAILURE
        with self._lock:
            result, message = self._export_line(line)
        _log(message)
        return result

    def shutdown(self) -> None:
        """Close the file; later exports return FAILURE. Safe to call more than once."""
        with self._lock:
            self._is_shut_down = True
            self._close()
            message = self._take_suppressed(f"for {self._template} shut down")
        _log(message)

    def force_flush(self, timeout_millis: int = 30000) -> bool:
        """Return True: each export is written unbuffered, so nothing waits to be flushed."""
        return True

    def _export_line(self, line: bytes) -> tuple[SpanExportResult, str | None]:
        """Write `line` with the lock held; return the result and a message to log, if any."""
        if self._is_shut_down:
            return SpanExportResult.FAILURE, self._count_failure(
                f"for {self._template} is shut down; spans dropped"
            )
        path = self._template
        try:
            path = self._resolve_path()
            self._write(path, line)
        # ValueError and friends: a NUL in the path, or a clock that fails or is out of range.
        except (OSError, ValueError, TypeError, OverflowError) as error:
            # The file may end in a partial line; reopening it starts a new line first.
            self._close()
            return SpanExportResult.FAILURE, self._count_failure(
                f"could not write spans to {path}: {error!r}"
            )
        return SpanExportResult.SUCCESS, self._count_success(path)

    def _resolve_path(self) -> str:
        day = datetime.fromtimestamp(self._clock(), tz=UTC).date().isoformat()
        return self._template.format(pid=os.getpid(), date=day)

    def _write(self, path: str, line: bytes) -> None:
        if self._file is None or self._file_path != path:
            self._close()
            # Unbuffered append: each line goes out in one write call, so on a local disk
            # processes sharing a file append whole lines, and a forked child inherits no
            # buffered data that it could write a second time.
            file = _open_regular_file(path)
            self._file = file
            self._file_path = path
            # An earlier write that failed part way left a partial line; end it, so this
            # line stays readable. Reading moves the position but appends still go to the end.
            if file.seek(0, os.SEEK_END):
                file.seek(-1, os.SEEK_END)
                if file.read(1) != b"\n":
                    _write_all(file, b"\n")
        _write_all(self._file, line)

    def _close(self) -> None:
        file, self._file, self._file_path = self._file, None, None
        if file is not None:
            # Every line went out unbuffered, so a failing close loses no spans.
            with contextlib.suppress(OSError):
                file.close()

    def _count_failure(self, message: str) -> str | None:
        """Return the warning to log for this failure, or None while warnings are paced."""
        now = self._now()
        if _is_recent(self._last_warning, now):
            self._suppressed += 1
            return None
        if self._suppressed:
            message += f"; {_count_exports(self._suppressed)} suppressed since the last warning"
        self._last_warning = now
        self._suppressed = 0
        return message

    def _count_success(self, path: str) -> str | None:
        """Return the message reporting failures suppressed before this success, if due."""
        if not self._suppressed:
            return None
        now = self._now()
        # Paced too, so exports that alternate between failing and succeeding stay quiet.
        if _is_recent(self._last_recovery, now):
            return None
        self._last_recovery = now
        return self._take_suppressed(f"recovered writing to {path}")

    def _take_suppressed(self, event: str) -> str | None:
        if not self._suppressed:
            return None
        message = f"{event}; {_count_exports(self._suppressed)} since the last warning"
        self._suppressed = 0
        return message

    def _now(self) -> float:
        try:
            return float(self._clock())
        except (TypeError, ValueError, OverflowError):
            # A broken clock is one cause of the failures, so pace them by the system clock.
            return time.time()

    def _reset_after_fork(self) -> None:
        # Another thread may have held the lock at the fork; the child gets a fresh one,
        # and drops the inherited file so it opens its own path at its next export.
        # Failures counted before the fork belong to the parent, which reports them itself.
        self._lock = threading.Lock()
        self._file = None
        self._file_path = None
        self._suppressed = 0


def _open_regular_file(path: str) -> io.FileIO:
    """Open `path` for appending, creating it owner-only; refuse anything but a regular file."""
    fd = os.open(path, _OPEN_FLAGS, 0o600)
    try:
        if not stat.S_ISREG(os.fstat(fd).st_mode):
            raise OSError(f"{path} is not a regular file")
        if _O_NONBLOCK:
            os.set_blocking(fd, True)
        return os.fdopen(fd, "a+b", buffering=0)
    except BaseException:
        os.close(fd)
        raise


def _write_all(file: io.FileIO, data: bytes) -> None:
    view = memoryview(data)
    while view:
        written = file.write(view)
        if not written:
            raise OSError(f"wrote nothing of {len(view)} bytes")
        view = view[written:]


def _is_recent(last: float | None, now: float) -> bool:
    # A clock that went backwards counts as not recent, so the message is logged rather
    # than held back until the clock catches up.
    return last is not None and 0 <= now - last < _WARNING_INTERVAL_SECONDS


def _count_exports(count: int) -> str:
    return f"{count} failed export{'' if count == 1 else 's'}"


def _log(warning: str | None) -> None:
    # Called with the lock released: a logging handler may export spans through this exporter.
    if warning is not None:
        _logger.warning("FileSpanExporter %s", warning)


def _reset_in_child(ref: "weakref.ref[FileSpanExporter]") -> None:
    exporter = ref()
    if exporter is not None:
        exporter._reset_after_fork()


def _check_template(template: str) -> None:
    try:
        fields = list(string.Formatter().parse(template))
    except ValueError as error:
        raise ValueError(f"FileSpanExporter path {template!r} is malformed: {error}") from None
    for _, name, format_spec, conversion in fields:
        if name is None:
            continue
        if name not in _PLACEHOLDERS or format_spec or conversion:
            raise ValueError(
                f"FileSpanExporter path {template!r} has an unknown placeholder "
                f"{{{name}}}; use {{pid}} or {{date}}, and {{{{ }}}} for literal braces"
            )


def _to_line(spans: Sequence[ReadableSpan]) -> bytes:
    groups: dict[Resource | None, dict[InstrumentationScope | None, list[_Json]]] = {}
    for span in spans:
        scopes = groups.setdefault(span.resource, {})
        scopes.setdefault(span.instrumentation_scope, []).append(_encode_span(span))
    request = {
        "resourceSpans": [
            _encode_resource_spans(resource, scopes) for resource, scopes in groups.items()
        ]
    }
    # ASCII output: a lone surrogate in an attribute becomes an escape instead of an
    # encoding error, and the reader turns it into U+FFFD.
    return (json.dumps(request, separators=(",", ":"), allow_nan=False) + "\n").encode("ascii")


def _encode_resource_spans(
    resource: Resource | None, scopes: Mapping[InstrumentationScope | None, list[_Json]]
) -> _Json:
    encoded: _Json = {}
    if resource is not None:
        encoded["resource"] = _with_attributes({}, resource.attributes, 0)
    encoded["scopeSpans"] = [_encode_scope_spans(scope, spans) for scope, spans in scopes.items()]
    if resource is not None and resource.schema_url:
        encoded["schemaUrl"] = resource.schema_url
    return encoded


def _encode_scope_spans(scope: InstrumentationScope | None, spans: list[_Json]) -> _Json:
    encoded: _Json = {}
    if scope is not None:
        encoded_scope: _Json = {"name": scope.name}
        if scope.version:
            encoded_scope["version"] = scope.version
        encoded["scope"] = _with_attributes(encoded_scope, scope.attributes or {}, 0)
    encoded["spans"] = spans
    if scope is not None and scope.schema_url:
        encoded["schemaUrl"] = scope.schema_url
    return encoded


def _encode_span(span: ReadableSpan) -> _Json:
    context = span.get_span_context()
    if context is None:
        raise ValueError(f"span {span.name!r} has no span context")
    encoded: _Json = {"traceId": f"{context.trace_id:032x}", "spanId": f"{context.span_id:016x}"}
    trace_state = context.trace_state.to_header()
    if trace_state:
        encoded["traceState"] = trace_state
    if span.parent is not None:
        encoded["parentSpanId"] = f"{span.parent.span_id:016x}"
    encoded["name"] = span.name
    encoded["kind"] = _OTLP_KINDS[span.kind]
    if span.start_time is not None:
        encoded["startTimeUnixNano"] = str(span.start_time)
    if span.end_time is not None:
        encoded["endTimeUnixNano"] = str(span.end_time)
    _with_attributes(encoded, span.attributes or {}, span.dropped_attributes)
    if span.events:
        encoded["events"] = [
            _with_attributes(
                {"timeUnixNano": str(event.timestamp), "name": event.name},
                event.attributes or {},
                event.dropped_attributes,
            )
            for event in span.events
        ]
    if span.dropped_events:
        encoded["droppedEventsCount"] = span.dropped_events
    if span.links:
        encoded["links"] = [_encode_link(link) for link in span.links]
    if span.dropped_links:
        encoded["droppedLinksCount"] = span.dropped_links
    status = _encode_status(span)
    if status:
        encoded["status"] = status
    return encoded


def _encode_link(link: Link) -> _Json:
    encoded: _Json = {
        "traceId": f"{link.context.trace_id:032x}",
        "spanId": f"{link.context.span_id:016x}",
    }
    trace_state = link.context.trace_state.to_header()
    if trace_state:
        encoded["traceState"] = trace_state
    return _with_attributes(encoded, link.attributes or {}, link.dropped_attributes)


def _encode_status(span: ReadableSpan) -> _Json:
    status: _Json = {}
    code = _OTLP_STATUS_CODES[span.status.status_code]
    if code:
        status["code"] = code
    if span.status.description:
        status["message"] = span.status.description
    return status


def _with_attributes(encoded: _Json, attributes: Mapping[str, object], dropped: int) -> _Json:
    if attributes:
        encoded["attributes"] = _encode_key_values(attributes)
    if dropped:
        encoded["droppedAttributesCount"] = dropped
    return encoded


def _encode_key_values(attributes: Mapping[str, object]) -> list[_Json]:
    return [{"key": key, "value": _encode_value(value)} for key, value in attributes.items()]


def _encode_value(value: object) -> _Json:
    # bool before int: True is an int in Python.
    if isinstance(value, str):
        return {"stringValue": value}
    if isinstance(value, bool):
        return {"boolValue": value}
    if isinstance(value, int):
        # OTLP JSON writes 64-bit integers as decimal strings.
        return {"intValue": str(value)}
    if isinstance(value, float):
        if math.isfinite(value):
            return {"doubleValue": value}
        return {"doubleValue": "NaN" if math.isnan(value) else _NON_FINITE[value]}
    if isinstance(value, bytes):
        return {"bytesValue": base64.b64encode(value).decode("ascii")}
    if value is None:
        return {}
    if isinstance(value, Mapping):
        values = _encode_key_values(value)
        return {"kvlistValue": {"values": values} if values else {}}
    if isinstance(value, Sequence):
        items = [_encode_value(item) for item in value]
        return {"arrayValue": {"values": items} if items else {}}
    raise TypeError(f"attribute value of type {type(value).__name__} is not an OTLP value")
