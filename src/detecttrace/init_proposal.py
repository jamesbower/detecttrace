"""Propose a configuration for `init` from already-loaded traces and verdict rows.

Nothing here reads files or prints: the command loads the inputs, calls propose_init, and
decides what to show, ask and write.
"""

import math
from collections import Counter, defaultdict
from collections.abc import Callable, Iterable
from dataclasses import dataclass

from detecttrace import conventions
from detecttrace.cases import build_trace_cases, classify_span, is_tool_arguments, is_tool_name
from detecttrace.config import MappingConfig, OperationConfig, normalize_label
from detecttrace.model import MAX_LABEL_LENGTH, Span, TraceCase, Verdict, VerdictRow
from detecttrace.runconfig import TraceFormat

SOURCE_DETECTTRACE = "detecttrace attribute"
SOURCE_LANGFUSE_PROMPT = "Langfuse prompt version"
SOURCE_SUFFIX = "suffix match"
SOURCE_DEFAULT = "default"
SOURCE_NOT_FOUND = "not found"
SOURCE_OPERATION_NAME = conventions.OPERATION_ATTRIBUTE
SOURCE_SPAN_NAMES = "span names"
SOURCE_OPENINFERENCE = "OpenInference"
SOURCE_SET = "set by you"

REQUIRED_LABELS = "verdict labels"

# Only spellings that mean the same verdict wherever they appear are mapped without asking:
# a wrong guess would silently corrupt the ground truth every metric is scored against, so
# common but ambiguous labels such as "malicious" are left for the user. Keys are normalized;
# normalization does not split camel case, so GUIDE-style "TruePositive" needs its own entry.
AUTO_LABEL_MAP: dict[str, Verdict] = {
    "true_positive": Verdict.TRUE_POSITIVE,
    "true positive": Verdict.TRUE_POSITIVE,
    "truepositive": Verdict.TRUE_POSITIVE,
    "tp": Verdict.TRUE_POSITIVE,
    "false_positive": Verdict.FALSE_POSITIVE,
    "false positive": Verdict.FALSE_POSITIVE,
    "falsepositive": Verdict.FALSE_POSITIVE,
    "fp": Verdict.FALSE_POSITIVE,
    "benign": Verdict.BENIGN,
    "benign_positive": Verdict.BENIGN,
    "benign positive": Verdict.BENIGN,
    "benignpositive": Verdict.BENIGN,
}

# A user answers one prompt per unmapped label; past this many the rest is left to check,
# which reports every unmapped label.
MAX_LISTED_LABELS = 100
MAX_LISTED_TOOLS = 200
MAX_ORPHAN_EXAMPLES = 3

_OPENINFERENCE_KIND = "openinference.span.kind"
_OPENINFERENCE_OPERATION = OperationConfig(
    attribute=_OPENINFERENCE_KIND, agent_value="AGENT", tool_value="TOOL"
)
_OPENINFERENCE_TOOL_NAME = "tool.name"
_OPENINFERENCE_TOOL_ARGUMENTS = "input.value"
_LANGFUSE_PROMPT_VERSION = "langfuse.prompt_version"
_DEFAULTS = MappingConfig()
_SUFFIXES = {
    "case_id": ("case_id", "case.id"),
    "alert_class": ("alert_class", "alert.class"),
    "verdict": ("verdict",),
    "prompt_version": ("prompt_version", "prompt.version"),
}
# check reads these two from the agent run's own attributes only; the others fall back to
# the resource, so a key found only there is still readable.
_ROOT_ONLY_FIELDS = ("case_id", "verdict")
# (trace ID, span ID)
_SpanKey = tuple[str, str]


@dataclass(frozen=True, slots=True)
class FieldProposal:
    value: str | None  # the attribute key; None when nothing qualified
    source: str
    covered: int  # agent runs (tool calls for the tool fields) carrying the key
    total: int


