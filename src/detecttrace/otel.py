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
import string
import threading
import time
import weakref
from collections.abc import Callable, Mapping, Sequence
from datetime import UTC, datetime
from pathlib import Path
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

    Exports never raise: a file that cannot be written returns FAILURE and logs a warning
    on the "detecttrace.otel" logger at most once a minute, with the number of failed
    exports since the last warning. `clock` gives seconds since the epoch, for tests.
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
                self._warn(f"could not encode spans for {self._template}: {error!r}")
            return SpanExportResult.FAILURE
        with self._lock:
            if self._is_shut_down:
                return SpanExportResult.FAILURE
            path = self._resolve_path()
            try:
                self._write(path, line)
            except OSError as error:
                self._close()
                self._warn(f"could not write spans to {path}: {error}")
                return SpanExportResult.FAILURE
        return SpanExportResult.SUCCESS

    def shutdown(self) -> None:
        """Close the file; later exports return FAILURE. Safe to call more than once."""
        with self._lock:
            self._is_shut_down = True
            self._close()

    def force_flush(self, timeout_millis: int = 30000) -> bool:
        """Return True: each export is written unbuffered, so nothing waits to be flushed."""
        return True

    def _resolve_path(self) -> str:
        day = datetime.fromtimestamp(self._clock(), tz=UTC).date().isoformat()
        return self._template.format(pid=os.getpid(), date=day)

    def _write(self, path: str, line: bytes) -> None:
        if self._file is None or self._file_path != path:
            self._close()
            # Unbuffered append: each line goes out in one write call, so on a local disk
            # processes sharing a file append whole lines, and a forked child inherits no
            # buffered data that it could write a second time.
            self._file = Path(path).open("ab", buffering=0)  # noqa: SIM115 - kept open across exports
            self._file_path = path
        view = memoryview(line)
        while view:
            written = self._file.write(view)
            if not written:
                raise OSError(f"wrote nothing of {len(view)} bytes")
            view = view[written:]

    def _close(self) -> None:
        file, self._file, self._file_path = self._file, None, None
        if file is not None:
            # Every line went out unbuffered, so a failing close loses no spans.
            with contextlib.suppress(OSError):
                file.close()

    def _warn(self, message: str) -> None:
        now = self._clock()
        # A clock that went backwards also warns, rather than staying silent until it catches up.
        if self._last_warning is not None and 0 <= now - self._last_warning < (
            _WARNING_INTERVAL_SECONDS
        ):
            self._suppressed += 1
            return
        if self._suppressed:
            plural = "" if self._suppressed == 1 else "s"
            message += (
                f"; {self._suppressed} failed export{plural} suppressed since the last warning"
            )
        _logger.warning("FileSpanExporter %s", message)
        self._last_warning = now
        self._suppressed = 0

    def _reset_after_fork(self) -> None:
        # Another thread may have held the lock at the fork; the child gets a fresh one,
        # and drops the inherited file so it opens its own path at its next export.
        self._lock = threading.Lock()
        self._file = None
        self._file_path = None


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
