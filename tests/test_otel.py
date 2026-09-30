"""FileSpanExporter writes OTLP JSON lines that the trace reader reads back unchanged."""

import contextlib
import io
import json
import logging
import os
import signal
import stat
import subprocess
import sys
import threading
import time
from collections.abc import Callable, Iterator, Mapping
from contextlib import AbstractContextManager
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import pytest
from opentelemetry.sdk.resources import Resource
from opentelemetry.sdk.trace import (
    Event,
    ReadableSpan,
    SpanLimits,
    SpanProcessor,
    TracerProvider,
)
from opentelemetry.sdk.trace.export import BatchSpanProcessor, SimpleSpanProcessor, SpanExportResult
from opentelemetry.sdk.util.instrumentation import InstrumentationScope
from opentelemetry.trace import Link, SpanContext, SpanKind, Status, StatusCode, TraceFlags

from detecttrace.model import Span
from detecttrace.otel import FileSpanExporter
from detecttrace.traces import load_spans

TRACE_ID = 0x0AF7651916CD43DD8448EB211C80319C
SPAN_ID = 0x00F067AA0BA902B7
PARENT_ID = 0x53995C3F42CD8AD8
LINKED_TRACE_ID = 0x4BF92F3577B34DA6A3CE929D0E0E4736
LINKED_SPAN_ID = 0x00000000000000AB
RESOURCE = Resource({"service.name": "triage-agent", "service.version": "1.2.0"})
SCOPE = InstrumentationScope("soc_agent.tracing", "0.4.0")
MIDNIGHT = datetime(2026, 10, 1, tzinfo=UTC).timestamp()
LOGGER_NAME = "detecttrace.otel"
SCHEMA_URL = "https://opentelemetry.io/schemas/1.26.0"
THREADS_PER_DAY = 4
FORK_TIMEOUT_SECONDS = 10
ROUND_TRIP_ATTRIBUTES: dict[str, Any] = {
    "text": "impossible travel",
    "count": -7,
    "large": 2**62,
    "ratio": 2.5,
    "flag": True,
    "texts": ["a", "b"],
    "counts": [1, 2],
    "flags": [True, False],
    "ratios": [1.5, 2.5],
}
EXPECTED_LINE = {
    "resourceSpans": [
        {
            "resource": {
                "attributes": [
                    {"key": "service.name", "value": {"stringValue": "triage-agent"}},
                    {"key": "service.version", "value": {"stringValue": "1.2.0"}},
                ]
            },
            "scopeSpans": [
                {
                    "scope": {"name": "soc_agent.tracing", "version": "0.4.0"},
                    "spans": [
                        {
                            "traceId": "0af7651916cd43dd8448eb211c80319c",
                            "spanId": "00f067aa0ba902b7",
                            "parentSpanId": "53995c3f42cd8ad8",
                            "name": "execute_tool get_user_profile",
                            "kind": 3,
                            "startTimeUnixNano": "1790000000000000000",
                            "endTimeUnixNano": "1790000000250000000",
                            "attributes": [
                                {"key": "gen_ai.tool.name", "value": {"stringValue": "lookup"}},
                                {"key": "retries", "value": {"intValue": "2"}},
                                {"key": "score", "value": {"doubleValue": 0.5}},
                                {"key": "cached", "value": {"boolValue": False}},
                            ],
                            "status": {"code": 2, "message": "upstream request timed out"},
                        }
                    ],
                }
            ],
        }
    ]
}


class FixedClock:
    """A settable clock in seconds since the epoch."""

    def __init__(self, now: float = MIDNIGHT - 3600) -> None:
        self.now = now

    def __call__(self) -> float:
        return self.now


def make_span(
    name: str = "execute_tool get_user_profile",
    *,
    parent: SpanContext | None = None,
    attributes: Mapping[str, Any] | None = None,
    kind: SpanKind = SpanKind.INTERNAL,
    status: Status | None = None,
    events: tuple[Event, ...] = (),
    links: tuple[Link, ...] = (),
    resource: Resource = RESOURCE,
    scope: InstrumentationScope = SCOPE,
) -> ReadableSpan:
    return ReadableSpan(
        name,
        context=SpanContext(TRACE_ID, SPAN_ID, is_remote=False, trace_flags=TraceFlags(1)),
        parent=parent,
        resource=resource,
        attributes=attributes or {},
        events=events,
        links=links,
        kind=kind,
        status=status or Status(StatusCode.UNSET),
        start_time=1_790_000_000_000_000_000,
        end_time=1_790_000_000_250_000_000,
        instrumentation_scope=scope,
    )


def parent_context() -> SpanContext:
    return SpanContext(TRACE_ID, PARENT_ID, is_remote=False, trace_flags=TraceFlags(1))


