"""Propose a configuration for `init` from already-loaded traces and verdict rows.

Nothing here reads files or prints: the command loads the inputs, calls propose_init, and
decides what to show, ask and write.
"""

from collections import Counter, defaultdict
from collections.abc import Callable, Iterable
from dataclasses import dataclass, replace
from functools import partial

from detecttrace import conventions
from detecttrace.cases import (
    build_trace_cases,
    classify_span,
    find_case_roots,
    is_tool_arguments,
    is_tool_name,
    to_single_text,
)
from detecttrace.config import (
    MappingConfig,
    OperationConfig,
    PromptVersionLookup,
    normalize_label,
)
from detecttrace.model import (
    MAX_LABEL_LENGTH,
    Span,
    TraceCase,
    Verdict,
    VerdictRow,
    to_short_label,
)
from detecttrace.runconfig import TraceFormat

SOURCE_DETECTTRACE = "detecttrace attribute"
SOURCE_LANGFUSE_PROMPT = "Langfuse prompt version"
SOURCE_LANGFUSE_MANAGED_PROMPT = "Langfuse managed prompt, on spans under the agent run"
SOURCE_SUFFIX = "suffix match"
SOURCE_DEFAULT = "default"
SOURCE_NOT_FOUND = "not found"
SOURCE_OPERATION_NAME = conventions.OPERATION_ATTRIBUTE
SOURCE_SPAN_NAMES = "span names"
SOURCE_OPENINFERENCE = "OpenInference"
SOURCE_SET = "set by you"

# The mapping fields check cannot run without; with no mappable label, REQUIRED_LABELS is
# missing too.
REQUIRED_FIELDS = ("case_id", "verdict")
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
# Suggestions in a form; past this many a user types the key instead of picking it.
MAX_SUGGESTED_KEYS = 200

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
    prompt_version_lookup: PromptVersionLookup = "root_then_resource"

    def to_mapping_config(self) -> MappingConfig:
        """The mapping as configuration; a field with no proposal keeps its default key."""
        return MappingConfig(
            case_id=self.case_id.value or _DEFAULTS.case_id,
            alert_class=self.alert_class.value or _DEFAULTS.alert_class,
            verdict=self.verdict.value or _DEFAULTS.verdict,
            prompt_version=self.prompt_version.value or _DEFAULTS.prompt_version,
            prompt_version_lookup=self.prompt_version_lookup,
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
    # Up to MAX_LISTED_TOOLS names per class, most called first, then in code-point order.
    tool_names_by_class: dict[str, tuple[str, ...]]
    tool_counts_by_class: dict[str, int]  # distinct tool names, listed or not
    case_counts_by_class: dict[str, int]
    traces_without_verdict: OrphanSummary
    verdicts_without_trace: OrphanSummary
    missing_required: tuple[str, ...]  # from REQUIRED_FIELDS, then REQUIRED_LABELS
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

    For Langfuse input with no prompt version found that way, langfuse.prompt_version (a
    managed prompt linked to a generation) is proposed with the descendant lookup when
    check, reading it that way, finds a version for at least half of the agent runs.

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
        for name in ("case_id", "alert_class", "verdict")
    }
    is_openinference = operation.source == SOURCE_OPENINFERENCE
    tool_source = SOURCE_OPENINFERENCE if is_openinference else SOURCE_DEFAULT
    tool_name = _OPENINFERENCE_TOOL_NAME if is_openinference else _DEFAULTS.tool_name
    tool_arguments = _OPENINFERENCE_TOOL_ARGUMENTS if is_openinference else _DEFAULTS.tool_arguments
    unversioned = MappingProposal(
        case_id=fields["case_id"],
        alert_class=fields["alert_class"],
        verdict=fields["verdict"],
        prompt_version=FieldProposal(None, SOURCE_NOT_FOUND, 0, len(agent_runs)),
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

    # Probing for a managed prompt version builds cases, which at 50k cases costs seconds; a
    # mapping built once is reused, so the probe that succeeds is also the final build.
    built: dict[MappingConfig, list[TraceCase]] = {}

    def build_cases(case_mapping: MappingConfig) -> list[TraceCase]:
        if case_mapping not in built:
            built[case_mapping], _ = build_trace_cases(spans, case_mapping)
        return built[case_mapping]

    prompt_version, prompt_version_lookup = _propose_or_measure_prompt_version(
        agent_runs,
        trace_format,
        # The given mapping, not the proposal's, so settings the proposal omits still apply.
        unversioned.to_mapping_config() if mapping is None else mapping,
        unversioned.case_id.value is not None,
        mapping,
        build_cases,
        notes,
    )
    proposed = replace(
        unversioned, prompt_version=prompt_version, prompt_version_lookup=prompt_version_lookup
    )
    if proposed.alert_class.value is None:
        notes.append(
            "no alert class found on agent runs; alert classes come from the verdict file only"
        )
    if proposed.prompt_version.value is None:
        notes.append("no prompt version found; every case will show as (no version)")

    trace_cases: list[TraceCase] = []
    if proposed.case_id.value is not None:
        trace_cases = build_cases(proposed.to_mapping_config() if mapping is None else mapping)

    analyst_labels = _pick_spellings(row.label for row in verdict_rows)
    label_map, unmapped_analyst = _auto_map(analyst_labels)
    agent_only = _pick_spellings(
        label
        for label in (case.agent_label for case in trace_cases)
        if label is not None and normalize_label(label) not in analyst_labels
    )
    agent_label_map, unmapped_agent = _auto_map(agent_only)
    missing = [name for name in REQUIRED_FIELDS if getattr(proposed, name).value is None]
    if not label_map:
        missing.append(REQUIRED_LABELS)

    tool_names_by_class, tool_counts_by_class, case_counts_by_class = _group_by_class(
        trace_cases, verdict_rows
    )
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
        tool_counts_by_class=tool_counts_by_class,
        case_counts_by_class=case_counts_by_class,
        traces_without_verdict=_summarize_orphans(trace_ids - verdict_ids),
        verdicts_without_trace=_summarize_orphans(verdict_ids - trace_ids),
        missing_required=tuple(missing),
        notes=tuple(notes),
    )


