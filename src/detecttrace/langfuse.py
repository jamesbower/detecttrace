"""Parse Langfuse v4 observation exports into spans."""

import re
from collections.abc import Iterator
from datetime import UTC, datetime, timedelta

from detecttrace import conventions
from detecttrace.model import Issue, IssueKind, Span, to_short_label
from detecttrace.otlp import Json, Report, parse_json_text

_MAX_ID_LENGTH = 200
_HEX_ID = re.compile(r"[0-9a-fA-F]+")
# The API writes `2026-09-30T08:03:03.000Z`; the blob export writes `2026-09-30 08:03:03.000000`,
# in UTC but without a zone. [0-9], not \d, which would also match other scripts' digits.
_TIMESTAMP = re.compile(
    r"([0-9]{4})-([0-9]{2})-([0-9]{2})[T ]([0-9]{2}):([0-9]{2}):([0-9]{2})"
    r"(?:\.([0-9]+))?(Z|[+-][0-9]{2}:[0-9]{2})?"
)
_EPOCH = datetime(1970, 1, 1, tzinfo=UTC)
_ONE_SECOND = timedelta(seconds=1)
_NANOS_PER_SECOND = 1_000_000_000
_UINT64_LIMIT = 2**64
_ERROR_LEVEL = "ERROR"
_TOOL_TYPE = "TOOL"
_EXECUTE_TOOL_PREFIX = conventions.EXECUTE_TOOL + " "
_OPERATIONS = {"AGENT": conventions.INVOKE_AGENT, _TOOL_TYPE: conventions.EXECUTE_TOOL}
_ATTRIBUTES_PREFIX = "attributes."
_RESOURCE_PREFIX = "resourceAttributes."
_NAMELESS_KEYS = ("attributes", "attributes.", "resourceAttributes", "resourceAttributes.")
_SCOPE = "scope"
_SCOPE_PREFIX = "scope."
_OTHER_METADATA_PREFIX = "langfuse.metadata."
# (API name, blob export name, attribute): Langfuse's own fields, readable so a mapping can name them.
_FIELD_ATTRIBUTES = (
    ("promptName", "prompt_name", "langfuse.prompt_name"),
    ("promptVersion", "prompt_version", "langfuse.prompt_version"),
    ("version", "version", "langfuse.version"),
    ("sessionId", "session_id", "langfuse.session_id"),
    ("traceName", "trace_name", "langfuse.trace_name"),
)


def parse_document(
    document: Json, subject: str, line_number: int | None, issues: list[Issue]
) -> Iterator[Span]:
    """Yield the valid spans of one Langfuse document, appending an Issue for each problem.

    A document is an API page (an object with a `data` list of rows), a JSON array of rows
    (the blob JSON export), or one row (a line of the blob JSONL export). Rows may use the
    API's camelCase or the blob export's snake_case field names. `output`, the tool result,
    is never read.

    A row without `input` and `output` keys gets a LANGFUSE_WITHOUT_IO issue with no detail;
    load_spans turns those into one note when no row of the input has either key.
    """
    prefix = "" if line_number is None else f"line {line_number}: "

    def report(kind: IssueKind, detail: str) -> None:
        issues.append(Issue(kind, subject, prefix + detail))

    if isinstance(document, list):
        rows = _number_rows(document, "")
    elif isinstance(document, dict) and "data" in document:
        if not isinstance(document["data"], list):
            report(IssueKind.INVALID_LANGFUSE_DOCUMENT, "data is not a list")
            return
        rows = _number_rows(document["data"], "data")
    elif isinstance(document, dict) and "resourceSpans" in document:
        report(
            IssueKind.INVALID_LANGFUSE_DOCUMENT,
            "an OTLP document, not Langfuse rows; set traces.format to otlp_jsonl or otlp_json",
        )
        return
    elif isinstance(document, dict):
        rows = iter([("row", document)])
    else:
        report(IssueKind.INVALID_LANGFUSE_DOCUMENT, "a document is not an object or a list")
        return
    for where, row in rows:
        span = _to_span(row, where, report)
        if span is not None:
            yield span


