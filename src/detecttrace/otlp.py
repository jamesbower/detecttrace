"""Parse OTLP JSON documents into spans."""

import json
import re
from collections.abc import Callable, Iterator
from typing import Any, TypeVar

from detecttrace.model import Issue, IssueKind, Span

_STATUS_ERROR = (2, "STATUS_CODE_ERROR")
_TRACE_ID = re.compile(r"[0-9a-f]{32}")
_SPAN_ID = re.compile(r"[0-9a-f]{16}")
_HEX = re.compile(r"[0-9a-f]*")
_BASE64 = re.compile(r"[A-Za-z0-9+/=]+")
_INT_TEXT = re.compile(r"-?[0-9]+")
_UINT64_LIMIT = 2**64
# Strict UTF-8 decoding rejects raw surrogates, so only a JSON escape can produce one.
# A valid pair decodes to one character, so any surrogate left over is a lone one.
_MAY_HOLD_SURROGATE = re.compile(r"\\u[dD][89a-fA-F]")
_SURROGATE = re.compile("[\ud800-\udfff]")

# OTLP JSON is untyped input: Any is the honest type until fields are validated in _to_span.
Json = Any
Report = Callable[[IssueKind, str], None]
T = TypeVar("T")


def parse_document(
    document: Json, subject: str, line_number: int | None, issues: list[Issue]
) -> Iterator[Span]:
    """Yield the valid spans of one OTLP JSON document, appending an Issue for each problem.

    `subject` names the file and `line_number` the JSON line (None for a one-document file);
    both go into the issues so a reader can find the bad input.
    """
    prefix = "" if line_number is None else f"line {line_number}: "

    def report(kind: IssueKind, detail: str) -> None:
        issues.append(Issue(kind, subject, prefix + detail))

    if not isinstance(document, dict) or not isinstance(document.get("resourceSpans"), list):
        report(IssueKind.INVALID_FILE, "a document has no resourceSpans")
        return
    for index, resource_spans in enumerate(document["resourceSpans"]):
        yield from _parse_resource_spans(resource_spans, f"resourceSpans[{index}]", report)


def _parse_resource_spans(resource_spans: Json, where: str, report: Report) -> Iterator[Span]:
    if not isinstance(resource_spans, dict):
        report(IssueKind.INVALID_FILE, f"{where} is not an object")
        return
    resource = resource_spans.get("resource") or {}
    if not isinstance(resource, dict):
        report(IssueKind.INVALID_FILE, f"{where}.resource is not an object")
        return
    # NOTE: this one dict is shared by every span of the resource; treat it as read-only.
    resource_attributes = _decode_attributes(
        resource.get("attributes"), f"{where}.resource", report
    )
    scope_spans_list = resource_spans.get("scopeSpans") or []
    if not isinstance(scope_spans_list, list):
        report(IssueKind.INVALID_FILE, f"{where}.scopeSpans is not a list")
        return
    for scope_index, scope_spans in enumerate(scope_spans_list):
        scope_where = f"{where}.scopeSpans[{scope_index}]"
        if not isinstance(scope_spans, dict):
            report(IssueKind.INVALID_FILE, f"{scope_where} is not an object")
            continue
        raw_spans = scope_spans.get("spans") or []
        if not isinstance(raw_spans, list):
            report(IssueKind.INVALID_FILE, f"{scope_where}.spans is not a list")
            continue
        for span_index, raw in enumerate(raw_spans):
            span_where = f"{scope_where}.spans[{span_index}]"
            span = _to_span(raw, resource_attributes, span_where, report)
            if span is not None:
                yield span


def _to_span(
    raw: Json, resource_attributes: dict[str, object], where: str, report: Report
) -> Span | None:
    if not isinstance(raw, dict):
        report(IssueKind.INVALID_SPAN, f"{where} is not an object")
        return None
    try:
        trace_id = _to_id(raw.get("traceId"), _TRACE_ID, "trace ID", 32)
        span_id = _to_id(raw.get("spanId"), _SPAN_ID, "span ID", 16)
        parent = raw.get("parentSpanId")
        parent_span_id = (
            None if parent in (None, "") else _to_id(parent, _SPAN_ID, "parent span ID", 16)
        )
        start_ns = _to_nanos(raw.get("startTimeUnixNano"), "start time")
        end = raw.get("endTimeUnixNano")
        end_ns = start_ns if end is None else _to_nanos(end, "end time")
        if end_ns < start_ns:
            raise ValueError("end time is before start time")
        status = raw.get("status") or {}
        if not isinstance(status, dict):
            raise ValueError("status is not an object")
    except ValueError as error:
        report(IssueKind.INVALID_SPAN, f"{where}: {error}")
        return None
    return Span(
        trace_id=trace_id,
        span_id=span_id,
        parent_span_id=parent_span_id,
        name=_to_name(raw.get("name"), where, report),
        start_ns=start_ns,
        end_ns=end_ns,
        is_error=status.get("code") in _STATUS_ERROR,
        attributes=_decode_attributes(raw.get("attributes"), where, report),
        resource_attributes=resource_attributes,
    )


def _to_id(value: Json, pattern: re.Pattern[str], label: str, length: int) -> str:
    if not isinstance(value, str):
        raise ValueError(f"{label} is missing or not a string")
    lowered = value.lower()
    if pattern.fullmatch(lowered):
        return lowered
    reason = f"{label} must be {length} hex characters"
    if _BASE64.fullmatch(value) and not _HEX.fullmatch(lowered):
        reason += "; IDs look base64; OTLP JSON uses hex"
    raise ValueError(reason)