def export_one(tmp_path: Path, span: ReadableSpan) -> dict[str, Any]:
    """Export one span and return its encoded OTLP JSON object."""
    path = tmp_path / "spans.jsonl"
    exporter = FileSpanExporter(path)
    exporter.export([span])
    exporter.shutdown()
    return json.loads(path.read_text())["resourceSpans"][0]["scopeSpans"][0]["spans"][0]


def read_documents(path: Path) -> list[dict[str, Any]]:
    return [json.loads(line) for line in path.read_text().splitlines()]


def span_names(path: Path) -> list[str]:
    return [
        span["name"]
        for document in read_documents(path)
        for resource_spans in document["resourceSpans"]
        for scope_spans in resource_spans["scopeSpans"]
        for span in scope_spans["spans"]
    ]


def list_names(folder: Path) -> list[str]:
    return sorted(path.name for path in folder.iterdir())


# --- Round trip through the real SDK --------------------------------------------------------


def _create_simple(exporter: FileSpanExporter) -> SpanProcessor:
    return SimpleSpanProcessor(exporter)


def _create_batch(exporter: FileSpanExporter) -> SpanProcessor:
    return BatchSpanProcessor(exporter)


@pytest.fixture(params=[_create_simple, _create_batch], ids=["simple", "batch"])
def round_trip(
    request: pytest.FixtureRequest, tmp_path: Path
) -> Iterator[tuple[list[ReadableSpan], list[Span]]]:
    """Spans made through the SDK with each processor, and the same spans read back."""
    path = tmp_path / "traces" / "spans.jsonl"
    path.parent.mkdir()
    create_processor: Callable[[FileSpanExporter], SpanProcessor] = request.param
    exporter = FileSpanExporter(path)
    provider = TracerProvider(resource=RESOURCE)
    provider.add_span_processor(create_processor(exporter))
    kept: list[ReadableSpan] = []
    provider.add_span_processor(_Keep(kept))
    tracer = provider.get_tracer("soc_agent.tracing", "0.4.0")
    with tracer.start_as_current_span("invoke_agent triage-agent", attributes={"case": "C-1"}):
        with tracer.start_as_current_span("execute_tool lookup", attributes=ROUND_TRIP_ATTRIBUTES):
            pass
        with tracer.start_as_current_span("execute_tool query") as failed:
            failed.set_status(Status(StatusCode.ERROR, "timeout"))
    provider.force_flush()
    spans, _ = load_spans(path.parent)
    yield kept, spans
    provider.shutdown()


class _Keep(SpanProcessor):
    def __init__(self, kept: list[ReadableSpan]) -> None:
        self.kept = kept

    def on_end(self, span: ReadableSpan) -> None:
        self.kept.append(span)


def _summarize_sdk(span: ReadableSpan) -> tuple[object, ...]:
    context = span.get_span_context()
    assert context is not None
    parent = None if span.parent is None else f"{span.parent.span_id:016x}"
    return (
        f"{context.trace_id:032x}",
        f"{context.span_id:016x}",
        parent,
        span.name,
        span.start_time,
        span.end_time,
        span.status.status_code is StatusCode.ERROR,
    )


def _summarize_read(span: Span) -> tuple[object, ...]:
    return (
        span.trace_id,
        span.span_id,
        span.parent_span_id,
        span.name,
        span.start_ns,
        span.end_ns,
        span.is_error,
    )


def test_round_trip_keeps_ids_parents_names_times_and_errors(
    round_trip: tuple[list[ReadableSpan], list[Span]],
) -> None:
    kept, spans = round_trip

    assert sorted(map(_summarize_read, spans), key=str) == sorted(
        map(_summarize_sdk, kept), key=str
    )


def test_round_trip_keeps_attributes_of_every_type(
    round_trip: tuple[list[ReadableSpan], list[Span]],
) -> None:
    _, spans = round_trip
    by_name = {span.name: span for span in spans}

    assert by_name["execute_tool lookup"].attributes == ROUND_TRIP_ATTRIBUTES


def test_round_trip_keeps_resource_attributes(
    round_trip: tuple[list[ReadableSpan], list[Span]],
) -> None:
    _, spans = round_trip

    assert [span.resource_attributes for span in spans] == [dict(RESOURCE.attributes)] * 3


# --- Encoding -----------------------------------------------------------------------------------


def test_one_fixed_span_encodes_to_the_expected_line(tmp_path: Path) -> None:
    span = make_span(
        parent=parent_context(),
        kind=SpanKind.CLIENT,
        status=Status(StatusCode.ERROR, "upstream request timed out"),
        attributes={"gen_ai.tool.name": "lookup", "retries": 2, "score": 0.5, "cached": False},
    )
    path = tmp_path / "spans.jsonl"
    exporter = FileSpanExporter(path)
    exporter.export([span])

    assert path.read_text() == json.dumps(EXPECTED_LINE, separators=(",", ":")) + "\n"


