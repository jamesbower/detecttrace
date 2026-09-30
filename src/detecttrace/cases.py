"""Build cases from spans: operation matching, case roots, and tool-call ownership."""

import math
from collections import defaultdict
from collections.abc import Callable, Iterator
from dataclasses import dataclass, field
from typing import Literal, TypeGuard

from detecttrace import conventions
from detecttrace.config import MappingConfig, OperationConfig
from detecttrace.model import (
    REPORT_KEY_LENGTH,
    SHORTENED_DETAIL,
    Issue,
    IssueKind,
    Span,
    ToolCall,
    TraceCase,
    to_short_label,
)

# (attribute key, first characters) of each long value already reported.
_ReportedLong = set[tuple[str, str]]


def build_trace_cases(
    spans: list[Span], mapping: MappingConfig
) -> tuple[list[TraceCase], list[Issue]]:
    """Group spans into cases. One case per case ID; duplicate roots are resolved and reported."""
    issues: list[Issue] = []
    reported_long: _ReportedLong = set()
    key = mapping.case_id

    def read_case_id(span: Span) -> str | None:
        subject = f"{span.trace_id}/{span.span_id}"
        text = _to_text(span.attributes.get(key), subject, key, issues)
        return _shorten(text, key, subject, issues, reported_long)

    candidates: list[_OpenCase] = []
    for trace_spans in _group_by_trace(spans):
        walk = _walk_trace(trace_spans, mapping, read_case_id, issues, reported_long)
        candidates.extend(walk.open_cases)
    # Closing only the kept roots keeps a dropped duplicate's issues out of the report.
    reported_resource_values: set[tuple[str, str]] = set()
    cases = [
        _close_case(case, mapping, issues, reported_resource_values, reported_long)
        for case in _pick_latest_roots(candidates, issues)
    ]
    return cases, issues


@dataclass(frozen=True, slots=True)
class CaseRoots:
    # The agent spans check opens a case at, including every copy of a duplicated case ID.
    roots: list[Span]
    # The topmost agent span of each agent subtree outside any case with no case ID in it:
    # the runs check reports as missing their case ID.
    keyless_tops: list[Span]


def find_case_roots(
    spans: list[Span],
    operation: OperationConfig,
    read_case_id: Callable[[Span], str | None],
) -> CaseRoots:
    """Find where check opens cases, with case IDs read by `read_case_id`.

    This is check's own walk: a case opens at an agent span with a case ID that no
    enclosing case on its path has, so an agent with another case ID inside a case opens a
    second one, and one with the same ID is a sub-agent. An orchestrator agent without a
    case ID above agents with one is neither a root nor a keyless top.
    """
    mapping = MappingConfig(operation=operation)
    roots: list[Span] = []
    keyless_tops: list[Span] = []
    for trace_spans in _group_by_trace(spans):
        walk = _walk_trace(trace_spans, mapping, read_case_id, [], set())
        roots.extend(case.root for case in walk.open_cases)
        keyless_tops.extend(_find_keyless_tops(trace_spans, walk))
    return CaseRoots(roots, keyless_tops)


def to_single_text(value: object) -> str | None:
    """`value` as check reads one attribute value: its text, or None when it is missing,
    blank, a number that is not finite, or not a single value."""
    if isinstance(value, bool):
        text = "true" if value else "false"
    elif isinstance(value, float) and not math.isfinite(value):
        return None
    elif isinstance(value, int | float | str):
        text = str(value)  # str(float) is the shortest form that round-trips
    else:
        return None
    return text.strip() or None


def _group_by_trace(spans: list[Span]) -> list[list[Span]]:
    by_trace: dict[str, list[Span]] = defaultdict(list)
    for span in spans:
        by_trace[span.trace_id].append(span)
    return [by_trace[trace_id] for trace_id in sorted(by_trace)]


def _find_keyless_tops(trace_spans: list[Span], walk: "_TraceWalk") -> list[Span]:
    parents = {span.span_id: span.parent_span_id for span in trace_spans}

    def ancestors(span: Span) -> Iterator[str]:
        seen: set[str] = set()
        parent = span.parent_span_id
        # A set, since a parent cycle would otherwise loop forever.
        while parent is not None and parent in parents and parent not in seen:
            seen.add(parent)
            yield parent
            parent = parents[parent]

    above_a_root = {span_id for case in walk.open_cases for span_id in ancestors(case.root)}
    candidates = [span for span in walk.keyless if span.span_id not in above_a_root]
    candidate_ids = {span.span_id for span in candidates}
    return [
        span
        for span in candidates
        if not any(span_id in candidate_ids for span_id in ancestors(span))
    ]


