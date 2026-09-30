"""Propose a configuration for `init` from already-loaded traces and verdict rows.

Nothing here reads files or prints: the command loads the inputs, calls propose_init, and
decides what to show, ask and write.
"""

import math
from collections import Counter, defaultdict
from collections.abc import Iterable
from dataclasses import dataclass

from detecttrace import conventions
from detecttrace.cases import build_trace_cases, classify_span
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
    tool_names_by_class: dict[str, tuple[str, ...]]
    case_counts_by_class: dict[str, int]
    traces_without_verdict: OrphanSummary
    verdicts_without_trace: OrphanSummary
    missing_required: tuple[str, ...]  # "case_id", "verdict", REQUIRED_LABELS
    notes: tuple[str, ...]


def propose_init(
    spans: list[Span], trace_format: TraceFormat, verdict_rows: list[VerdictRow]
) -> Proposal:
    """Propose the mapping, label maps and checklist inputs for `init`.

    Each of case ID, alert class, agent verdict and prompt version takes the first qualifying
    key of: the detecttrace.* name; for Langfuse input, langfuse.prompt_version (prompt
    version only); a key ending in a known name after a dot. A key qualifies when it is on at
    least half of the agent runs; among suffix matches the most common wins, then the
    shortest, then the first in code-point order. The result is the same for the same input.
    """
    notes: list[str] = []
    operation = _propose_operation(spans, notes)
    operation_config = OperationConfig(
        attribute=operation.attribute,
        agent_value=operation.agent_value,
        tool_value=operation.tool_value,
        span_name_fallback=operation.span_name_fallback,
    )
    agent_runs = _find_agent_runs(spans, operation_config)
    tool_calls = [span for span in spans if classify_span(span, operation_config) == "tool"]
    fields = {name: _propose_field(name, agent_runs, trace_format, notes) for name in _SUFFIXES}
    is_openinference = operation.source == SOURCE_OPENINFERENCE
    mapping = MappingProposal(
        case_id=fields["case_id"],
        alert_class=fields["alert_class"],
        verdict=fields["verdict"],
        prompt_version=fields["prompt_version"],
        tool_name=_propose_tool_field(
            _OPENINFERENCE_TOOL_NAME if is_openinference else _DEFAULTS.tool_name,
            is_openinference,
            tool_calls,
        ),
        tool_arguments=_propose_tool_field(
            _OPENINFERENCE_TOOL_ARGUMENTS if is_openinference else _DEFAULTS.tool_arguments,
            is_openinference,
            tool_calls,
        ),
        operation=operation,
    )
    if mapping.alert_class.value is None:
        notes.append(
            "no alert class found on agent runs; alert classes come from the verdict file only"
        )
    if mapping.prompt_version.value is None:
        notes.append("no prompt version found; every case will show as (no version)")

    trace_cases: list[TraceCase] = []
    if mapping.case_id.value is not None:
        trace_cases, _ = build_trace_cases(spans, mapping.to_mapping_config())
    if trace_format == "langfuse" and trace_cases and not any(c.tool_calls for c in trace_cases):
        # The Langfuse SDK's default span filter can drop tool spans before they reach
        # Langfuse, which would show as low evidence completeness with no other sign.
        notes.append(
            "Langfuse input has agent runs but no tool calls. The Langfuse SDK's default "
            "span filter may have dropped them; set should_export_span=lambda span: True."
        )

    analyst_labels = _pick_spellings(row.label for row in verdict_rows)
    label_map, unmapped_analyst = _auto_map(analyst_labels)
    agent_only = _pick_spellings(
        label
        for label in (case.agent_label for case in trace_cases)
        if label is not None and normalize_label(label) not in analyst_labels
    )
    agent_label_map, unmapped_agent = _auto_map(agent_only)
    missing = [name for name in _ROOT_ONLY_FIELDS if getattr(mapping, name).value is None]
    if not label_map:
        missing.append(REQUIRED_LABELS)

    tool_names_by_class, case_counts_by_class = _group_by_class(trace_cases, verdict_rows)
    trace_ids = {case.case_id for case in trace_cases}
    verdict_ids = {row.case_id for row in verdict_rows}
    return Proposal(
        trace_format=trace_format,
        agent_run_count=len(agent_runs),
        mapping=mapping,
        label_map=label_map,
        agent_label_map=agent_label_map,
        unmapped_analyst_labels=_cap(unmapped_analyst, "analyst", notes),
        unmapped_agent_labels=_cap(unmapped_agent, "agent", notes),
        tool_names_by_class=tool_names_by_class,
        case_counts_by_class=case_counts_by_class,
        traces_without_verdict=_summarize_orphans(trace_ids - verdict_ids),
        verdicts_without_trace=_summarize_orphans(verdict_ids - trace_ids),
        missing_required=tuple(missing),
        notes=tuple(notes),
    )