@pytest.mark.parametrize(
    ("kind", "code"),
    [
        (SpanKind.INTERNAL, 1),
        (SpanKind.SERVER, 2),
        (SpanKind.CLIENT, 3),
        (SpanKind.PRODUCER, 4),
        (SpanKind.CONSUMER, 5),
    ],
)
def test_kind_is_the_otlp_number(tmp_path: Path, kind: SpanKind, code: int) -> None:
    assert export_one(tmp_path, make_span(kind=kind))["kind"] == code


@pytest.mark.parametrize(
    ("status", "expected"),
    [
        (Status(StatusCode.OK), {"code": 1}),
        (Status(StatusCode.ERROR), {"code": 2}),
        (Status(StatusCode.ERROR, "boom"), {"code": 2, "message": "boom"}),
    ],
)
def test_status_writes_code_and_message(
    tmp_path: Path, status: Status, expected: dict[str, object]
) -> None:
    assert export_one(tmp_path, make_span(status=status))["status"] == expected


def test_unset_status_is_omitted(tmp_path: Path) -> None:
    assert "status" not in export_one(tmp_path, make_span())


def test_span_without_parent_has_no_parent_span_id(tmp_path: Path) -> None:
    assert "parentSpanId" not in export_one(tmp_path, make_span())


def test_span_without_attributes_events_or_links_omits_them(tmp_path: Path) -> None:
    encoded = export_one(tmp_path, make_span())

    assert {"attributes", "events", "links"} & encoded.keys() == set()


@pytest.mark.parametrize(
    ("value", "expected"),
    [
        ("text", {"stringValue": "text"}),
        (True, {"boolValue": True}),
        (False, {"boolValue": False}),
        (0, {"intValue": "0"}),
        (-(2**63), {"intValue": "-9223372036854775808"}),
        (1.0, {"doubleValue": 1.0}),
        (float("nan"), {"doubleValue": "NaN"}),
        (float("inf"), {"doubleValue": "Infinity"}),
        (float("-inf"), {"doubleValue": "-Infinity"}),
        (b"\x00\xff", {"bytesValue": "AP8="}),
        (("a", "b"), {"arrayValue": {"values": [{"stringValue": "a"}, {"stringValue": "b"}]}}),
        ((True, 1), {"arrayValue": {"values": [{"boolValue": True}, {"intValue": "1"}]}}),
        (("a", None), {"arrayValue": {"values": [{"stringValue": "a"}, {}]}}),
        ((), {"arrayValue": {}}),
        ({"k": 1}, {"kvlistValue": {"values": [{"key": "k", "value": {"intValue": "1"}}]}}),
        ("café \ud800", {"stringValue": "café \ud800"}),
    ],
    ids=repr,
)
def test_attribute_value_encodes_as_otlp_any_value(
    tmp_path: Path, value: object, expected: dict[str, object]
) -> None:
    encoded = export_one(tmp_path, make_span(attributes={"k": value}))

    assert encoded["attributes"] == [{"key": "k", "value": expected}]


def test_line_is_ascii_so_a_lone_surrogate_never_breaks_the_write(tmp_path: Path) -> None:
    path = tmp_path / "spans.jsonl"
    FileSpanExporter(path).export([make_span(attributes={"k": "café \ud800"})])

    assert path.read_bytes().isascii()


def test_events_encode_with_time_name_and_attributes(tmp_path: Path) -> None:
    event = Event("retry", attributes={"attempt": 2}, timestamp=1_790_000_000_100_000_000)

    assert export_one(tmp_path, make_span(events=(event,)))["events"] == [
        {
            "timeUnixNano": "1790000000100000000",
            "name": "retry",
            "attributes": [{"key": "attempt", "value": {"intValue": "2"}}],
        }
    ]


def test_links_encode_with_ids_and_attributes(tmp_path: Path) -> None:
    linked = SpanContext(LINKED_TRACE_ID, LINKED_SPAN_ID, is_remote=True)
    link = Link(linked, attributes={"reason": "alert"})

    assert export_one(tmp_path, make_span(links=(link,)))["links"] == [
        {
            "traceId": "4bf92f3577b34da6a3ce929d0e0e4736",
            "spanId": "00000000000000ab",
            "attributes": [{"key": "reason", "value": {"stringValue": "alert"}}],
        }
    ]


def test_dropped_attributes_are_counted(tmp_path: Path) -> None:
    path = tmp_path / "spans.jsonl"
    provider = TracerProvider(span_limits=SpanLimits(max_span_attributes=1))
    provider.add_span_processor(SimpleSpanProcessor(FileSpanExporter(path)))
    provider.get_tracer("t").start_span("s", attributes={"a": 1, "b": 2, "c": 3}).end()
    span = read_documents(path)[0]["resourceSpans"][0]["scopeSpans"][0]["spans"][0]

    assert span["droppedAttributesCount"] == 2


