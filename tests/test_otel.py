"""FileSpanExporter writes OTLP JSON lines that the trace reader reads back unchanged."""

import json
import logging
import os
import subprocess
import sys
import threading
from collections.abc import Callable, Iterator, Mapping
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


def export_from_threads(path: Path, thread_count: int, spans_per_thread: int) -> None:
    provider = TracerProvider()
    provider.add_span_processor(SimpleSpanProcessor(FileSpanExporter(path)))
    tracer = provider.get_tracer("t")

    def work() -> None:
        for index in range(spans_per_thread):
            # Large spans make a torn or interleaved write likely if the lock were missing.
            tracer.start_span(f"span {index}", attributes={"payload": "x" * 5000}).end()

    threads = [threading.Thread(target=work) for _ in range(thread_count)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()


def test_threads_writing_at_once_give_whole_lines(tmp_path: Path) -> None:
    path = tmp_path / "spans.jsonl"
    export_from_threads(path, thread_count=8, spans_per_thread=200)

    # json.loads fails on any interleaved or cut line.
    assert len(span_names(path)) == 1600


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
    """Export one span from a forked child; return the child's pid once it has exited."""
    child = os.fork()
    if child == 0:  # pragma: no cover - runs in the child process
        result = exporter.export([make_span(name)])
        os._exit(0 if result is SpanExportResult.SUCCESS else 1)
    os.waitpid(child, 0)
    return child


@pytest.mark.skipif(not hasattr(os, "fork"), reason="needs os.fork")
def test_forked_child_writes_its_own_pid_file(tmp_path: Path) -> None:
    exporter = FileSpanExporter(tmp_path / "{pid}.jsonl")
    exporter.export([make_span("parent before fork")])
    child = fork_and_export(exporter, "child")
    exporter.export([make_span("parent after fork")])

    assert {path.name: span_names(path) for path in tmp_path.iterdir()} == {
        f"{os.getpid()}.jsonl": ["parent before fork", "parent after fork"],
        f"{child}.jsonl": ["child"],
    }


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
