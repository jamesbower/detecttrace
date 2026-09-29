"""Build cases from spans: operation matching, case roots, and tool-call ownership (PRD §7.3)."""

from collections import defaultdict
from dataclasses import dataclass, field
from typing import Literal

from detecttrace import conventions
from detecttrace.config import MappingConfig, OperationConfig
from detecttrace.model import Issue, IssueKind, Span, ToolCall, TraceCase


def build_trace_cases(
    spans: list[Span], mapping: MappingConfig
) -> tuple[list[TraceCase], list[Issue]]:
    """Group spans into cases. One case per case ID; duplicate roots are resolved and reported."""
    issues: list[Issue] = []
    by_trace: dict[str, list[Span]] = defaultdict(list)
    for span in spans:
        by_trace[span.trace_id].append(span)
    candidates: list[TraceCase] = []
    for trace_id in sorted(by_trace):
        candidates.extend(_build_trace(by_trace[trace_id], mapping, issues))
    return _pick_latest_roots(candidates, issues), issues


@dataclass
class _OpenCase:
    root: Span
    case_id: str
    tool_calls: list[ToolCall] = field(default_factory=list)
    descendants: list[Span] = field(default_factory=list)


def _span_order(span: Span) -> tuple[int, str]:
    return (span.start_ns, span.span_id)


def _build_trace(
    trace_spans: list[Span], mapping: MappingConfig, issues: list[Issue]
) -> list[TraceCase]:
    by_id = {span.span_id: span for span in trace_spans}
    children: dict[str, list[Span]] = defaultdict(list)
    tops: list[Span] = []
    for span in trace_spans:
        if span.parent_span_id is not None and span.parent_span_id in by_id:
            children[span.parent_span_id].append(span)
        else:
            tops.append(span)

    open_cases: list[_OpenCase] = []
    orphan_count = 0
    visited: set[str] = set()
    # Iterative depth-first walk: (span, case it belongs to, whether its parent chain is broken).
    stack: list[tuple[Span, _OpenCase | None, bool]] = [
        (span, None, span.parent_span_id is not None)
        for span in sorted(tops, key=_span_order, reverse=True)
    ]
    while stack:
        span, current, is_broken_chain = stack.pop()
        visited.add(span.span_id)
        subject = f"{span.trace_id}/{span.span_id}"
        kind = _classify(span, mapping.operation)
        if kind == "agent":
            case_id = _to_text(
                span.attributes.get(mapping.case_id), subject, mapping.case_id, issues
            )
            if case_id is not None and (current is None or current.case_id != case_id):
                if current is not None:
                    issues.append(
                        Issue(IssueKind.NESTED_CASE, case_id, f"inside case {current.case_id}")
                    )
                current = _OpenCase(root=span, case_id=case_id)
                open_cases.append(current)
            elif case_id is None and current is None:
                issues.append(Issue(IssueKind.AGENT_WITHOUT_CASE_ID, subject))
        elif kind == "tool":
            if current is None:
                orphan_count += 1
                orphan_kind = (
                    IssueKind.BROKEN_PARENT_CHAIN if is_broken_chain else IssueKind.ORPHAN_TOOL_SPAN
                )
                issues.append(Issue(orphan_kind, subject))
            else:
                current.tool_calls.append(_to_tool_call(span, mapping.operation, issues))
        if current is not None and span is not current.root:
            current.descendants.append(span)
        for child in sorted(children[span.span_id], key=_span_order, reverse=True):
            stack.append((child, current, is_broken_chain))

    # Spans in a parent cycle are never reached from a top span.
    for span in trace_spans:
        if span.span_id not in visited and _classify(span, mapping.operation) == "tool":
            orphan_count += 1
            issues.append(Issue(IssueKind.BROKEN_PARENT_CHAIN, f"{span.trace_id}/{span.span_id}"))

    return [_close_case(case, mapping, orphan_count > 0, issues) for case in open_cases]


def _classify(span: Span, operation: OperationConfig) -> Literal["agent", "tool"] | None:
    value = span.attributes.get(operation.attribute)
    if value is None:
        if not operation.span_name_fallback:
            return None
        value = span.name.split(" ", 1)[0]
    if value == operation.agent_value:
        return "agent"
    if value == operation.tool_value:
        return "tool"
    return None