def test_spans_are_grouped_by_resource_then_scope(tmp_path: Path) -> None:
    other_resource = Resource({"service.name": "enrichment-agent"})
    other_scope = InstrumentationScope("other.scope")
    path = tmp_path / "spans.jsonl"
    FileSpanExporter(path).export(
        [
            make_span("a"),
            make_span("b", resource=other_resource),
            make_span("c", scope=other_scope),
            make_span("d"),
        ]
    )
    document = read_documents(path)[0]

    assert [
        [[span["name"] for span in scope["spans"]] for scope in resource["scopeSpans"]]
        for resource in document["resourceSpans"]
    ] == [[["a", "d"], ["c"]], [["b"]]]


def export_document(tmp_path: Path, span: ReadableSpan) -> dict[str, Any]:
    """Export one span and return the whole OTLP JSON document."""
    path = tmp_path / "spans.jsonl"
    FileSpanExporter(path).export([span])
    return read_documents(path)[0]


def test_scope_without_a_version_encodes_its_name_only(tmp_path: Path) -> None:
    document = export_document(tmp_path, make_span(scope=InstrumentationScope("s")))

    assert document["resourceSpans"][0]["scopeSpans"][0]["scope"] == {"name": "s"}


def test_resource_schema_url_is_written_on_the_resource_spans(tmp_path: Path) -> None:
    resource = Resource({"service.name": "triage-agent"}, SCHEMA_URL)
    document = export_document(tmp_path, make_span(resource=resource))

    assert document["resourceSpans"][0]["schemaUrl"] == SCHEMA_URL


def test_scope_schema_url_is_written_on_the_scope_spans(tmp_path: Path) -> None:
    scope = InstrumentationScope("s", schema_url=SCHEMA_URL)
    document = export_document(tmp_path, make_span(scope=scope))

    assert document["resourceSpans"][0]["scopeSpans"][0]["schemaUrl"] == SCHEMA_URL


def test_one_export_writes_one_line(tmp_path: Path) -> None:
    path = tmp_path / "spans.jsonl"
    exporter = FileSpanExporter(path)
    exporter.export([make_span("a"), make_span("b")])
    exporter.export([make_span("c")])

    assert len(path.read_text().splitlines()) == 2


def test_empty_export_writes_no_file(tmp_path: Path) -> None:
    path = tmp_path / "spans.jsonl"
    FileSpanExporter(path).export([])

    assert not path.exists()


def test_export_appends_to_an_existing_file(tmp_path: Path) -> None:
    path = tmp_path / "spans.jsonl"
    FileSpanExporter(path).export([make_span("a")])
    FileSpanExporter(path).export([make_span("b")])

    assert span_names(path) == ["a", "b"]


# --- Threads, processes and dates ----------------------------------------------------------------


def export_from_both_sides_of_midnight(exporter: FileSpanExporter, exports_per_thread: int) -> None:
    """Export from threads whose clocks sit on either side of UTC midnight, all at once."""
    # Large spans keep each write long, widening the window for a torn or misdirected line.
    span = make_span(attributes={"payload": "x" * 5000})
    start = threading.Barrier(THREADS_PER_DAY * 2)

    def work() -> None:
        start.wait()
        for _ in range(exports_per_thread):
            exporter.export([span])

    threads = [
        threading.Thread(target=work, name=f"{side}-{index}")
        for side in ("before", "after")
        for index in range(THREADS_PER_DAY)
    ]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()


def clock_by_thread() -> float:
    """Just before midnight for "before-*" threads, just after it for the rest."""
    is_before = threading.current_thread().name.startswith("before")
    return MIDNIGHT - 1 if is_before else MIDNIGHT + 1


def count_spans(folder: Path) -> int:
    # span_names parses every line, so a torn or interleaved line fails here.
    return sum(len(span_names(path)) for path in sorted(folder.iterdir()))


@pytest.fixture
def frequent_thread_switches() -> Iterator[None]:
    """Switch threads every microsecond instead of every 5 ms, so races show up reliably."""
    interval = sys.getswitchinterval()
    sys.setswitchinterval(1e-6)
    yield
    sys.setswitchinterval(interval)


@pytest.mark.usefixtures("frequent_thread_switches")
def test_threads_switching_files_at_midnight_lose_no_spans(tmp_path: Path) -> None:
    # Every export switches files, so without the lock one thread closes or swaps the file
    # another is writing, and that export raises or lands in the wrong day.
    exporter = FileSpanExporter(tmp_path / "{date}.jsonl", clock=clock_by_thread)
    export_from_both_sides_of_midnight(exporter, exports_per_thread=200)

    assert count_spans(tmp_path) == THREADS_PER_DAY * 2 * 200