@dataclass(frozen=True, slots=True)
class OperationProposal:
    attribute: str
    agent_value: str
    tool_value: str
    span_name_fallback: bool
    source: str


@dataclass(frozen=True, slots=True)
class MappingProposal:
    case_id: FieldProposal
    alert_class: FieldProposal
    verdict: FieldProposal
    prompt_version: FieldProposal
    tool_name: FieldProposal
    tool_arguments: FieldProposal
    operation: OperationProposal

    def to_mapping_config(self) -> MappingConfig:
        """The mapping as configuration; a field with no proposal keeps its default key."""
        return MappingConfig(
            case_id=self.case_id.value or _DEFAULTS.case_id,
            alert_class=self.alert_class.value or _DEFAULTS.alert_class,
            verdict=self.verdict.value or _DEFAULTS.verdict,
            prompt_version=self.prompt_version.value or _DEFAULTS.prompt_version,
            tool_name=self.tool_name.value or _DEFAULTS.tool_name,
            tool_arguments=self.tool_arguments.value or _DEFAULTS.tool_arguments,
            operation=OperationConfig(
                attribute=self.operation.attribute,
                agent_value=self.operation.agent_value,
                tool_value=self.operation.tool_value,
                span_name_fallback=self.operation.span_name_fallback,
            ),
        )


@dataclass(frozen=True, slots=True)
class OrphanSummary:
    count: int
    examples: tuple[str, ...]  # the first case IDs in sorted order


@dataclass(frozen=True, slots=True)
class Proposal:
    trace_format: TraceFormat
    agent_run_count: int
    mapping: MappingProposal
    label_map: dict[str, Verdict]  # analyst labels in a fixed spelling, keyed as written
    # Agent labels in a fixed spelling that match no analyst label. An agent label equal to an
    # analyst label after normalization is left out: check falls back to label_map for it.
    agent_label_map: dict[str, Verdict]
    unmapped_analyst_labels: tuple[str, ...]
    # Agent labels that match no analyst label and no fixed spelling.
    unmapped_agent_labels: tuple[str, ...]
    trace_cases: list[TraceCase]  # the cases check would build through this mapping
    tool_names_by_class: dict[str, tuple[str, ...]]
    case_counts_by_class: dict[str, int]
    traces_without_verdict: OrphanSummary
    verdicts_without_trace: OrphanSummary
    missing_required: tuple[str, ...]  # "case_id", "verdict", REQUIRED_LABELS
    notes: tuple[str, ...]