def _number_rows(rows: list[Json], name: str) -> Iterator[tuple[str, Json]]:
    for index, row in enumerate(rows):
        yield f"{name}[{index}]", row


def _to_span(row: Json, where: str, report: Report) -> Span | None:
    if not isinstance(row, dict):
        report(IssueKind.INVALID_LANGFUSE_ROW, f"{where} is not an object")
        return None
    if isinstance(row.get("observations"), list):
        report(IssueKind.LEGACY_LANGFUSE_TRACE, f"{where}: a trace object holding observations")
        return None
    try:
        span_id = _to_id(row.get("id"), "id")
        trace_id = _to_id(_read_field(row, "traceId", "trace_id"), "trace ID")
        parent = _read_field(row, "parentObservationId", "parent_observation_id")
        # The blob export writes "" where the API writes null.
        parent_span_id = None if parent is None or parent == "" else _to_id(parent, "parent ID")
        start_ns = _to_nanos(_read_field(row, "startTime", "start_time"), "start time")
        end_ns = _to_nanos(_read_field(row, "endTime", "end_time"), "end time")
        if end_ns < start_ns:
            raise ValueError("end time is before start time")
    except ValueError as error:
        report(IssueKind.INVALID_LANGFUSE_ROW, f"{where}: {error}")
        return None
    if "input" not in row and "output" not in row:
        # The field groups a user asked for decide which keys exist, so absence (not null)
        # means the io group was left out: no tool arguments, and metadata may be cut.
        report(IssueKind.LANGFUSE_WITHOUT_IO, "")
    row_type = _read_text(row, "type", where, report)
    name = _read_text(row, "name", where, report) or ""
    attributes, resource_attributes = _read_metadata(row.get("metadata"), where, report)
    for api_name, blob_name, key in _FIELD_ATTRIBUTES:
        value = _read_field(row, api_name, blob_name)
        if value is not None and value != "":
            _add_derived(attributes, key, value, where, report)
    if conventions.OPERATION_ATTRIBUTE not in attributes and row_type in _OPERATIONS:
        attributes[conventions.OPERATION_ATTRIBUTE] = _OPERATIONS[row_type]
    if row_type == _TOOL_TYPE:
        # Langfuse names a tool row by its tool and moves the call arguments into `input`.
        # A name in the OTLP `execute_tool <tool>` form is left to the span-name fallback,
        # which strips the prefix, or reports a missing tool name, exactly as for OTLP.
        if name and name != conventions.EXECUTE_TOOL and not name.startswith(_EXECUTE_TOOL_PREFIX):
            attributes.setdefault(conventions.TOOL_NAME, name)
        arguments = row.get("input")
        if arguments is not None and arguments != "":
            attributes.setdefault(conventions.TOOL_CALL_ARGUMENTS, arguments)
    return Span(
        trace_id=trace_id,
        span_id=span_id,
        parent_span_id=parent_span_id,
        name=name,
        start_ns=start_ns,
        end_ns=end_ns,
        is_error=_read_text(row, "level", where, report) == _ERROR_LEVEL,
        attributes=attributes,
        resource_attributes=resource_attributes,
    )


def _read_field(row: dict[str, Json], api_name: str, blob_name: str) -> Json:
    return row[api_name] if api_name in row else row.get(blob_name)


def _to_id(value: Json, label: str) -> str:
    if value is None:
        raise ValueError(f"{label} is missing")
    if not isinstance(value, str):
        raise ValueError(f"{label} is not a string")
    if not value:
        raise ValueError(f"{label} is empty")
    if len(value) > _MAX_ID_LENGTH:
        raise ValueError(f"{label} is longer than {_MAX_ID_LENGTH} characters")
    # OpenTelemetry IDs are hex, so an ID sent in either case still links to its parent.
    return value.lower() if _HEX_ID.fullmatch(value) else value


