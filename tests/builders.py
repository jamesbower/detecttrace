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


def langfuse_row(
    row_id: str,
    parent: str | None = None,
    *,
    trace_id: str = TRACE_ID,
    type: str = "SPAN",
    name: str = "span",
    start: str = "2026-09-30T08:00:00.000Z",
    end: str = "2026-09-30T08:00:01.000Z",
    level: str = "DEFAULT",
    attributes: dict[str, object] | None = None,
    resource: dict[str, object] | None = None,
    **fields: Any,
) -> dict[str, Any]:
    """One Langfuse v2 observation row in the API's camelCase form, metadata flattened."""
    metadata = {f"resourceAttributes.{key}": value for key, value in (resource or {}).items()}
    metadata.update({f"attributes.{key}": value for key, value in (attributes or {}).items()})
    return {
        "id": row_id,
        "traceId": trace_id,
        "parentObservationId": parent,
        "type": type,
        "name": name,
        "startTime": start,
        "endTime": end,
        "level": level,
        "input": None,
        "output": None,
        "metadata": metadata,
        **fields,
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


RUN_VERDICTS = "case_id,alert_class,verdict\nDT-1,impossible_travel,TP\nDT-2,impossible_travel,FP\nDT-3,impossible_travel,Benign\n"
RUN_CHECKLIST = "alert_class: impossible_travel\nitems:\n  - {id: signins, tool: get_signin_logs}\n"
RUN_CONFIG = """\
traces: {path: traces}
verdicts: {path: verdicts.csv}
checklists: checklists
output: dashboard.html
label_map: {TP: true_positive, FP: false_positive, Benign: benign}
"""


def run_trace(number: int, case_id: str) -> dict[str, Any]:
    """One OTLP document holding a case root with one tool call, in its own trace."""
    trace_id = f"{number:032x}"
    root = span_hex(number * 2)
    return otlp_document(
        [
            otlp_span(
                root,
                trace_id=trace_id,
                name="invoke_agent triage",
                attributes={
                    "gen_ai.operation.name": "invoke_agent",
                    "detecttrace.case_id": case_id,
                    "detecttrace.alert_class": "impossible_travel",
                    "detecttrace.verdict": "Benign",
                    "detecttrace.prompt_version": "v1",
                },
            ),
            otlp_span(
                span_hex(number * 2 + 1),
                root,
                trace_id=trace_id,
                name="execute_tool get_signin_logs",
                attributes={
                    "gen_ai.operation.name": "execute_tool",
                    "gen_ai.tool.name": "get_signin_logs",
                },
            ),
        ]
    )


def write_run_folder(
    folder: Path,
    *,
    case_ids: tuple[str, ...] = ("DT-1", "DT-2", "DT-3"),
    verdicts: str = RUN_VERDICTS,
    config: str = RUN_CONFIG,
) -> Path:
    """Write traces, verdicts, a checklist and detecttrace.yaml; return the configuration path."""
    (folder / "traces").mkdir(parents=True)
    write_jsonl(
        folder / "traces" / "batch.jsonl",
        [run_trace(number, case_id) for number, case_id in enumerate(case_ids, start=1)],
    )
    (folder / "verdicts.csv").write_text(verdicts, encoding="utf-8")
    (folder / "checklists").mkdir()
    (folder / "checklists" / "impossible_travel.yaml").write_text(RUN_CHECKLIST, encoding="utf-8")
    config_path = folder / "detecttrace.yaml"
    config_path.write_text(config, encoding="utf-8")
    return config_path
