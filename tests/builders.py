"""Builders for test spans and OTLP JSON."""

import gzip
import json
from pathlib import Path
from typing import Any

from detecttrace.model import Span

TRACE_ID = "0af7651916cd43dd8448eb211c80319c"


def span_hex(n: int) -> str:
    return f"{n:016x}"


def otlp_value(value: object) -> dict[str, Any]:
    if isinstance(value, bool):
        return {"boolValue": value}
    if isinstance(value, int):
        return {"intValue": str(value)}
    if isinstance(value, float):
        return {"doubleValue": value}
    if isinstance(value, list):
        return {"arrayValue": {"values": [otlp_value(v) for v in value]}}
    return {"stringValue": value}


def otlp_attributes(attributes: dict[str, object]) -> list[dict[str, Any]]:
    return [{"key": key, "value": otlp_value(value)} for key, value in attributes.items()]


def otlp_span(
    span_id: str,
    parent: str = "",
    *,
    trace_id: str = TRACE_ID,
    name: str = "span",
    start_ns: int = 1_000,
    end_ns: int = 2_000,
    status: dict[str, Any] | None = None,
    attributes: dict[str, object] | None = None,
) -> dict[str, Any]:
    return {
        "traceId": trace_id,
        "spanId": span_id,
        "parentSpanId": parent,
        "name": name,
        "startTimeUnixNano": str(start_ns),
        "endTimeUnixNano": str(end_ns),
        "status": status or {},
        "attributes": otlp_attributes(attributes or {}),
    }


def otlp_document(
    spans: list[dict[str, Any]], resource: dict[str, object] | None = None
) -> dict[str, Any]:
    return {
        "resourceSpans": [
            {
                "resource": {"attributes": otlp_attributes(resource or {})},
                "scopeSpans": [{"scope": {"name": "test"}, "spans": spans}],
            }
        ]
    }


def write_jsonl(path: Path, documents: list[dict[str, Any]]) -> Path:
    path.write_text("".join(json.dumps(doc) + "\n" for doc in documents), encoding="utf-8")
    return path


def write_gzip_jsonl(path: Path, documents: list[dict[str, Any]]) -> Path:
    path.write_bytes(gzip.compress("".join(json.dumps(d) + "\n" for d in documents).encode()))
    return path


def make_span(
    span_id: str,
    parent: str | None = None,
    *,
    name: str = "span",
    trace_id: str = TRACE_ID,
    start_ns: int = 0,
    end_ns: int = 100,
    is_error: bool = False,
    attributes: dict[str, object] | None = None,
    resource: dict[str, object] | None = None,
) -> Span:
    return Span(
        trace_id=trace_id,
        span_id=span_id,
        parent_span_id=parent,
        name=name,
        start_ns=start_ns,
        end_ns=end_ns,
        is_error=is_error,
        attributes=attributes or {},
        resource_attributes=resource or {},
    )


# **fields passes make_span keywords through (trace_id, start_ns, end_ns, is_error, resource).
def agent_span(
    span_id: str,
    parent: str | None = None,
    *,
    attributes: dict[str, object] | None = None,
    **fields: Any,
) -> Span:
    return make_span(
        span_id,
        parent,
        name="invoke_agent triage",
        attributes={"gen_ai.operation.name": "invoke_agent", **(attributes or {})},
        **fields,
    )


def case_root(
    span_id: str,
    case_id: object,
    parent: str | None = None,
    *,
    verdict: object = "benign",
    attributes: dict[str, object] | None = None,
    **fields: Any,
) -> Span:
    return agent_span(
        span_id,
        parent,
        attributes={
            "detecttrace.case_id": case_id,
            "detecttrace.verdict": verdict,
            **(attributes or {}),
        },
        **fields,
    )


def tool_span(
    span_id: str,
    parent: str | None,
    tool: str = "get_signin_logs",
    *,
    attributes: dict[str, object] | None = None,
    **fields: Any,
) -> Span:
    return make_span(
        span_id,
        parent,
        name=f"execute_tool {tool}",
        attributes={
            "gen_ai.operation.name": "execute_tool",
            "gen_ai.tool.name": tool,
            **(attributes or {}),
        },
        **fields,
    )