def list_run_attribute_keys(spans: list[Span], mapping: MappingConfig) -> tuple[str, ...]:
    """The attribute keys on the agent runs `mapping` finds, and on their resources, as
    candidates for the mapping fields: unique, in code-point order, the first
    MAX_SUGGESTED_KEYS of them.

    Resource keys are included because check reads alert class and prompt version from
    there too. A key a user could not read back or type is left out, as propose_init leaves
    it out.
    """
    keys = {
        key
        for run in _find_agent_runs(spans, mapping.operation, mapping.case_id)
        for attributes in (run.attributes, run.resource_attributes)
        for key in attributes
    }
    return tuple(sorted(key for key in keys if _is_writable_key(key))[:MAX_SUGGESTED_KEYS])


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
    """The agent spans check would open cases at, plus each run it would report as missing
    its case ID: with `case_id_key`, as check would read it; without, from whichever
    candidate key holds the case ID.
    """
    found = find_case_roots(spans, operation, partial(_read_case_id, case_id_key))
    return found.roots + found.keyless_tops


def _read_case_id(case_id_key: str | None, span: Span) -> str | None:
    if case_id_key is not None:
        return _read_text(span, case_id_key)
    text = _read_text(span, _DEFAULTS.case_id)
    if text is not None:
        return text
    suffixes = _SUFFIXES["case_id"]
    for key, value in span.attributes.items():
        text = to_single_text(value)
        if text is not None and _is_suffix_key(key, suffixes):
            return to_short_label(text)
    return None


def _read_text(span: Span, key: str) -> str | None:
    text = to_single_text(span.attributes.get(key))
    # Shortened as check shortens a case ID, so two IDs check would equate stay equal.
    return None if text is None else to_short_label(text)


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