def propose_init(
    spans: list[Span],
    trace_format: TraceFormat,
    verdict_rows: list[VerdictRow],
    *,
    mapping: MappingConfig | None = None,
) -> Proposal:
    """Propose the mapping, label maps and checklist inputs for `init`.

    Each of case ID, alert class, agent verdict and prompt version takes the first qualifying
    key of: the detecttrace.* name; for Langfuse input, langfuse.prompt_version (prompt
    version only); a key ending in a known name after a dot. A key qualifies when it is on at
    least half of the agent runs; among suffix matches the most common wins, then the
    shortest, then the first in code-point order. The result is the same for the same input.

    With `mapping` (the user's edits), each key that differs from the one detected is
    proposed as given, with source SOURCE_SET and its coverage, and everything else (agent
    runs, cases, labels, tool names, orphans) is read through `mapping`.
    """
    notes: list[str] = []
    operation_notes: list[str] = []
    operation = _propose_operation(spans, operation_notes)
    if mapping is not None and _to_operation_config(operation) != mapping.operation:
        given = mapping.operation
        operation = OperationProposal(
            attribute=given.attribute,
            agent_value=given.agent_value,
            tool_value=given.tool_value,
            span_name_fallback=given.span_name_fallback,
            source=SOURCE_SET,
        )
    else:
        notes += operation_notes
    operation_config = _to_operation_config(operation)
    agent_runs = _find_agent_runs(
        spans, operation_config, None if mapping is None else mapping.case_id
    )
    tool_calls = [span for span in spans if classify_span(span, operation_config) == "tool"]
    fields = {
        name: _propose_or_measure_field(name, agent_runs, trace_format, mapping, notes)
        for name in _SUFFIXES
    }
    is_openinference = operation.source == SOURCE_OPENINFERENCE
    tool_source = SOURCE_OPENINFERENCE if is_openinference else SOURCE_DEFAULT
    tool_name = _OPENINFERENCE_TOOL_NAME if is_openinference else _DEFAULTS.tool_name
    tool_arguments = _OPENINFERENCE_TOOL_ARGUMENTS if is_openinference else _DEFAULTS.tool_arguments
    proposed = MappingProposal(
        case_id=fields["case_id"],
        alert_class=fields["alert_class"],
        verdict=fields["verdict"],
        prompt_version=fields["prompt_version"],
        tool_name=_propose_tool_field(
            *_choose_key(tool_name, tool_source, None if mapping is None else mapping.tool_name),
            is_tool_name,
            tool_calls,
        ),
        tool_arguments=_propose_tool_field(
            *_choose_key(
                tool_arguments,
                tool_source,
                None if mapping is None else mapping.tool_arguments,
            ),
            is_tool_arguments,
            tool_calls,
        ),
        operation=operation,
    )
    if proposed.alert_class.value is None:
        notes.append(
            "no alert class found on agent runs; alert classes come from the verdict file only"
        )
    if proposed.prompt_version.value is None:
        notes.append("no prompt version found; every case will show as (no version)")

    trace_cases: list[TraceCase] = []
    if proposed.case_id.value is not None:
        # The given mapping, not the proposal's, so settings the proposal omits still apply.
        trace_cases, _ = build_trace_cases(
            spans, proposed.to_mapping_config() if mapping is None else mapping
        )

    analyst_labels = _pick_spellings(row.label for row in verdict_rows)
    label_map, unmapped_analyst = _auto_map(analyst_labels)
    agent_only = _pick_spellings(
        label
        for label in (case.agent_label for case in trace_cases)
        if label is not None and normalize_label(label) not in analyst_labels
    )
    agent_label_map, unmapped_agent = _auto_map(agent_only)
    missing = [name for name in _ROOT_ONLY_FIELDS if getattr(proposed, name).value is None]
    if not label_map:
        missing.append(REQUIRED_LABELS)

    tool_names_by_class, case_counts_by_class = _group_by_class(trace_cases, verdict_rows)
    trace_ids = {case.case_id for case in trace_cases}
    verdict_ids = {row.case_id for row in verdict_rows}
    return Proposal(
        trace_format=trace_format,
        agent_run_count=len(agent_runs),
        mapping=proposed,
        label_map=label_map,
        agent_label_map=agent_label_map,
        unmapped_analyst_labels=_cap(unmapped_analyst, "analyst", notes),
        unmapped_agent_labels=_cap(unmapped_agent, "agent", notes),
        trace_cases=trace_cases,
        tool_names_by_class=tool_names_by_class,
        case_counts_by_class=case_counts_by_class,
        traces_without_verdict=_summarize_orphans(trace_ids - verdict_ids),
        verdicts_without_trace=_summarize_orphans(verdict_ids - trace_ids),
        missing_required=tuple(missing),
        notes=tuple(notes),
    )


def _to_operation_config(operation: OperationProposal) -> OperationConfig:
    return OperationConfig(
        attribute=operation.attribute,
        agent_value=operation.agent_value,
        tool_value=operation.tool_value,
        span_name_fallback=operation.span_name_fallback,
    )


def _choose_key(detected: str, source: str, given: str | None) -> tuple[str, str]:
    if given is None or given == detected:
        return detected, source
    return given, SOURCE_SET


