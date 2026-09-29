"""Read OTLP JSON trace files (JSON lines or one document, plain or gzip) into spans."""

import gzip
import json
from collections.abc import Iterator
from pathlib import Path
from typing import Any, TextIO

from detecttrace.model import Issue, IssueKind, Span

_GZIP_MAGIC = b"\x1f\x8b"
_STATUS_ERROR = (2, "STATUS_CODE_ERROR")

# OTLP JSON is untyped input: Any is the honest type until fields are validated in _to_span.
Json = Any


def load_spans(path: Path) -> tuple[list[Span], list[Issue]]:
    """Read every trace file at `path` (a file or a folder) and return unique spans.

    Files are read in sorted path order and the first copy of a duplicate span wins,
    so the same input always gives the same spans.
    """
    if not path.exists():
        raise FileNotFoundError(
            f"Trace path not found: {path}. Check traces.path in detecttrace.yaml."
        )
    spans: list[Span] = []
    issues: list[Issue] = []
    seen: dict[tuple[str, str], Span] = {}
    for file_path in _list_trace_files(path):
        for document in _read_documents(file_path, issues):
            for span in _parse_document(document, file_path, issues):
                key = (span.trace_id, span.span_id)
                first = seen.get(key)
                if first is not None:
                    kind = (
                        IssueKind.DUPLICATE_SPAN
                        if span == first
                        else IssueKind.CONFLICTING_DUPLICATE_SPAN
                    )
                    issues.append(Issue(kind, str(file_path), f"{span.trace_id}/{span.span_id}"))
                    continue
                seen[key] = span
                spans.append(span)
    return spans, issues


def _list_trace_files(path: Path) -> list[Path]:
    if path.is_file():
        return [path]
    files = [p for p in path.rglob("*") if p.is_file() and not p.name.startswith(".")]
    # POSIX form so Windows and Linux read files, and so pick duplicate winners, in the same order.
    return sorted(files, key=lambda p: p.relative_to(path).as_posix())


def _read_documents(file_path: Path, issues: list[Issue]) -> Iterator[Json]:
    subject = str(file_path)
    try:
        with _open_text(file_path) as handle:
            line_number = 0
            first_line = ""
            for line in handle:
                line_number += 1
                if line.strip():
                    first_line = line
                    break
            if not first_line:
                issues.append(Issue(IssueKind.EMPTY_FILE, subject))
                return
            first_document = _parse_json(first_line)
            if first_document is None:
                # Not JSON lines: the whole file is one (pretty-printed) document.
                document = _parse_json(first_line + handle.read())
                if document is None:
                    issues.append(Issue(IssueKind.INVALID_FILE, subject, "not valid JSON"))
                    return
                yield document
                return
            yield first_document
            for line in handle:
                line_number += 1
                if not line.strip():
                    continue
                document = _parse_json(line)
                if document is None:
                    # Only the last line of a file can lack its newline: a writer is still busy.
                    kind = (
                        IssueKind.INVALID_LINE if line.endswith("\n") else IssueKind.TRUNCATED_LINE
                    )
                    issues.append(Issue(kind, subject, f"line {line_number}"))
                    continue
                yield document
    except EOFError:
        issues.append(
            Issue(
                IssueKind.TRUNCATED_FILE,
                subject,
                "compressed file ends early; earlier data was read",
            )
        )
    except (OSError, UnicodeDecodeError) as error:
        issues.append(Issue(IssueKind.INVALID_FILE, subject, str(error)))


def _open_text(file_path: Path) -> TextIO:
    with file_path.open("rb") as probe:
        is_gzip = probe.read(2) == _GZIP_MAGIC
    if is_gzip:
        return gzip.open(file_path, "rt", encoding="utf-8")
    return file_path.open(encoding="utf-8")


def _parse_json(text: str) -> Json | None:
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        return None


def _parse_document(document: Json, file_path: Path, issues: list[Issue]) -> Iterator[Span]:
    if not isinstance(document, dict) or not isinstance(document.get("resourceSpans"), list):
        issues.append(
            Issue(IssueKind.INVALID_FILE, str(file_path), "a document has no resourceSpans")
        )
        return
    for resource_spans in document["resourceSpans"]:
        resource = resource_spans.get("resource") or {}
        resource_attributes = _decode_attributes(resource.get("attributes") or [])
        for scope_spans in resource_spans.get("scopeSpans") or []:
            for raw in scope_spans.get("spans") or []:
                span = _to_span(raw, resource_attributes)
                if span is None:
                    issues.append(
                        Issue(IssueKind.INVALID_SPAN, str(file_path), "missing IDs or times")
                    )
                    continue
                yield span


def _to_span(raw: Json, resource_attributes: dict[str, object]) -> Span | None:
    try:
        trace_id = str(raw["traceId"]).lower()
        span_id = str(raw["spanId"]).lower()
        start_ns = int(raw["startTimeUnixNano"])
        end_ns = int(raw.get("endTimeUnixNano") or start_ns)
    except (KeyError, TypeError, ValueError, AttributeError):
        return None
    if not trace_id or not span_id:
        return None
    status = raw.get("status") or {}
    return Span(
        trace_id=trace_id,
        span_id=span_id,
        parent_span_id=str(raw.get("parentSpanId") or "").lower() or None,
        name=str(raw.get("name") or ""),
        start_ns=start_ns,
        end_ns=end_ns,
        is_error=status.get("code") in _STATUS_ERROR,
        attributes=_decode_attributes(raw.get("attributes") or []),
        resource_attributes=resource_attributes,
    )


def _decode_attributes(items: Json) -> dict[str, object]:
    return {item["key"]: _decode_value(item.get("value") or {}) for item in items if "key" in item}


def _decode_value(value: Json) -> object:
    if "stringValue" in value:
        return value["stringValue"]
    if "boolValue" in value:
        return bool(value["boolValue"])
    if "intValue" in value:
        return int(value["intValue"])  # OTLP JSON encodes 64-bit integers as strings
    if "doubleValue" in value:
        return float(value["doubleValue"])
    if "arrayValue" in value:
        return [_decode_value(v) for v in value["arrayValue"].get("values") or []]
    if "kvlistValue" in value:
        return _decode_attributes(value["kvlistValue"].get("values") or [])
    if "bytesValue" in value:
        return value["bytesValue"]
    return None