def _propose_or_measure_prompt_version(
    agent_runs: list[Span],
    trace_format: TraceFormat,
    base: MappingConfig,
    has_case_id: bool,
    mapping: MappingConfig | None,
    build_cases: Callable[[MappingConfig], list[TraceCase]],
    notes: list[str],
) -> tuple[FieldProposal, PromptVersionLookup]:
    field_notes: list[str] = []
    total = len(agent_runs)
    field = _propose_field("prompt_version", agent_runs, trace_format, field_notes)
    lookup = _DEFAULTS.prompt_version_lookup
    if field.value is None and trace_format == "langfuse" and has_case_id:
        # Langfuse links a managed prompt only to a generation, never to the agent run, so
        # the version is read below the agent run exactly as check's descendant lookup does.
        # `base` is the mapping check would use but for the prompt version, so on success
        # this build is the final one.
        managed = base.model_copy(
            update={
                "prompt_version": _LANGFUSE_PROMPT_VERSION,
                "prompt_version_lookup": "descendant",
            }
        )
        covered = _count_versioned_cases(build_cases(managed))
        if _is_enough(covered, total):
            field = FieldProposal(
                _LANGFUSE_PROMPT_VERSION, SOURCE_LANGFUSE_MANAGED_PROMPT, covered, total
            )
            lookup = "descendant"
        elif covered:
            field_notes.append(
                f"prompt_version: {_LANGFUSE_PROMPT_VERSION} gives a version under only "
                f"{covered:,} of {total:,} agent runs; not proposed, since it must be on "
                "at least half"
            )
    # As in _propose_or_measure_field: the default key given back for a field not found is
    # no change.
    if mapping is None or (
        mapping.prompt_version == (field.value or _DEFAULTS.prompt_version)
        and mapping.prompt_version_lookup == lookup
    ):
        notes += field_notes
        return field, lookup
    if mapping.prompt_version_lookup == "descendant":
        covered = _count_versioned_cases(build_cases(mapping))
    else:
        covered = sum(
            1 for run in agent_runs if _has_value(run, mapping.prompt_version, is_root_only=False)
        )
    return (
        FieldProposal(mapping.prompt_version, SOURCE_SET, covered, total),
        mapping.prompt_version_lookup,
    )


def _count_versioned_cases(trace_cases: list[TraceCase]) -> int:
    return sum(1 for case in trace_cases if case.prompt_version is not None)


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
                if usable[key] and _has_text(value):
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
    if _has_text(run.attributes.get(key)):
        return True
    return not is_root_only and _has_text(run.resource_attributes.get(key))


def _has_text(value: object) -> bool:
    return to_single_text(value) is not None


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
) -> tuple[dict[str, tuple[str, ...]], dict[str, int], dict[str, int]]:
    # As in check: a case without a verdict row is dropped (ROOT_WITHOUT_VERDICT), a case's
    # class is its first verdict row's, and classes group by normalized name, shown in the
    # spelling of the first case in case-ID order.
    class_by_case: dict[str, str] = {}
    for row in verdict_rows:
        class_by_case.setdefault(row.case_id, row.alert_class)
    spellings: dict[str, str] = {}
    calls: dict[str, Counter[str]] = defaultdict(Counter)
    counts: Counter[str] = Counter()
    for case in sorted(trace_cases, key=lambda case: case.case_id):
        alert_class = class_by_case.get(case.case_id)
        if alert_class is None:
            continue
        key = normalize_label(alert_class)
        spellings.setdefault(key, alert_class)
        counts[key] += 1
        calls[key].update(call.tool_name for call in case.tool_calls if call.tool_name)
    tool_names: dict[str, tuple[str, ...]] = {}
    for key in sorted(counts):
        ranked = sorted(calls[key].items(), key=lambda item: (-item[1], item[0]))
        tool_names[spellings[key]] = tuple(name for name, _ in ranked[:MAX_LISTED_TOOLS])
    return (
        tool_names,
        {spellings[key]: len(calls[key]) for key in sorted(counts)},
        {spellings[key]: counts[key] for key in sorted(counts)},
    )


def _summarize_orphans(case_ids: set[str]) -> OrphanSummary:
    return OrphanSummary(len(case_ids), tuple(sorted(case_ids)[:MAX_ORPHAN_EXAMPLES]))
