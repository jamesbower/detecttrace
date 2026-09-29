"""Builders for test spans and OTLP JSON."""

import gzip
import json
from pathlib import Path
from typing import Any

TRACE_ID = "0af7651916cd43dd8448eb211c80319c"


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