@dataclass(slots=True)
class _OpenCase:
    root: Span
    case_id: str
    tool_calls: list[ToolCall] = field(default_factory=list)
    descendants: list[Span] = field(default_factory=list)
    orphan_count: int = 0  # orphan tool spans in the root's trace


def _span_order(span: Span) -> tuple[int, str]:
    return (span.start_ns, span.span_id)


def _walk_trace(
    trace_spans: list[Span],
    mapping: MappingConfig,
    read_case_id: Callable[[Span], str | None],
    issues: list[Issue],
    reported_long: _ReportedLong,
) -> "_TraceWalk":
    by_id = {span.span_id: span for span in trace_spans}
    children: dict[str, list[Span]] = defaultdict(list)
    parents: dict[str, Span] = {}
    tops: list[Span] = []
    for span in trace_spans:
        if span.parent_span_id is not None and span.parent_span_id in by_id:
            children[span.parent_span_id].append(span)
            parents[span.span_id] = by_id[span.parent_span_id]
        else:
            tops.append(span)

    walk = _TraceWalk(children, mapping, read_case_id, issues, reported_long)
    for span in sorted(tops, key=_span_order):
        walk.run(span, is_broken_chain=span.parent_span_id is not None)
    if len(walk.visited) < len(by_id):
        for span in sorted(by_id.values(), key=_span_order):
            if span.span_id in walk.visited:
                continue
            start = _find_cycle_start(span, parents)
            issues.append(
                Issue(
                    IssueKind.BROKEN_PARENT_CHAIN,
                    f"{start.trace_id}/{start.span_id}",
                    "parent cycle",
                )
            )
            walk.run(start, is_broken_chain=True)

    for case in walk.open_cases:
        case.orphan_count = walk.orphan_count
    return walk


@dataclass(slots=True)
class _TraceWalk:
    """Depth-first walk of one trace that opens cases and assigns tool calls to them."""

    children: dict[str, list[Span]]
    mapping: MappingConfig
    read_case_id: Callable[[Span], str | None]  # reports an invalid value as an issue
    issues: list[Issue]
    reported_long: _ReportedLong
    open_cases: list[_OpenCase] = field(default_factory=list)
    orphan_count: int = 0
    visited: set[str] = field(default_factory=set)
    keyless: list[Span] = field(default_factory=list)  # agents without a case ID, outside cases

    def run(self, top: Span, is_broken_chain: bool) -> None:
        is_collecting_descendants = self.mapping.prompt_version_lookup == "descendant"
        # Entries: (span, case it belongs to, case IDs open on its path).
        stack: list[tuple[Span, _OpenCase | None, tuple[str, ...]]] = [(top, None, ())]
        while stack:
            span, current, path_case_ids = stack.pop()
            # A parent cycle leads back to a visited span; stopping there ends the walk.
            if span.span_id in self.visited:
                continue
            self.visited.add(span.span_id)
            kind = classify_span(span, self.mapping.operation)
            if kind == "agent":
                current, path_case_ids = self._visit_agent(span, current, path_case_ids)
            elif kind == "tool":
                self._visit_tool(span, current, is_broken_chain)
            if is_collecting_descendants and current is not None and span is not current.root:
                current.descendants.append(span)
            for child in sorted(self.children.get(span.span_id, ()), key=_span_order, reverse=True):
                stack.append((child, current, path_case_ids))

    def _visit_agent(
        self, span: Span, current: _OpenCase | None, path_case_ids: tuple[str, ...]
    ) -> tuple[_OpenCase | None, tuple[str, ...]]:
        # read_case_id returns None for missing and invalid alike; only invalid adds an issue.
        issue_count = len(self.issues)
        case_id = self.read_case_id(span)
        if case_id is None:
            if current is None:
                self.keyless.append(span)
                # A present but invalid case ID is already reported as INVALID_ATTRIBUTE.
                if len(self.issues) == issue_count:
                    subject = f"{span.trace_id}/{span.span_id}"
                    self.issues.append(Issue(IssueKind.AGENT_WITHOUT_CASE_ID, subject))
            return current, path_case_ids
        if case_id in path_case_ids:
            # The innermost case owns its whole subtree, so a reappearing outer ID is a sub-agent.
            if current is not None and current.case_id != case_id:
                self.issues.append(
                    Issue(
                        IssueKind.NESTED_CASE,
                        case_id,
                        f"case ID {case_id} reappears inside case {current.case_id}; "
                        f"treated as a sub-agent of {current.case_id}",
                    )
                )
            return current, path_case_ids
        if current is not None:
            self.issues.append(
                Issue(IssueKind.NESTED_CASE, case_id, f"inside case {current.case_id}")
            )
        opened = _OpenCase(root=span, case_id=case_id)
        self.open_cases.append(opened)
        return opened, (*path_case_ids, case_id)

    def _visit_tool(self, span: Span, current: _OpenCase | None, is_broken_chain: bool) -> None:
        if current is not None:
            current.tool_calls.append(
                _to_tool_call(span, self.mapping, self.issues, self.reported_long)
            )
            return
        self.orphan_count += 1
        kind = IssueKind.BROKEN_PARENT_CHAIN if is_broken_chain else IssueKind.ORPHAN_TOOL_SPAN
        self.issues.append(Issue(kind, f"{span.trace_id}/{span.span_id}"))