def _to_name(value: Json, where: str, report: Report) -> str:
    if value is None or isinstance(value, str):
        return value or ""
    report(IssueKind.INVALID_ATTRIBUTE, f"{where}: name is not a string")
    return ""


def _to_nanos(value: Json, label: str) -> int:
    # OTLP JSON times are unsigned 64-bit integers, written as digit strings or JSON integers.
    # The length check keeps int() off absurdly long digit strings.
    if isinstance(value, str) and value.isascii() and value.isdigit() and len(value) <= 20:
        value = int(value)
    if isinstance(value, int) and not isinstance(value, bool) and 0 <= value < _UINT64_LIMIT:
        return value
    raise ValueError(f"{label} must be an unsigned 64-bit integer of nanoseconds")


def _decode_attributes(items: Json, where: str, report: Report) -> dict[str, object]:
    """Decode an attribute list, skipping and reporting each malformed entry."""
    if items is None:
        return {}
    if not isinstance(items, list):
        report(IssueKind.INVALID_ATTRIBUTE, f"{where}.attributes is not a list")
        return {}
    attributes: dict[str, object] = {}
    for index, item in enumerate(items):
        # Fast path for the common string attribute: this loop runs millions of times at scale.
        if isinstance(item, dict):
            key = item.get("key")
            value = item.get("value")
            if isinstance(key, str) and isinstance(value, dict):
                text = value.get("stringValue")
                if isinstance(text, str):
                    attributes[key] = text
                    continue
        try:
            key, value = _decode_key_value(item)
        except (ValueError, RecursionError) as error:
            report(IssueKind.INVALID_ATTRIBUTE, f"{where}.attributes[{index}]: {error}")
            continue
        attributes[key] = value
    return attributes


def _decode_key_value(item: Json) -> tuple[str, object]:
    if not isinstance(item, dict) or not isinstance(item.get("key"), str):
        raise ValueError("attribute has no string key")
    value = item.get("value")
    return item["key"], _decode_value({} if value is None else value)


def _decode_value(value: Json) -> object:
    """Decode one OTLP AnyValue; raise ValueError when its shape is wrong."""
    if not isinstance(value, dict):
        raise ValueError("value is not an object")
    if "stringValue" in value:
        return _require(value["stringValue"], str, "stringValue")
    if "boolValue" in value:
        return _require(value["boolValue"], bool, "boolValue")
    if "intValue" in value:
        return _to_int(value["intValue"])
    if "doubleValue" in value:
        raw = value["doubleValue"]
        if isinstance(raw, int | float) and not isinstance(raw, bool):
            return _to_float(raw)
        # Strings cover the JSON encodings "NaN", "Infinity" and "-Infinity".
        return float(_require(raw, str, "doubleValue"))
    if "arrayValue" in value:
        return [_decode_value(v) for v in _values(value["arrayValue"], "arrayValue")]
    if "kvlistValue" in value:
        return dict(_decode_key_value(i) for i in _values(value["kvlistValue"], "kvlistValue"))
    if "bytesValue" in value:
        return _require(value["bytesValue"], str, "bytesValue")
    if value:
        # A mistyped key such as "stringvalue" would otherwise decode to None unnoticed.
        raise ValueError(f"unknown value type: {', '.join(sorted(value))}")
    return None


def _to_int(raw: Json) -> int:
    # OTLP JSON encodes 64-bit integers as strings. int() alone would also accept
    # spaces, underscores, "+" and non-ASCII digits.
    if isinstance(raw, int) and not isinstance(raw, bool):
        return raw
    if not _INT_TEXT.fullmatch(_require(raw, str, "intValue")):
        raise ValueError("intValue is not an integer")
    return int(raw)


def _to_float(raw: int | float) -> float:
    try:
        return float(raw)
    except OverflowError:
        raise ValueError("doubleValue is out of range") from None


def _values(container: Json, name: str) -> list[Json]:
    if not isinstance(container, dict):
        raise ValueError(f"{name} is not an object")
    values = container.get("values") or []
    if not isinstance(values, list):
        raise ValueError(f"{name}.values is not a list")
    return values


def _require(value: Json, expected: type[T], name: str) -> T:
    if not isinstance(value, expected):
        raise ValueError(f"{name} is not a {expected.__name__}")
    return value


def parse_json_text(text: str) -> Json | None:
    """Decode JSON text, or return None when it is not valid JSON or nests too deep."""
    # A str, never bytes: json.loads on bytes would also accept UTF-16 and UTF-32.
    try:
        document = json.loads(text)
    except (ValueError, RecursionError):
        return None
    return _replace_surrogates(document) if _MAY_HOLD_SURROGATE.search(text) else document


def _replace_surrogates(document: Json) -> Json:
    """Replace lone surrogates in every string and key with U+FFFD, in place where possible.

    A lone surrogate can't be encoded as UTF-8, so one left in a case ID or argument would
    make the results write fail. The walk uses a stack, since a document can nest as deep
    as the JSON parser allows.
    """
    if isinstance(document, str):
        return _SURROGATE.sub("\ufffd", document)
    stack = [document]
    while stack:
        container = stack.pop()
        if isinstance(container, list):
            for index, value in enumerate(container):
                if isinstance(value, str):
                    container[index] = _SURROGATE.sub("\ufffd", value)
                elif isinstance(value, list | dict):
                    stack.append(value)
        elif isinstance(container, dict):
            items = list(container.items())
            container.clear()
            for key, value in items:
                if isinstance(value, str):
                    value = _SURROGATE.sub("\ufffd", value)
                elif isinstance(value, list | dict):
                    stack.append(value)
                container[_SURROGATE.sub("\ufffd", key)] = value
    return document