def _to_tool_call(span: Span, operation: OperationConfig, issues: list[Issue]) -> ToolCall:
    subject = f"{span.trace_id}/{span.span_id}"
    raw_name = span.attributes.get(conventions.TOOL_NAME)
    tool_name = raw_name.strip() if isinstance(raw_name, str) else ""
    if not tool_name:
        prefix, _, suffix = span.name.partition(" ")
        if prefix == operation.tool_value:
            tool_name = suffix.strip()
    if not tool_name:
        issues.append(Issue(IssueKind.MISSING_TOOL_NAME, subject))
    raw_arguments = span.attributes.get(conventions.TOOL_CALL_ARGUMENTS)
    arguments: str | dict[str, object] | None = None
    if isinstance(raw_arguments, str | dict):
        arguments = raw_arguments
    elif raw_arguments is not None:
        issues.append(
            Issue(
                IssueKind.INVALID_ATTRIBUTE,
                subject,
                f"{conventions.TOOL_CALL_ARGUMENTS} is a {type(raw_arguments).__name__}",
            )
        )
    return ToolCall(
        span_id=span.span_id,
        tool_name=tool_name,
        arguments=arguments,
        start_ns=span.start_ns,
        end_ns=span.end_ns,
        is_failed=span.is_error or conventions.ERROR_TYPE in span.attributes,
    )


def _close_case(
    case: _OpenCase, mapping: MappingConfig, is_incomplete: bool, issues: list[Issue]
) -> TraceCase:
    root = case.root
    subject = case.case_id
    prompt_version = _read_root_or_resource(root, mapping.prompt_version, subject, issues)
    if prompt_version is None and mapping.prompt_version_lookup == "descendant":
        prompt_version = _read_descendant_version(case, mapping.prompt_version, issues)
    if is_incomplete:
        issues.append(Issue(IssueKind.INCOMPLETE_TRACE, subject, root.trace_id))
    return TraceCase(
        case_id=case.case_id,
        trace_id=root.trace_id,
        root_span_id=root.span_id,
        start_ns=root.start_ns,
        end_ns=root.end_ns,
        alert_class=_read_root_or_resource(root, mapping.alert_class, subject, issues),
        agent_label=_to_text(
            root.attributes.get(mapping.verdict), subject, mapping.verdict, issues
        ),
        prompt_version=prompt_version,
        tool_calls=tuple(sorted(case.tool_calls, key=lambda call: (call.start_ns, call.span_id))),
        is_incomplete_trace=is_incomplete,
    )


def _read_root_or_resource(root: Span, key: str, subject: str, issues: list[Issue]) -> str | None:
    value = _to_text(root.attributes.get(key), subject, key, issues)
    if value is None:
        value = _to_text(root.resource_attributes.get(key), subject, key, issues)
    return value


def _read_descendant_version(case: _OpenCase, key: str, issues: list[Issue]) -> str | None:
    values: list[str] = []
    for span in sorted(case.descendants, key=_span_order):
        value = _to_text(span.attributes.get(key), case.case_id, key, issues)
        if value is not None:
            values.append(value)
    distinct = sorted(set(values))
    if len(distinct) > 1:
        issues.append(Issue(IssueKind.VERSION_CONFLICT, case.case_id, ", ".join(distinct)))
        return None
    return values[0] if values else None


def _to_text(value: object, subject: str, key: str, issues: list[Issue]) -> str | None:
    """Convert one attribute value to text (PRD §7.3 type conversion); None means missing."""
    if value is None:
        return None
    if isinstance(value, bool):
        text = "true" if value else "false"
    elif isinstance(value, int | float | str):
        text = str(value)  # str(float) is the shortest form that round-trips
    else:
        issues.append(
            Issue(
                IssueKind.INVALID_ATTRIBUTE,
                subject,
                f"{key} is a {type(value).__name__}, not a single value",
            )
        )
        return None
    return text.strip() or None


def _pick_latest_roots(candidates: list[TraceCase], issues: list[Issue]) -> list[TraceCase]:
    return candidates  # replaced in Task 7