def _to_nanos(value: Json, label: str) -> int:
    if value is None:
        raise ValueError(f"{label} is missing")
    if not isinstance(value, str):
        raise ValueError(f"{label} is not a string")
    match = _TIMESTAMP.fullmatch(value)
    if match is None:
        raise ValueError(f"{label} is not a date and time")
    year, month, day, hour, minute, second, fraction, zone = match.groups()
    try:
        moment = datetime(
            int(year),
            int(month),
            int(day),
            int(hour),
            int(minute),
            int(second),
            tzinfo=UTC,
        )
    except ValueError:
        raise ValueError(f"{label} is not a date and time") from None
    seconds = (moment - _EPOCH) // _ONE_SECOND
    if zone is not None and zone != "Z":
        offset_hours, offset_minutes = int(zone[1:3]), int(zone[4:6])
        if offset_hours > 23 or offset_minutes > 59:
            raise ValueError(f"{label} is not a date and time")
        offset = offset_hours * 3600 + offset_minutes * 60
        seconds -= offset if zone[0] == "+" else -offset
    # Digits past nanoseconds are dropped; a span time has no finer unit.
    nanos = seconds * _NANOS_PER_SECOND + int((fraction or "")[:9].ljust(9, "0"))
    if not 0 <= nanos < _UINT64_LIMIT:
        raise ValueError(f"{label} is out of range")
    return nanos


def _read_text(row: dict[str, Json], field: str, where: str, report: Report) -> str | None:
    value = row.get(field)
    if value is None or isinstance(value, str):
        return value
    report(IssueKind.INVALID_ATTRIBUTE, f"{where}: {field} is not a string")
    return None


def _read_metadata(
    metadata: Json, where: str, report: Report
) -> tuple[dict[str, object], dict[str, object]]:
    """Split Langfuse's flat metadata into span attributes and resource attributes.

    `attributes.<name>` and `resourceAttributes.<name>` hold the OpenTelemetry attributes;
    `scope.*` is left out; any other key is named `langfuse.metadata.<key>`. Values keep the
    type Langfuse wrote: typed in API pages, all strings in the blob export. Metadata given
    as a JSON string is decoded once; values inside it are never decoded again.
    """
    if metadata is None:
        return {}, {}
    if isinstance(metadata, str):
        decoded = parse_json_text(metadata)
        if isinstance(decoded, dict):
            metadata = decoded
    if not isinstance(metadata, dict):
        report(
            IssueKind.LANGFUSE_METADATA_NOT_OBJECT,
            f"{where}: metadata is a {type(metadata).__name__}",
        )
        return {}, {}
    attributes: dict[str, object] = {}
    resource_attributes: dict[str, object] = {}
    others: list[tuple[str, object]] = []
    for key, value in metadata.items():
        if key.startswith(_ATTRIBUTES_PREFIX) and len(key) > len(_ATTRIBUTES_PREFIX):
            attributes[key[len(_ATTRIBUTES_PREFIX) :]] = value
        elif key.startswith(_RESOURCE_PREFIX) and len(key) > len(_RESOURCE_PREFIX):
            resource_attributes[key[len(_RESOURCE_PREFIX) :]] = value
        elif key in _NAMELESS_KEYS:
            report(IssueKind.INVALID_ATTRIBUTE, f"{where}: metadata key '{key}' names no attribute")
        elif key == _SCOPE or key.startswith(_SCOPE_PREFIX):
            continue
        else:
            others.append((_OTHER_METADATA_PREFIX + key, value))
    # Added after every span attribute, whatever the key order, so a span attribute wins.
    for key, value in others:
        _add_derived(attributes, key, value, where, report)
    return attributes, resource_attributes


def _add_derived(
    attributes: dict[str, object], key: str, value: object, where: str, report: Report
) -> None:
    """Add an attribute Langfuse derived from its own fields; a span attribute of that name wins."""
    if key not in attributes:
        attributes[key] = value
    elif attributes[key] != value:
        report(
            IssueKind.INVALID_ATTRIBUTE,
            f"{where}: {to_short_label(key)} is given twice with different values; "
            "the span attribute was kept",
        )