def _propose_operation(spans: list[Span], notes: list[str]) -> OperationProposal:
    default = _DEFAULTS.operation
    # A rule is proposed only when check would find agent runs under it: GenAI spans such as
    # `chat` carry the operation attribute without making it the rule for agent runs.
    agents = [span for span in spans if classify_span(span, default) == "agent"]
    if any(span.attributes.get(default.attribute) == default.agent_value for span in agents):
        source = SOURCE_OPERATION_NAME
    elif agents:
        source = SOURCE_SPAN_NAMES
    elif any(classify_span(span, _OPENINFERENCE_OPERATION) == "agent" for span in spans):
        return OperationProposal(
            attribute=_OPENINFERENCE_OPERATION.attribute,
            agent_value=_OPENINFERENCE_OPERATION.agent_value,
            tool_value=_OPENINFERENCE_OPERATION.tool_value,
            span_name_fallback=_OPENINFERENCE_OPERATION.span_name_fallback,
            source=SOURCE_OPENINFERENCE,
        )
    else:
        source = SOURCE_DEFAULT
        notes.append(
            f"no agent runs found: no span has {default.attribute} {default.agent_value}, "
            f"an '{default.agent_value} ...' name, or {_OPENINFERENCE_KIND} "
            f"{_OPENINFERENCE_OPERATION.agent_value}"
        )
    return OperationProposal(
        attribute=default.attribute,
        agent_value=default.agent_value,
        tool_value=default.tool_value,
        span_name_fallback=default.span_name_fallback,
        source=source,
    )


def _find_agent_runs(
    spans: list[Span], operation: OperationConfig, case_id_key: str | None
) -> list[Span]:
    """The agent spans check would open cases at: with `case_id_key`, as check would read it;
    without, whichever candidate key the case ID is read from.

    check opens a case at the first agent span on a path that has a case ID, so an
    orchestrator agent above agents with case IDs is not a run of its own. An agent subtree
    in which no agent has a candidate case ID is one run, at its topmost agent span: the run
    check would report as missing its case ID.
    """
    by_key = {(span.trace_id, span.span_id): span for span in spans}
    is_agent = {key: classify_span(span, operation) == "agent" for key, span in by_key.items()}
    is_keyed = {
        key: is_agent[key] and _has_case_id_candidate(by_key[key], case_id_key) for key in by_key
    }
    has_keyed_below: set[_SpanKey] = set()
    for key, keyed in is_keyed.items():
        parent = _parent_key(by_key[key])
        # has_keyed_below holds every ancestor of a marked span, so a marked parent ends the
        # walk; this also stops a parent cycle.
        while keyed and parent in by_key and parent not in has_keyed_below:
            has_keyed_below.add(parent)
            parent = _parent_key(by_key[parent])
    is_keyless_top = {
        key: is_agent[key] and not is_keyed[key] and key not in has_keyed_below for key in by_key
    }
    has_keyed_above = _create_ancestor_check(by_key, is_keyed)
    has_keyless_top_above = _create_ancestor_check(by_key, is_keyless_top)

    runs: list[Span] = []
    for key, span in by_key.items():
        parent = _parent_key(span)
        is_run_start = is_keyed[key] or (is_keyless_top[key] and not has_keyless_top_above(parent))
        if is_run_start and not has_keyed_above(parent):
            runs.append(span)
    return runs


def _parent_key(span: Span) -> _SpanKey | None:
    parent = span.parent_span_id
    return None if parent is None else (span.trace_id, parent)


def _create_ancestor_check(
    by_key: dict[_SpanKey, Span], flags: dict[_SpanKey, bool]
) -> Callable[[_SpanKey | None], bool]:
    """Return whether a span or one of its ancestors has its flag set."""
    memo: dict[_SpanKey, bool] = {}

    def check(key: _SpanKey | None) -> bool:
        path: list[_SpanKey] = []
        on_path: set[_SpanKey] = set()
        result = False
        # Iterative, with a set for parent cycles: a deep or looping chain must not recurse.
        while key is not None and key in by_key and key not in on_path:
            if key in memo:
                result = memo[key]
                break
            path.append(key)
            on_path.add(key)
            if flags[key]:
                result = True
                break
            key = _parent_key(by_key[key])
        for visited in path:
            memo[visited] = result
        return result

    return check