def _find_cycle_start(span: Span, parents: dict[str, Span]) -> Span:
    # A span never reached from a top span climbs into a parent cycle; start at its earliest
    # member so spans hanging below the cycle are walked as its children.
    path: dict[str, Span] = {}
    while span.span_id not in path:
        path[span.span_id] = span
        span = parents[span.span_id]
    cycle = list(path.values())[list(path).index(span.span_id) :]
    return min(cycle, key=_span_order)


def classify_span(span: Span, operation: OperationConfig) -> Literal["agent", "tool"] | None:
    """Whether `span` is an agent run, a tool call, or neither under the operation rule."""
    value = span.attributes.get(operation.attribute)
    if not isinstance(value, str) or not value.strip():
        if not operation.span_name_fallback:
            return None
        value = span.name.split(" ", 1)[0]
    if value == operation.agent_value:
        return "agent"
    if value == operation.tool_value:
        return "tool"
    return None


def is_tool_name(value: object) -> TypeGuard[str]:
    """Whether a tool name attribute holds a name; otherwise the span name is the fallback."""
    return isinstance(value, str) and bool(value.strip())


def is_tool_arguments(value: object) -> TypeGuard[str | dict[str, object]]:
    """Whether a tool arguments attribute holds arguments that checklist rules can read."""
    return isinstance(value, str | dict)


def _to_tool_call(
    span: Span, mapping: MappingConfig, issues: list[Issue], reported_long: _ReportedLong
) -> ToolCall:
    subject = f"{span.trace_id}/{span.span_id}"
    raw_name = span.attributes.get(mapping.tool_name)
    tool_name = raw_name.strip() if is_tool_name(raw_name) else ""
    if raw_name is not None and not isinstance(raw_name, str):
        issues.append(
            Issue(
                IssueKind.INVALID_ATTRIBUTE,
                subject,
                f"{mapping.tool_name} is a {type(raw_name).__name__}",
            )
        )
    if not tool_name:
        prefix, _, suffix = span.name.partition(" ")
        if prefix == mapping.operation.tool_value:
            tool_name = suffix.strip()
    if not tool_name:
        issues.append(Issue(IssueKind.MISSING_TOOL_NAME, subject))
    raw_arguments = span.attributes.get(mapping.tool_arguments)
    arguments: str | dict[str, object] | None = None
    if is_tool_arguments(raw_arguments):
        arguments = raw_arguments
    elif raw_arguments is not None:
        issues.append(
            Issue(
                IssueKind.INVALID_ATTRIBUTE,
                subject,
                f"{mapping.tool_arguments} is a {type(raw_arguments).__name__}",
            )
        )
    return ToolCall(
        span_id=span.span_id,
        tool_name=_shorten(tool_name, mapping.tool_name, subject, issues, reported_long) or "",
        arguments=arguments,
        start_ns=span.start_ns,
        end_ns=span.end_ns,
        is_failed=span.is_error or conventions.ERROR_TYPE in span.attributes,
    )