def _propose_operation(spans: list[Span], notes: list[str]) -> OperationProposal:
    if any(conventions.OPERATION_ATTRIBUTE in span.attributes for span in spans):
        source = SOURCE_OPERATION_NAME
    elif any(span.name.split(" ", 1)[0] == conventions.INVOKE_AGENT for span in spans):
        source = SOURCE_SPAN_NAMES
    elif any(_OPENINFERENCE_KIND in span.attributes for span in spans):
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
            f"no agent runs found: no span has {conventions.OPERATION_ATTRIBUTE}, "
            f"an '{conventions.INVOKE_AGENT} ...' name, or {_OPENINFERENCE_KIND}"
        )
    default = _DEFAULTS.operation
    return OperationProposal(
        attribute=default.attribute,
        agent_value=default.agent_value,
        tool_value=default.tool_value,
        span_name_fallback=default.span_name_fallback,
        source=source,
    )


def _find_agent_runs(spans: list[Span], operation: OperationConfig) -> list[Span]:
    """Agent spans with no agent span above them: the spans check opens cases at."""
    by_key = {(span.trace_id, span.span_id): span for span in spans}
    is_agent = {key: classify_span(span, operation) == "agent" for key, span in by_key.items()}
    # Whether a span or one of its ancestors is an agent span; filled as the walks go.
    has_agent: dict[tuple[str, str], bool] = {}

    def has_agent_at_or_above(key: tuple[str, str] | None) -> bool:
        path: list[tuple[str, str]] = []
        on_path: set[tuple[str, str]] = set()
        result = False
        # Iterative, with a set for parent cycles: a deep or looping chain must not recurse.
        while key is not None and key in by_key and key not in on_path:
            if key in has_agent:
                result = has_agent[key]
                break
            path.append(key)
            on_path.add(key)
            if is_agent[key]:
                result = True
                break
            parent = by_key[key].parent_span_id
            key = None if parent is None else (key[0], parent)
        for visited in path:
            has_agent[visited] = result
        return result

    runs: list[Span] = []
    for key, span in by_key.items():
        if not is_agent[key]:
            continue
        parent = span.parent_span_id
        if parent is None or not has_agent_at_or_above((span.trace_id, parent)):
            runs.append(span)
    return runs


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
    dotted = tuple("." + suffix for suffix in suffixes)
    counts: Counter[str] = Counter()
    # Keys already judged, so thousands of distinct or huge keys cost one check each.
    usable: dict[str, bool] = {}
    for run in agent_runs:
        found: set[str] = set()
        sources = [run.attributes] if is_root_only else [run.attributes, run.resource_attributes]
        for attributes in sources:
            for key, value in attributes.items():
                if key not in usable:
                    usable[key] = (key in suffixes or key.endswith(dotted)) and _is_writable_key(
                        key
                    )
                if usable[key] and _is_text(value):
                    found.add(key)
        counts.update(found)
    return counts


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


def _propose_tool_field(key: str, is_openinference: bool, tool_calls: list[Span]) -> FieldProposal:
    covered = sum(1 for span in tool_calls if _is_text(span.attributes.get(key)))
    source = SOURCE_OPENINFERENCE if is_openinference else SOURCE_DEFAULT
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
    # As in the join, a case's class is its first verdict row's; the trace's only without one.
    class_by_case: dict[str, str] = {}
    for row in verdict_rows:
        class_by_case.setdefault(row.case_id, row.alert_class)
    tools: dict[str, set[str]] = defaultdict(set)
    counts: Counter[str] = Counter()
    for case in trace_cases:
        alert_class = class_by_case.get(case.case_id, case.alert_class)
        if alert_class is None:
            continue
        counts[alert_class] += 1
        tools[alert_class].update(call.tool_name for call in case.tool_calls if call.tool_name)
    return (
        {name: tuple(sorted(tools[name])[:MAX_LISTED_TOOLS]) for name in sorted(counts)},
        {name: counts[name] for name in sorted(counts)},
    )


def _summarize_orphans(case_ids: set[str]) -> OrphanSummary:
    return OrphanSummary(len(case_ids), tuple(sorted(case_ids)[:MAX_ORPHAN_EXAMPLES]))