def _has_case_id_candidate(span: Span, case_id_key: str | None) -> bool:
    if case_id_key is not None:
        return _has_value(span, case_id_key, is_root_only=True)
    if _has_value(span, _DEFAULTS.case_id, is_root_only=True):
        return True
    suffixes = _SUFFIXES["case_id"]
    return any(
        _is_suffix_key(key, suffixes) and _is_text(value) for key, value in span.attributes.items()
    )


def _propose_or_measure_field(
    name: str,
    agent_runs: list[Span],
    trace_format: TraceFormat,
    mapping: MappingConfig | None,
    notes: list[str],
) -> FieldProposal:
    field_notes: list[str] = []
    field = _propose_field(name, agent_runs, trace_format, field_notes)
    given = None if mapping is None else getattr(mapping, name)
    # A field not found is written with its default key, so that key given back is no change.
    if given is None or given == (field.value or getattr(_DEFAULTS, name)):
        notes += field_notes
        return field
    is_root_only = name in _ROOT_ONLY_FIELDS
    covered = sum(1 for run in agent_runs if _has_value(run, given, is_root_only))
    return FieldProposal(given, SOURCE_SET, covered, len(agent_runs))


def _propose_field(
    name: str, agent_runs: list[Span], trace_format: TraceFormat, notes: list[str]
) -> FieldProposal:
    total = len(agent_runs)
    is_root_only = name in _ROOT_ONLY_FIELDS
    detecttrace_key = getattr(_DEFAULTS, name)
    fixed = [(detecttrace_key, SOURCE_DETECTTRACE)]
    if name == "prompt_version" and trace_format == "langfuse":
        fixed.append((_LANGFUSE_PROMPT_VERSION, SOURCE_LANGFUSE_PROMPT))
    below_bar: list[tuple[str, int]] = []
    for key, source in fixed:
        covered = sum(1 for run in agent_runs if _has_value(run, key, is_root_only))
        if _is_enough(covered, total):
            return FieldProposal(key, source, covered, total)
        if covered:
            below_bar.append((key, covered))

    counts = _count_suffix_matches(agent_runs, _SUFFIXES[name], is_root_only)
    ranked = sorted(counts.items(), key=lambda item: (-item[1], len(item[0]), item[0]))
    if ranked and _is_enough(ranked[0][1], total):
        key, covered = ranked[0]
        if len(ranked) > 1:
            runner_up, runner_up_count = ranked[1]
            notes.append(
                f"{name}: also found {runner_up} on {runner_up_count:,} of {total:,} agent runs"
            )
        return FieldProposal(key, SOURCE_SUFFIX, covered, total)
    below_bar.extend(ranked[:1])
    if below_bar:
        key, covered = max(below_bar, key=lambda item: (item[1], -len(item[0])))
        notes.append(
            f"{name}: {key} is on only {covered:,} of {total:,} agent runs; "
            "not proposed, since it must be on at least half"
        )
    return FieldProposal(None, SOURCE_NOT_FOUND, 0, total)


def _count_suffix_matches(
    agent_runs: list[Span], suffixes: tuple[str, ...], is_root_only: bool
) -> Counter[str]:
    counts: Counter[str] = Counter()
    # Keys already judged, so thousands of distinct or huge keys cost one check each.
    usable: dict[str, bool] = {}
    for run in agent_runs:
        found: set[str] = set()
        sources = [run.attributes] if is_root_only else [run.attributes, run.resource_attributes]
        for attributes in sources:
            for key, value in attributes.items():
                if key not in usable:
                    usable[key] = _is_suffix_key(key, suffixes)
                if usable[key] and _is_text(value):
                    found.add(key)
        counts.update(found)
    return counts