def test_pid_placeholder_gives_each_process_its_own_file(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    exporter = FileSpanExporter(tmp_path / "spans-{pid}.jsonl")
    monkeypatch.setattr(os, "getpid", lambda: 100)
    exporter.export([make_span()])
    monkeypatch.setattr(os, "getpid", lambda: 200)
    exporter.export([make_span()])

    assert list_names(tmp_path) == ["spans-100.jsonl", "spans-200.jsonl"]


def fork_and_export(exporter: FileSpanExporter, name: str) -> int:
    """Export one span from a forked child; return its exit code, failing if it hangs."""
    child = os.fork()
    if child == 0:  # pragma: no cover - runs in the child process
        code = 1
        try:
            if exporter.export([make_span(name)]) is SpanExportResult.SUCCESS:
                code = 0
        finally:
            os._exit(code)
    deadline = time.monotonic() + FORK_TIMEOUT_SECONDS
    while time.monotonic() < deadline:
        pid, status = os.waitpid(child, os.WNOHANG)
        if pid:
            return os.waitstatus_to_exitcode(status)
        time.sleep(0.01)
    os.kill(child, signal.SIGKILL)
    os.waitpid(child, 0)
    pytest.fail(f"forked child did not exit within {FORK_TIMEOUT_SECONDS} s")


@contextlib.contextmanager
def held_by_another_thread(lock: AbstractContextManager[Any]) -> Iterator[None]:
    """Hold `lock` in a helper thread for the duration of the block."""
    acquired = threading.Event()
    release = threading.Event()

    def hold() -> None:
        with lock:
            acquired.set()
            release.wait()

    thread = threading.Thread(target=hold)
    thread.start()
    acquired.wait()
    try:
        yield
    finally:
        release.set()
        thread.join()


needs_fork = pytest.mark.skipif(not hasattr(os, "fork"), reason="needs os.fork")


@needs_fork
def test_child_forked_while_another_thread_exports_can_export(tmp_path: Path) -> None:
    exporter = FileSpanExporter(tmp_path / "spans.jsonl")
    exporter.export([make_span("parent")])
    # The fork copies the lock as held, by a thread the child does not have.
    with held_by_another_thread(exporter._lock):
        exit_code = fork_and_export(exporter, "child")

    assert exit_code == 0


@needs_fork
def test_forked_child_opens_the_path_again_instead_of_the_inherited_file(tmp_path: Path) -> None:
    path = tmp_path / "spans.jsonl"
    exporter = FileSpanExporter(path)
    exporter.export([make_span("parent")])
    # A rotated file: the parent's open handle now points at the old name.
    path.rename(tmp_path / "rotated.jsonl")
    fork_and_export(exporter, "child")

    assert span_names(path) == ["child"]


def test_date_placeholder_switches_files_at_utc_midnight(tmp_path: Path) -> None:
    clock = FixedClock(MIDNIGHT - 1)
    exporter = FileSpanExporter(tmp_path / "{date}.jsonl", clock=clock)
    exporter.export([make_span()])
    clock.now = MIDNIGHT + 1
    exporter.export([make_span()])

    assert list_names(tmp_path) == ["2026-09-30.jsonl", "2026-10-01.jsonl"]


def test_doubled_braces_are_literal(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(os, "getpid", lambda: 7)
    FileSpanExporter(tmp_path / "{{x}}-{pid}.jsonl").export([make_span()])

    assert list_names(tmp_path) == ["{x}-7.jsonl"]


@pytest.mark.parametrize(
    "template",
    [
        "{host}.jsonl",
        "{}.jsonl",
        "{0}.jsonl",
        "{pid:>8}.jsonl",
        "{pid!r}.jsonl",
        "a{.jsonl",
        "a}.jsonl",
    ],
)
def test_bad_template_is_rejected_at_construction(tmp_path: Path, template: str) -> None:
    with pytest.raises(ValueError, match="path"):
        FileSpanExporter(tmp_path / template)


# --- Errors and lifecycle ------------------------------------------------------------------------


@pytest.fixture
def unwritable(tmp_path: Path) -> Path:
    return tmp_path / "missing" / "spans.jsonl"


def test_write_error_returns_failure(unwritable: Path) -> None:
    assert FileSpanExporter(unwritable).export([make_span()]) is SpanExportResult.FAILURE


@pytest.mark.skipif(
    sys.platform == "win32" or os.geteuid() == 0,
    reason="needs POSIX permissions that the current user cannot bypass",
)
def test_read_only_file_returns_failure(tmp_path: Path) -> None:
    path = tmp_path / "spans.jsonl"
    path.touch(mode=0o444)

    assert FileSpanExporter(path).export([make_span()]) is SpanExportResult.FAILURE


def test_write_error_warns_naming_the_path(
    unwritable: Path, caplog: pytest.LogCaptureFixture
) -> None:
    FileSpanExporter(unwritable).export([make_span()])

    assert [(r.name, r.levelno, str(unwritable) in r.getMessage()) for r in caplog.records] == [
        (LOGGER_NAME, logging.WARNING, True)
    ]


def test_second_failure_within_a_minute_is_not_logged(
    unwritable: Path, caplog: pytest.LogCaptureFixture
) -> None:
    clock = FixedClock()
    exporter = FileSpanExporter(unwritable, clock=clock)
    exporter.export([make_span()])
    clock.now += 59
    exporter.export([make_span()])

    assert len(caplog.records) == 1


def test_failure_after_a_minute_warns_with_the_suppressed_count(
    unwritable: Path, caplog: pytest.LogCaptureFixture
) -> None:
    clock = FixedClock()
    exporter = FileSpanExporter(unwritable, clock=clock)
    exporter.export([make_span()])
    clock.now += 10
    exporter.export([make_span()])
    exporter.export([make_span()])
    clock.now += 60
    exporter.export([make_span()])

    assert "2 failed exports suppressed" in caplog.records[-1].getMessage()


def test_suppressed_count_restarts_after_each_warning(
    unwritable: Path, caplog: pytest.LogCaptureFixture
) -> None:
    clock = FixedClock()
    exporter = FileSpanExporter(unwritable, clock=clock)
    exporter.export([make_span()])
    clock.now += 10
    exporter.export([make_span()])
    clock.now += 60
    exporter.export([make_span()])
    clock.now += 60
    exporter.export([make_span()])

    assert "failed export" not in caplog.records[-1].getMessage()


UNENCODABLE = {"k": object()}


def test_first_success_after_suppressed_failures_reports_them(
    tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    path = tmp_path / "spans.jsonl"
    clock = FixedClock()
    exporter = FileSpanExporter(path, clock=clock)
    exporter.export([make_span(attributes=UNENCODABLE)])
    clock.now += 10
    exporter.export([make_span(attributes=UNENCODABLE)])
    exporter.export([make_span(attributes=UNENCODABLE)])
    exporter.export([make_span()])

    assert caplog.records[-1].getMessage() == (
        f"FileSpanExporter recovered writing to {path}; 2 failed exports since the last warning"
    )


def test_success_after_an_unsuppressed_failure_logs_nothing(
    tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    exporter = FileSpanExporter(tmp_path / "spans.jsonl", clock=FixedClock())
    exporter.export([make_span(attributes=UNENCODABLE)])
    exporter.export([make_span()])

    assert len(caplog.records) == 1


def test_recovery_restarts_the_suppressed_count(
    tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    clock = FixedClock()
    exporter = FileSpanExporter(tmp_path / "spans.jsonl", clock=clock)
    exporter.export([make_span(attributes=UNENCODABLE)])
    clock.now += 10
    exporter.export([make_span(attributes=UNENCODABLE)])
    exporter.export([make_span()])
    clock.now += 60
    exporter.export([make_span(attributes=UNENCODABLE)])

    assert "failed export" not in caplog.records[-1].getMessage()


def test_recoveries_are_reported_at_most_once_a_minute(
    tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    # Exports that alternate between failing and succeeding would otherwise log at each success.
    clock = FixedClock()
    exporter = FileSpanExporter(tmp_path / "spans.jsonl", clock=clock)
    exporter.export([make_span(attributes=UNENCODABLE)])
    clock.now += 10
    exporter.export([make_span(attributes=UNENCODABLE)])
    exporter.export([make_span()])
    clock.now += 10
    exporter.export([make_span(attributes=UNENCODABLE)])
    exporter.export([make_span()])

    assert len(caplog.records) == 2


def test_shutdown_reports_suppressed_failures(
    tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    path = tmp_path / "spans.jsonl"
    clock = FixedClock()
    exporter = FileSpanExporter(path, clock=clock)
    exporter.export([make_span(attributes=UNENCODABLE)])
    clock.now += 10
    exporter.export([make_span(attributes=UNENCODABLE)])
    exporter.shutdown()

    assert caplog.records[-1].getMessage() == (
        f"FileSpanExporter for {path} shut down; 1 failed export since the last warning"
    )


def test_shutdown_without_suppressed_failures_logs_nothing(
    tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    exporter = FileSpanExporter(tmp_path / "spans.jsonl", clock=FixedClock())
    exporter.export([make_span(attributes=UNENCODABLE)])
    exporter.shutdown()

    assert len(caplog.records) == 1


def test_clock_that_raises_returns_failure(tmp_path: Path) -> None:
    exporter = FileSpanExporter(tmp_path / "{date}.jsonl", clock=broken_clock)

    assert exporter.export([make_span()]) is SpanExportResult.FAILURE


def test_clock_that_raises_warns_once_a_minute(
    tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    exporter = FileSpanExporter(tmp_path / "{date}.jsonl", clock=broken_clock)
    exporter.export([make_span()])
    exporter.export([make_span()])

    assert len(caplog.records) == 1


def broken_clock() -> float:
    raise ValueError("clock is broken")


def test_path_with_a_nul_byte_returns_failure(tmp_path: Path) -> None:
    exporter = FileSpanExporter(tmp_path / "spans\x00.jsonl")

    assert exporter.export([make_span()]) is SpanExportResult.FAILURE


def test_log_handler_that_exports_does_not_deadlock(unwritable: Path) -> None:
    exporter = FileSpanExporter(unwritable)
    logger = logging.getLogger(LOGGER_NAME)
    handler = _ExportingHandler(exporter)
    logger.addHandler(handler)
    # A daemon thread, so a deadlocked export cannot keep the test run from exiting.
    thread = threading.Thread(target=exporter.export, args=([make_span()],), daemon=True)
    try:
        thread.start()
        thread.join(timeout=5)
    finally:
        logger.removeHandler(handler)

    assert not thread.is_alive()


class _ExportingHandler(logging.Handler):
    """Sends a span through the exporter for each record, as a log-to-trace bridge might."""

    def __init__(self, exporter: FileSpanExporter) -> None:
        super().__init__()
        self.exporter = exporter

    def emit(self, record: logging.LogRecord) -> None:
        self.exporter.export([make_span("from the log handler")])


def test_unencodable_attribute_returns_failure(tmp_path: Path) -> None:
    span = make_span(attributes={"k": object()})

    assert FileSpanExporter(tmp_path / "s.jsonl").export([span]) is SpanExportResult.FAILURE


def test_unencodable_attribute_warns(tmp_path: Path, caplog: pytest.LogCaptureFixture) -> None:
    FileSpanExporter(tmp_path / "s.jsonl").export([make_span(attributes={"k": object()})])

    assert [r.name for r in caplog.records] == [LOGGER_NAME]


def test_export_after_shutdown_returns_failure(tmp_path: Path) -> None:
    exporter = FileSpanExporter(tmp_path / "spans.jsonl")
    exporter.shutdown()

    assert exporter.export([make_span()]) is SpanExportResult.FAILURE


def test_export_after_shutdown_warns_that_spans_are_dropped(
    tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    path = tmp_path / "spans.jsonl"
    exporter = FileSpanExporter(path)
    exporter.shutdown()
    exporter.export([make_span()])

    assert [r.getMessage() for r in caplog.records] == [
        f"FileSpanExporter for {path} is shut down; spans dropped"
    ]


def test_exports_after_shutdown_warn_once_a_minute(
    tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    clock = FixedClock()
    exporter = FileSpanExporter(tmp_path / "spans.jsonl", clock=clock)
    exporter.shutdown()
    exporter.export([make_span()])
    clock.now += 59
    exporter.export([make_span()])

    assert len(caplog.records) == 1


def test_shutdown_twice_is_safe(tmp_path: Path) -> None:
    exporter = FileSpanExporter(tmp_path / "spans.jsonl")
    exporter.export([make_span()])
    exporter.shutdown()
    exporter.shutdown()

    assert span_names(tmp_path / "spans.jsonl") == ["execute_tool get_user_profile"]


def test_force_flush_returns_true(tmp_path: Path) -> None:
    exporter = FileSpanExporter(tmp_path / "spans.jsonl")
    exporter.export([make_span()])

    assert exporter.force_flush() is True


# --- What the path points at ----------------------------------------------------------------------

needs_posix = pytest.mark.skipif(sys.platform == "win32", reason="needs POSIX files")


@pytest.fixture
def umask_022() -> Iterator[None]:
    previous = os.umask(0o022)
    yield
    os.umask(previous)


@needs_posix
@pytest.mark.usefixtures("umask_022")
def test_new_file_is_readable_by_its_owner_only(tmp_path: Path) -> None:
    path = tmp_path / "spans.jsonl"
    FileSpanExporter(path).export([make_span()])

    assert stat.S_IMODE(path.stat().st_mode) == 0o600


@needs_posix
def test_existing_file_keeps_its_mode(tmp_path: Path) -> None:
    path = tmp_path / "spans.jsonl"
    path.touch(mode=0o640)
    path.chmod(0o640)
    FileSpanExporter(path).export([make_span()])

    assert stat.S_IMODE(path.stat().st_mode) == 0o640


@pytest.fixture
def symlinked(tmp_path: Path) -> tuple[Path, Path]:
    """A symlink at the export path and the empty file it points at."""
    target = tmp_path / "target.jsonl"
    target.touch()
    link = tmp_path / "spans.jsonl"
    link.symlink_to(target)
    return link, target


@needs_posix
def test_symlink_at_the_path_returns_failure(symlinked: tuple[Path, Path]) -> None:
    link, _ = symlinked

    assert FileSpanExporter(link).export([make_span()]) is SpanExportResult.FAILURE


@needs_posix
def test_symlink_at_the_path_is_not_followed(symlinked: tuple[Path, Path]) -> None:
    link, target = symlinked
    FileSpanExporter(link).export([make_span()])

    assert target.read_bytes() == b""


@needs_posix
def test_symlink_at_the_path_warns(
    symlinked: tuple[Path, Path], caplog: pytest.LogCaptureFixture
) -> None:
    link, _ = symlinked
    FileSpanExporter(link).export([make_span()])

    assert [r.name for r in caplog.records] == [LOGGER_NAME]


@pytest.mark.skipif(not hasattr(os, "mkfifo"), reason="needs os.mkfifo")
def test_fifo_at_the_path_returns_failure_without_blocking(tmp_path: Path) -> None:
    path = tmp_path / "spans.jsonl"
    os.mkfifo(path)
    exporter = FileSpanExporter(path)
    results: list[SpanExportResult] = []
    # A daemon thread, so an export blocked on the FIFO cannot keep the test run from exiting.
    thread = threading.Thread(
        target=lambda: results.append(exporter.export([make_span()])), daemon=True
    )
    thread.start()
    thread.join(timeout=5)

    assert results == [SpanExportResult.FAILURE]


# --- Partial lines --------------------------------------------------------------------------------

PARTIAL_LINE = '{"resourceSpans":[{"scopeSpans'


def clean_line(tmp_path: Path) -> str:
    """The line one export of make_span() writes to an empty file, without its newline."""
    path = tmp_path / "clean.jsonl"
    FileSpanExporter(path).export([make_span()])
    return path.read_text().removesuffix("\n")


def test_export_after_a_partial_line_starts_a_new_line(tmp_path: Path) -> None:
    expected = clean_line(tmp_path)
    path = tmp_path / "spans.jsonl"
    path.write_text(PARTIAL_LINE)
    FileSpanExporter(path).export([make_span()])

    assert path.read_text().splitlines() == [PARTIAL_LINE, expected]


def test_export_after_a_short_then_failing_write_starts_a_new_line(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    expected = clean_line(tmp_path)
    path = tmp_path / "spans.jsonl"
    exporter = FileSpanExporter(path)
    monkeypatch.setattr(os, "fdopen", _open_short_then_failing)
    exporter.export([make_span()])
    monkeypatch.undo()
    exporter.export([make_span()])

    assert path.read_text().splitlines()[-1] == expected


def _open_short_then_failing(fd: int, mode: str, buffering: int) -> "_ShortThenFailingFile":
    return _ShortThenFailingFile(io.FileIO(fd, mode.replace("b", "")))


class _ShortThenFailingFile:
    """A file whose first write stores half its bytes and whose next write fails, as on a full disk."""

    def __init__(self, file: io.FileIO) -> None:
        self._file = file
        self._writes = 0

    def write(self, data: memoryview) -> int:
        self._writes += 1
        if self._writes > 1:
            raise OSError("no space left on device")
        return self._file.write(data[: len(data) // 2])

    def __getattr__(self, name: str) -> Any:
        return getattr(self._file, name)


# --- Optional dependency ----------------------------------------------------------------------

_BLOCK_SDK = "import sys; sys.modules['opentelemetry'] = None\n"


def _run_python(code: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [sys.executable, "-c", _BLOCK_SDK + code], capture_output=True, text=True, check=False
    )


def test_import_without_the_sdk_names_the_extra() -> None:
    result = _run_python(
        "try:\n    import detecttrace.otel\nexcept ImportError as error:\n    print(error)\n"
    )

    assert "pip install detecttrace[otel]" in result.stdout


def test_cli_imports_without_the_sdk() -> None:
    result = _run_python("import detecttrace, detecttrace.cli, detecttrace.pipeline\n")

    assert (result.returncode, result.stderr) == (0, "")


# --- Generated fixture ---------------------------------------------------------------------------


def test_generated_fixture_reads_as_the_same_spans_as_the_otlp_variant() -> None:
    formats = Path(__file__).parent / "fixtures" / "formats"
    exported, _ = load_spans(formats / "file_span_exporter" / "traces")
    trace_ids = {span.trace_id for span in exported}
    otlp, _ = load_spans(formats / "otlp_subset" / "traces")

    assert sorted(exported, key=_span_key) == sorted(
        (span for span in otlp if span.trace_id in trace_ids), key=_span_key
    )


def _span_key(span: Span) -> tuple[str, str]:
    return span.trace_id, span.span_id