def _close_case(
    case: _OpenCase,
    mapping: MappingConfig,
    issues: list[Issue],
    reported_resource_values: set[tuple[str, str]],
    reported_long: _ReportedLong,
) -> TraceCase:
    root = case.root
    subject = case.case_id
    prompt_version = _read_root_or_resource(
        root, mapping.prompt_version, subject, issues, reported_resource_values, reported_long
    )
    if prompt_version is None and mapping.prompt_version_lookup == "descendant":
        prompt_version = _read_descendant_version(
            case, mapping.prompt_version, issues, reported_long
        )
    is_incomplete = case.orphan_count > 0
    if is_incomplete:
        issues.append(
            Issue(
                IssueKind.INCOMPLETE_TRACE,
                subject,
                f"{root.trace_id}: {case.orphan_count} orphan tool span(s)",
            )
        )
    return TraceCase(
        case_id=case.case_id,
        trace_id=root.trace_id,
        root_span_id=root.span_id,
        start_ns=root.start_ns,
        end_ns=root.end_ns,
        alert_class=_read_root_or_resource(
            root, mapping.alert_class, subject, issues, reported_resource_values, reported_long
        ),
        agent_label=_shorten(
            _to_text(root.attributes.get(mapping.verdict), subject, mapping.verdict, issues),
            mapping.verdict,
            subject,
            issues,
            reported_long,
        ),
        prompt_version=prompt_version,
        tool_calls=tuple(sorted(case.tool_calls, key=lambda call: (call.start_ns, call.span_id))),
        is_incomplete_trace=is_incomplete,
    )


def _read_root_or_resource(
    root: Span,
    key: str,
    subject: str,
    issues: list[Issue],
    reported_resource_values: set[tuple[str, str]],
    reported_long: _ReportedLong,
) -> str | None:
    value = _to_text(root.attributes.get(key), subject, key, issues)
    if value is not None:
        return _shorten(value, key, subject, issues, reported_long)
    raw = root.resource_attributes.get(key)
    found: list[Issue] = []
    value = _to_text(raw, "resource", key, found)
    # Every span of a process shares its resource, so a bad value is reported once, not per case.
    if found and (key, repr(raw)) not in reported_resource_values:
        reported_resource_values.add((key, repr(raw)))
        issues.extend(
            Issue(issue.kind, issue.subject, f"{issue.detail}; first seen in trace {root.trace_id}")
            for issue in found
        )
    return _shorten(value, key, subject, issues, reported_long)


def _read_descendant_version(
    case: _OpenCase, key: str, issues: list[Issue], reported_long: _ReportedLong
) -> str | None:
    values: list[str] = []
    for span in sorted(case.descendants, key=_span_order):
        value = _shorten(
            _to_text(
                span.attributes.get(key), case.case_id, f"{key} on span {span.span_id}", issues
            ),
            key,
            case.case_id,
            issues,
            reported_long,
        )
        if value is not None:
            values.append(value)
    distinct = sorted(set(values))
    if len(distinct) > 1:
        issues.append(Issue(IssueKind.VERSION_CONFLICT, case.case_id, ", ".join(distinct)))
        return None
    return values[0] if values else None


def _to_text(value: object, subject: str, key: str, issues: list[Issue]) -> str | None:
    """to_single_text, reporting a value that is present but not readable; None means missing."""
    text = to_single_text(value)
    if text is None and value is not None and not isinstance(value, str):
        detail = (
            "is not a finite number"
            if isinstance(value, float)
            else f"is a {type(value).__name__}, not a single value"
        )
        issues.append(Issue(IssueKind.INVALID_ATTRIBUTE, subject, f"{key} {detail}"))
    return text


def _shorten(
    text: str | None, key: str, subject: str, issues: list[Issue], reported: _ReportedLong
) -> str | None:
    """`text` cut to the label limit, reported once per key and start; None stays None."""
    if text is None:
        return None
    short = to_short_label(text)
    if short is not text and (key, text[:REPORT_KEY_LENGTH]) not in reported:
        reported.add((key, text[:REPORT_KEY_LENGTH]))
        issues.append(Issue(IssueKind.INVALID_ATTRIBUTE, subject, f"{key} is {SHORTENED_DETAIL}"))
    return short


def _pick_latest_roots(candidates: list[_OpenCase], issues: list[Issue]) -> list[_OpenCase]:
    """Keep one root per case ID: latest start, then latest end, highest trace ID, highest span ID."""
    by_case: dict[str, list[_OpenCase]] = defaultdict(list)
    for candidate in candidates:
        by_case[candidate.case_id].append(candidate)
    chosen: list[_OpenCase] = []
    for case_id in sorted(by_case):
        group = by_case[case_id]
        latest = max(
            group, key=lambda c: (c.root.start_ns, c.root.end_ns, c.root.trace_id, c.root.span_id)
        )
        for other in group:
            if other is not latest:
                issues.append(
                    Issue(
                        IssueKind.DUPLICATE_ROOT,
                        case_id,
                        f"kept {latest.root.trace_id}/{latest.root.span_id}, "
                        f"ignored {other.root.trace_id}/{other.root.span_id}",
                    )
                )
        chosen.append(latest)
    return chosen
