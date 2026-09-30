"""Internal records shared by the loader, the case builder, and the join."""

from dataclasses import dataclass
from enum import StrEnum


class InputFileError(Exception):
    """Input the run cannot use at all; the CLI exits 1."""


def describe_os_error(error: OSError) -> str:
    """Describe a file system error without the path, which must not reach the dashboard."""
    return error.strerror or type(error).__name__


MAX_LABEL_LENGTH = 200
SHORTENED_DETAIL = f"longer than {MAX_LABEL_LENGTH} characters; shortened"
# A shortened value is reported once per field and start, not once per case that carries it.
REPORT_KEY_LENGTH = 60


def to_short_label(text: str) -> str:
    """`text` cut to MAX_LABEL_LENGTH characters, ending in "…", when it is longer.

    The same text always gives the same result, so a case ID shortened in the traces and in
    the verdict file still joins.
    """
    if len(text) <= MAX_LABEL_LENGTH:
        return text
    return text[: MAX_LABEL_LENGTH - 1] + "\u2026"


class Verdict(StrEnum):
    TRUE_POSITIVE = "true_positive"
    FALSE_POSITIVE = "false_positive"
    BENIGN = "benign"


class IssueKind(StrEnum):
    EMPTY_FILE = "empty_file"
    TRUNCATED_LINE = "truncated_line"
    TRUNCATED_FILE = "truncated_file"
    INVALID_LINE = "invalid_line"
    INVALID_FILE = "invalid_file"
    INVALID_SPAN = "invalid_span"
    DUPLICATE_SPAN = "duplicate_span"
    CONFLICTING_DUPLICATE_SPAN = "conflicting_duplicate_span"
    INVALID_ATTRIBUTE = "invalid_attribute"
    MISSING_TOOL_NAME = "missing_tool_name"
    AGENT_WITHOUT_CASE_ID = "agent_without_case_id"
    ORPHAN_TOOL_SPAN = "orphan_tool_span"
    BROKEN_PARENT_CHAIN = "broken_parent_chain"
    NESTED_CASE = "nested_case"
    INCOMPLETE_TRACE = "incomplete_trace"
    VERSION_CONFLICT = "version_conflict"
    DUPLICATE_ROOT = "duplicate_root"
    INVALID_VERDICT_ROW = "invalid_verdict_row"
    LONG_VERDICT_VALUE = "long_verdict_value"
    DUPLICATE_VERDICT = "duplicate_verdict"
    CONFLICTING_ANALYST_VERDICT = "conflicting_analyst_verdict"
    ROOT_WITHOUT_VERDICT = "root_without_verdict"
    VERDICT_WITHOUT_ROOT = "verdict_without_root"
    ALERT_CLASS_CONFLICT = "alert_class_conflict"
    UNMAPPED_ANALYST_LABEL = "unmapped_analyst_label"
    UNMAPPED_AGENT_LABEL = "unmapped_agent_label"
    MISSING_AGENT_VERDICT = "missing_agent_verdict"
    UNREADABLE_ARGUMENTS = "unreadable_arguments"
    UNREADABLE_DURATION = "unreadable_duration"
    UNREADABLE_KQL_TIMESPAN = "unreadable_kql_timespan"
    RULE_TYPE_MISMATCH = "rule_type_mismatch"
    MISSING_TOOL_ARGUMENTS = "missing_tool_arguments"
    UNKNOWN_CHECKLIST_TOOL = "unknown_checklist_tool"
    UNUSED_CHECKLIST = "unused_checklist"
    CONSOLE_EXPORTER_OUTPUT = "console_exporter_output"
    UNSUPPORTED_COMPRESSION = "unsupported_compression"
    INVALID_LANGFUSE_DOCUMENT = "invalid_langfuse_document"
    INVALID_LANGFUSE_ROW = "invalid_langfuse_row"
    LANGFUSE_METADATA_NOT_OBJECT = "langfuse_metadata_not_object"
    LEGACY_LANGFUSE_TRACE = "legacy_langfuse_trace"
    LANGFUSE_WITHOUT_IO = "langfuse_without_io"
    LANGFUSE_NO_TOOL_CALLS = "langfuse_no_tool_calls"


@dataclass(frozen=True, slots=True)
class Issue:
    kind: IssueKind
    subject: str  # the file, span, or case ID the issue is about
    # For UNMAPPED_ANALYST_LABEL, UNMAPPED_AGENT_LABEL and UNKNOWN_CHECKLIST_TOOL this is the
    # bare label or tool the summary groups by, never a sentence; for other kinds it describes.
    detail: str = ""


@dataclass(frozen=True, slots=True)
class Span:
    trace_id: str  # lowercase hex; a Langfuse ID that isn't hex is kept as given
    span_id: str  # lowercase hex; a Langfuse ID that isn't hex is kept as given
    parent_span_id: str | None
    name: str
    start_ns: int
    end_ns: int
    is_error: bool
    attributes: dict[str, object]
    resource_attributes: dict[str, object]


@dataclass(frozen=True, slots=True)
class ToolCall:
    span_id: str
    tool_name: str  # empty when unknown: satisfies no checklist item
    # JSON string as sent, or a map in its original key order.
    arguments: str | dict[str, object] | None
    start_ns: int
    end_ns: int
    is_failed: bool


@dataclass(frozen=True, slots=True)
class TraceCase:
    case_id: str
    trace_id: str
    root_span_id: str
    start_ns: int
    end_ns: int
    alert_class: str | None
    agent_label: str | None
    prompt_version: str | None
    # ordered by start time, then span ID
    tool_calls: tuple[ToolCall, ...]
    is_incomplete_trace: bool


@dataclass(frozen=True, slots=True)
class VerdictRow:
    case_id: str
    alert_class: str
    label: str
    # physical 1-based line in the CSV (header is line 1)
    line_number: int


@dataclass(frozen=True, slots=True)
class Case:
    case_id: str
    alert_class: str  # CSV form
    prompt_version: str | None  # None: the trace has no version
    analyst_verdict: Verdict | None  # None: unmapped or conflicting label
    agent_verdict: Verdict | None  # None: unmapped or missing label
    start_ns: int
    # ordered by start time, then span ID
    tool_calls: tuple[ToolCall, ...]
    is_incomplete_trace: bool