def _is_suffix_key(key: str, suffixes: tuple[str, ...]) -> bool:
    is_match = key in suffixes or key.endswith(tuple("." + suffix for suffix in suffixes))
    return is_match and _is_writable_key(key)


def _is_writable_key(key: str) -> bool:
    # A key a user could not read back or type is never proposed, however common.
    return len(key) <= MAX_LABEL_LENGTH and key.isprintable()


def _has_value(run: Span, key: str, is_root_only: bool) -> bool:
    # As in check, an unreadable value on the span falls through to the resource.
    if _is_text(run.attributes.get(key)):
        return True
    return not is_root_only and _is_text(run.resource_attributes.get(key))


def _is_text(value: object) -> bool:
    """Whether check reads `value` as a single non-empty value."""
    if isinstance(value, float) and not math.isfinite(value):
        return False
    if isinstance(value, bool | int | float):
        return True
    return isinstance(value, str) and bool(value.strip())


def _is_enough(covered: int, total: int) -> bool:
    return covered > 0 and covered * 2 >= total


def _propose_tool_field(
    key: str,
    source: str,
    is_readable: Callable[[object], bool],
    tool_calls: list[Span],
) -> FieldProposal:
    covered = sum(1 for span in tool_calls if is_readable(span.attributes.get(key)))
    return FieldProposal(key, source, covered, len(tool_calls))


def _pick_spellings(labels: Iterable[str]) -> dict[str, str]:
    """One spelling per normalized label: the first in code-point order, whatever the row order."""
    spellings: dict[str, str] = {}
    for label in labels:
        key = normalize_label(label)
        if key and (key not in spellings or label < spellings[key]):
            spellings[key] = label
    return spellings


def _auto_map(spellings: dict[str, str]) -> tuple[dict[str, Verdict], list[str]]:
    mapped: dict[str, Verdict] = {}
    unmapped: list[str] = []
    for key, label in sorted(spellings.items(), key=lambda item: item[1]):
        if key in AUTO_LABEL_MAP:
            mapped[label] = AUTO_LABEL_MAP[key]
        else:
            unmapped.append(label)
    return mapped, unmapped


def _cap(labels: list[str], side: str, notes: list[str]) -> tuple[str, ...]:
    if len(labels) > MAX_LISTED_LABELS:
        notes.append(
            f"{len(labels) - MAX_LISTED_LABELS:,} more unmapped {side} labels are not listed; "
            "check reports every one"
        )
    return tuple(labels[:MAX_LISTED_LABELS])


def _group_by_class(
    trace_cases: list[TraceCase], verdict_rows: list[VerdictRow]
) -> tuple[dict[str, tuple[str, ...]], dict[str, int]]:
    # As in check: a case without a verdict row is dropped (ROOT_WITHOUT_VERDICT), a case's
    # class is its first verdict row's, and classes group by normalized name, shown in the
    # spelling of the first case in case-ID order.
    class_by_case: dict[str, str] = {}
    for row in verdict_rows:
        class_by_case.setdefault(row.case_id, row.alert_class)
    spellings: dict[str, str] = {}
    tools: dict[str, set[str]] = defaultdict(set)
    counts: Counter[str] = Counter()
    for case in sorted(trace_cases, key=lambda case: case.case_id):
        alert_class = class_by_case.get(case.case_id)
        if alert_class is None:
            continue
        key = normalize_label(alert_class)
        spellings.setdefault(key, alert_class)
        counts[key] += 1
        tools[key].update(call.tool_name for call in case.tool_calls if call.tool_name)
    return (
        {spellings[key]: tuple(sorted(tools[key])[:MAX_LISTED_TOOLS]) for key in sorted(counts)},
        {spellings[key]: counts[key] for key in sorted(counts)},
    )


def _summarize_orphans(case_ids: set[str]) -> OrphanSummary:
    return OrphanSummary(len(case_ids), tuple(sorted(case_ids)[:MAX_ORPHAN_EXAMPLES]))
