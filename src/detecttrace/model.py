"""Internal records shared by the loader, the case builder, and the join."""

from dataclasses import dataclass
from enum import StrEnum

UNKNOWN_VERSION = "unknown"


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
    DUPLICATE_VERDICT = "duplicate_verdict"
    CONFLICTING_ANALYST_VERDICT = "conflicting_analyst_verdict"
    ROOT_WITHOUT_VERDICT = "root_without_verdict"
    VERDICT_WITHOUT_ROOT = "verdict_without_root"
    ALERT_CLASS_CONFLICT = "alert_class_conflict"
    UNMAPPED_ANALYST_LABEL = "unmapped_analyst_label"
    UNMAPPED_AGENT_LABEL = "unmapped_agent_label"
    MISSING_AGENT_VERDICT = "missing_agent_verdict"


@dataclass(frozen=True, slots=True)
class Issue:
    kind: IssueKind
    subject: str  # the file, span, or case ID the issue is about
    detail: str = ""


@dataclass(frozen=True, slots=True)
class Span:
    trace_id: str  # lowercase hex
    span_id: str  # lowercase hex
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
    arguments: (
        str | dict[str, object] | None
    )  # JSON string as sent, or a map in its original key order
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
    tool_calls: tuple[ToolCall, ...]
    is_incomplete_trace: bool


@dataclass(frozen=True, slots=True)
class VerdictRow:
    case_id: str
    alert_class: str
    label: str
    line_number: int


@dataclass(frozen=True, slots=True)
class Case:
    case_id: str
    alert_class: str  # CSV form
    prompt_version: str  # UNKNOWN_VERSION when the trace has none
    analyst_verdict: Verdict | None  # None: unmapped or conflicting label
    agent_verdict: Verdict | None  # None: unmapped or missing label
    start_ns: int
    tool_calls: tuple[ToolCall, ...]
    is_incomplete_trace: bool
