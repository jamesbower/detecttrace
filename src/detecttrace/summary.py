"""The end-of-run terminal summary: issue severities, grouped issue lines, and join coverage."""

import unicodedata
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from enum import StrEnum

from detecttrace import conventions
from detecttrace.model import MAX_LABEL_LENGTH, Issue, IssueKind


class Severity(StrEnum):
    INVALID_INPUT = "invalid_input"
    WARNING = "warning"


_I = Severity.INVALID_INPUT
_W = Severity.WARNING

SEVERITY: Mapping[IssueKind, Severity] = {
    IssueKind.EMPTY_FILE: _I,
    IssueKind.TRUNCATED_LINE: _I,
    IssueKind.TRUNCATED_FILE: _I,
    IssueKind.INVALID_LINE: _I,
    IssueKind.INVALID_FILE: _I,
    IssueKind.INVALID_SPAN: _I,
    IssueKind.DUPLICATE_SPAN: _W,
    IssueKind.CONFLICTING_DUPLICATE_SPAN: _I,
    IssueKind.INVALID_ATTRIBUTE: _I,
    IssueKind.MISSING_TOOL_NAME: _I,
    IssueKind.AGENT_WITHOUT_CASE_ID: _I,
    IssueKind.ORPHAN_TOOL_SPAN: _I,
    IssueKind.BROKEN_PARENT_CHAIN: _I,
    IssueKind.NESTED_CASE: _W,
    IssueKind.INCOMPLETE_TRACE: _W,
    IssueKind.VERSION_CONFLICT: _I,
    IssueKind.DUPLICATE_ROOT: _W,
    IssueKind.INVALID_VERDICT_ROW: _I,
    IssueKind.LONG_VERDICT_VALUE: _I,
    IssueKind.DUPLICATE_VERDICT: _W,
    IssueKind.CONFLICTING_ANALYST_VERDICT: _I,
    IssueKind.ROOT_WITHOUT_VERDICT: _W,
    IssueKind.VERDICT_WITHOUT_ROOT: _W,
    IssueKind.ALERT_CLASS_CONFLICT: _I,
    IssueKind.UNMAPPED_ANALYST_LABEL: _I,
    IssueKind.UNMAPPED_AGENT_LABEL: _I,
    IssueKind.MISSING_AGENT_VERDICT: _I,
    IssueKind.UNREADABLE_ARGUMENTS: _I,
    IssueKind.UNREADABLE_DURATION: _I,
    IssueKind.UNREADABLE_KQL_TIMESPAN: _I,
    IssueKind.RULE_TYPE_MISMATCH: _W,
    IssueKind.MISSING_TOOL_ARGUMENTS: _W,
    IssueKind.UNKNOWN_CHECKLIST_TOOL: _W,
    IssueKind.UNUSED_CHECKLIST: _W,
    IssueKind.CONSOLE_EXPORTER_OUTPUT: _I,
    IssueKind.UNSUPPORTED_COMPRESSION: _I,
    IssueKind.INVALID_LANGFUSE_DOCUMENT: _I,
    IssueKind.INVALID_LANGFUSE_ROW: _I,
    IssueKind.LANGFUSE_METADATA_NOT_OBJECT: _I,
    IssueKind.LEGACY_LANGFUSE_TRACE: _I,
    IssueKind.LANGFUSE_WITHOUT_IO: _W,
    IssueKind.LANGFUSE_NO_TOOL_CALLS: _W,
}

_AGENT = conventions.INVOKE_AGENT

# kind: (after a count of 1, after any other count, fix hint).
# Placeholders: {key} is the group key (label, tool, or checklist item); {config} the file name.
_TEMPLATES: Mapping[IssueKind, tuple[str, str, str]] = {
    IssueKind.EMPTY_FILE: (
        "trace file is empty",
        "trace files are empty",
        "Check that the exporter writes to the trace folder, or remove empty files.",
    ),
    IssueKind.TRUNCATED_LINE: (
        "trace file ends with a cut-off line, which was skipped",
        "trace files end with a cut-off line, which was skipped",
        "This is normal while a file is still being written; run again once it is complete.",
    ),
    IssueKind.TRUNCATED_FILE: (
        "compressed trace file ends early",
        "compressed trace files end early",
        "Copy or compress the file again once the exporter has finished.",
    ),
    IssueKind.INVALID_LINE: (
        "trace line is not valid JSON and was skipped",
        "trace lines are not valid JSON and were skipped",
        "Each line must hold one trace export record (an OTLP JSON export request, or a "
        "Langfuse observation row); check the exporter's output format.",
    ),
    IssueKind.INVALID_FILE: (
        "trace file or folder could not be read",
        "trace files or folders could not be read",
        "Check the file permissions and that each file holds OTLP JSON or a Langfuse export.",
    ),
    IssueKind.INVALID_SPAN: (
        "span is malformed and was skipped",
        "spans are malformed and were skipped",
        "Each span needs a valid traceId, spanId, and start and end times.",
    ),
    IssueKind.DUPLICATE_SPAN: (
        "span appears more than once with the same content; the copy was ignored",
        "spans appear more than once with the same content; the copies were ignored",
        "Check that the same traces are not exported to more than one file.",
    ),
    IssueKind.CONFLICTING_DUPLICATE_SPAN: (
        "span ID is reused with different content; the first copy was kept",
        "span IDs are reused with different content; the first copies were kept",
        "Check that the exporter writes each span once and that span IDs are unique.",
    ),
    IssueKind.INVALID_ATTRIBUTE: (
        "attribute has a value that could not be used",
        "attributes have values that could not be used",
        "Check the attribute types against the attribute specification in the README.",
    ),
    IssueKind.MISSING_TOOL_NAME: (
        "tool span has no tool name, so it satisfies no checklist item",
        "tool spans have no tool name, so they satisfy no checklist item",
        "Set the tool name attribute (mapping.tool_name in {config}) on every tool span.",
    ),
    IssueKind.AGENT_WITHOUT_CASE_ID: (
        f"{_AGENT} span has no case ID and was not scored",
        f"{_AGENT} spans have no case ID and were not scored",
        "Set the case ID attribute (mapping.case_id in {config}) on every " + _AGENT + " span.",
    ),
    IssueKind.ORPHAN_TOOL_SPAN: (
        "tool span is not inside any case",
        "tool spans are not inside any case",
        f"Check that tool spans are descendants of an {_AGENT} span with a case ID.",
    ),
    IssueKind.BROKEN_PARENT_CHAIN: (
        "span has a parent chain that is broken or loops",
        "spans have a parent chain that is broken or loops",
        "Check that parent span IDs point to spans exported in the same trace.",
    ),
    IssueKind.NESTED_CASE: (
        "case starts inside another case and is treated as a sub-agent",
        "cases start inside another case and are treated as sub-agents",
        "Set the case ID only on the top-level " + _AGENT + " span of each case.",
    ),
    IssueKind.INCOMPLETE_TRACE: (
        "case has tool spans that could not be attached to it",
        "cases have tool spans that could not be attached to them",
        "Check that the trace files hold every span of each trace.",
    ),
    IssueKind.VERSION_CONFLICT: (
        "case has more than one prompt version, so it has no version",
        "cases have more than one prompt version, so they have no version",
        "Set one prompt version (mapping.prompt_version in {config}) per case.",
    ),
    IssueKind.DUPLICATE_ROOT: (
        "case ID appears on more than one agent span; the latest was kept",
        "case IDs appear on more than one agent span; the latest was kept",
        "Check that each case is exported once.",
    ),
    IssueKind.INVALID_VERDICT_ROW: (
        "verdict row could not be read and was skipped",
        "verdict rows could not be read and were skipped",
        "Each row needs case_id, alert_class, and verdict; quote values that contain commas.",
    ),
    IssueKind.LONG_VERDICT_VALUE: (
        f"verdict row has a value longer than {MAX_LABEL_LENGTH} characters, which was shortened",
        f"verdict rows have values longer than {MAX_LABEL_LENGTH} characters, which were shortened",
        f"Keep case IDs, alert classes, and verdict labels to {MAX_LABEL_LENGTH} characters.",
    ),
    IssueKind.DUPLICATE_VERDICT: (
        "case has repeated verdict rows that agree",
        "cases have repeated verdict rows that agree",
        "Remove the repeated rows from the verdict file.",
    ),
    IssueKind.CONFLICTING_ANALYST_VERDICT: (
        "case has verdict rows that disagree, so it has no analyst verdict",
        "cases have verdict rows that disagree, so they have no analyst verdict",
        "Keep one verdict row per case in the verdict file.",
    ),
    IssueKind.ROOT_WITHOUT_VERDICT: (
        "trace has no verdict row",
        "traces have no verdict row",
        "Add verdicts for these cases, or check mapping.case_id in {config}.",
    ),
    IssueKind.VERDICT_WITHOUT_ROOT: (
        "verdict has no matching trace",
        "verdicts have no matching trace",
        "Check mapping.case_id in {config} and that the traces cover the same cases.",
    ),
    IssueKind.ALERT_CLASS_CONFLICT: (
        "case has conflicting alert classes; the verdict file's value was used",
        "cases have conflicting alert classes; the verdict file's values were used",
        "Make the alert class agree between the traces and the verdict file.",
    ),
    IssueKind.UNMAPPED_ANALYST_LABEL: (
        "verdict uses the label '{key}', which has no mapping",
        "verdicts use the label '{key}', which has no mapping",
        "Add it to label_map in {config}.",
    ),
    IssueKind.UNMAPPED_AGENT_LABEL: (
        "case has the agent verdict '{key}', which has no mapping",
        "cases have the agent verdict '{key}', which has no mapping",
        "Add it to agent_label_map or label_map in {config}.",
    ),
    IssueKind.MISSING_AGENT_VERDICT: (
        "case has no agent verdict on its trace",
        "cases have no agent verdict on their trace",
        "Set the verdict attribute (mapping.verdict in {config}) on the " + _AGENT + " span.",
    ),
    IssueKind.UNREADABLE_ARGUMENTS: (
        "tool call has arguments that could not be read",
        "tool calls have arguments that could not be read",
        "Send tool arguments as a JSON object so checklist rules can check them.",
    ),
    IssueKind.UNREADABLE_DURATION: (
        "tool argument has a duration or timestamp that could not be read",
        "tool arguments have a duration or timestamp that could not be read",
        "Use ISO 8601 durations and timestamps in arguments that checklist rules check.",
    ),
    IssueKind.UNREADABLE_KQL_TIMESPAN: (
        "tool argument has a KQL lookback that could not be read",
        "tool arguments have a KQL lookback that could not be read",
        "Write lookbacks as ago(<number><unit>), for example ago(7d).",
    ),
    IssueKind.RULE_TYPE_MISMATCH: (
        "rule in checklist item '{key}' expects a different type than the tool arguments hold",
        "rules in checklist item '{key}' expect a different type than the tool arguments hold",
        "Change the rule's expected type, or fix how the agent sends that argument.",
    ),
    IssueKind.MISSING_TOOL_ARGUMENTS: (
        "checklist item has argument rules, but no call to its tool carries arguments",
        "checklist items have argument rules, but no call to their tools carries arguments",
        "Check mapping.tool_arguments in {config}.",
    ),
    IssueKind.UNKNOWN_CHECKLIST_TOOL: (
        "checklist item requires the tool '{key}', which no case calls",
        "checklist items require the tool '{key}', which no case calls",
        "Check the tool name's spelling in the checklist.",
    ),
    IssueKind.UNUSED_CHECKLIST: (
        "checklist is for an alert class that no case has",
        "checklists are for alert classes that no case has",
        "Check the alert class name in the checklist against the verdict file.",
    ),
    IssueKind.CONSOLE_EXPORTER_OUTPUT: (
        "trace file holds console exporter output, not OTLP JSON",
        "trace files hold console exporter output, not OTLP JSON",
        # Doubled braces: hints go through str.format.
        "Write traces with the Collector file exporter, or from the agent with "
        "FileSpanExporter: pip install detecttrace[otel], then add "
        'BatchSpanProcessor(FileSpanExporter("traces/{{date}}-{{pid}}.jsonl")) '
        "to your TracerProvider.",
    ),
    IssueKind.UNSUPPORTED_COMPRESSION: (
        "trace file is zstd-compressed and was skipped",
        "trace files are zstd-compressed and were skipped",
        "Install detecttrace[zstd] to read zstd-compressed files.",
    ),
    IssueKind.INVALID_LANGFUSE_DOCUMENT: (
        "trace document is not a Langfuse observation export and was skipped",
        "trace documents are not Langfuse observation exports and were skipped",
        "With traces.format: langfuse, each file must hold observation rows: an API page "
        "with a data list, a JSON array of rows, or one row per line.",
    ),
    IssueKind.INVALID_LANGFUSE_ROW: (
        "Langfuse observation row is malformed and was skipped",
        "Langfuse observation rows are malformed and were skipped",
        "Each observation row needs an id, a traceId, and start and end times.",
    ),
    IssueKind.LANGFUSE_METADATA_NOT_OBJECT: (
        "Langfuse observation has metadata that is not an object, so its attributes were not read",
        "Langfuse observations have metadata that is not an object, so their attributes were "
        "not read",
        "Export observations from Langfuse v4 (the v2 observations API or a blob export), "
        "which writes metadata as an object.",
    ),
    IssueKind.LEGACY_LANGFUSE_TRACE: (
        "Langfuse row is a legacy trace object and was skipped",
        "Langfuse rows are legacy trace objects and were skipped",
        "Export observations (the v2 observations API or a blob export) instead of traces.",
    ),
    IssueKind.LANGFUSE_WITHOUT_IO: (
        "Langfuse export has no input or output fields, so tool arguments are missing and "
        "long metadata values may be cut",
        "Langfuse exports have no input or output fields, so tool arguments are missing and "
        "long metadata values may be cut",
        "Export observations with the io field group; expandMetadata alone keeps metadata "
        "whole but still leaves out tool arguments.",
    ),
    IssueKind.LANGFUSE_NO_TOOL_CALLS: (
        "Langfuse input has agent runs but no tool calls",
        "Langfuse inputs have agent runs but no tool calls",
        "The Langfuse SDK's default span filter may have dropped them; "
        "set should_export_span=lambda span: True.",
    ),
}

# Kinds whose Issue.detail is the grouping key itself, so repeating it per example adds nothing.
_KEY_IN_DETAIL = frozenset(
    {
        IssueKind.UNMAPPED_ANALYST_LABEL,
        IssueKind.UNMAPPED_AGENT_LABEL,
        IssueKind.UNKNOWN_CHECKLIST_TOOL,
    }
)
_MAX_EXAMPLES = 3
TERMINAL_TEXT_LIMIT = 60


@dataclass(frozen=True, slots=True)
class IssueExample:
    subject: str
    detail: str | None  # None when the detail is the line's key or empty


@dataclass(frozen=True, slots=True)
class SummaryLine:
    severity: Severity
    kind: IssueKind
    count: int
    message: str  # the sentence with the count, without the fix hint, with the raw key
    message_without_count: str  # the same sentence after its leading count
    terminal_message: str  # the same sentence, with the key made safe for a terminal
    hint: str  # the fix hint, with the raw configuration file name
    terminal_hint: str  # the same hint, with the file name made safe for a terminal
    examples: tuple[IssueExample, ...]  # up to 3, one per subject, raw


@dataclass(frozen=True, slots=True)
class CoverageLine:
    message: str  # ready for the terminal
    hint: str | None  # ready for the terminal; set only when coverage is low
    is_low: bool
    sentence: str  # the message without the terminal's "WARNING: " prefix
    raw_hint: str | None  # the hint with the raw configuration file name
    share_text: str | None  # "44% of traces matched a verdict"; None when nothing was read


@dataclass(slots=True)
class _Group:
    count: int = 0
    examples: list[IssueExample] = field(default_factory=list)


@dataclass(frozen=True, slots=True)
class JoinCoverage:
    verdicts_matched: int
    verdicts_total: int
    traces_matched: int
    traces_total: int


def summarize_issues(
    issues: Sequence[Issue], config_name: str = "detecttrace.yaml"
) -> list[SummaryLine]:
    """Group issues by kind and key into one line each: invalid input first, then by count."""
    groups: dict[tuple[IssueKind, str], _Group] = {}
    for issue in issues:
        group_key = (issue.kind, _to_group_key(issue))
        group = groups.get(group_key)
        if group is None:
            group = groups[group_key] = _Group()
        group.count += 1
        if len(group.examples) < _MAX_EXAMPLES and all(
            example.subject != issue.subject for example in group.examples
        ):
            detail = None if issue.kind in _KEY_IN_DETAIL else issue.detail or None
            group.examples.append(IssueExample(issue.subject, detail))
    ordered = sorted(
        groups.items(),
        key=lambda item: (
            SEVERITY[item[0][0]] is not Severity.INVALID_INPUT,
            -item[1].count,
            item[0][0].value,
            item[0][1],
        ),
    )
    terminal_config_name = to_terminal_text(config_name)
    return [
        SummaryLine(
            severity=SEVERITY[kind],
            kind=kind,
            count=group.count,
            message=_render_message(kind, key, group.count),
            message_without_count=_render_phrase(kind, key, group.count),
            terminal_message=_render_message(kind, to_terminal_text(key), group.count),
            hint=_render_hint(kind, key, config_name),
            terminal_hint=_render_hint(kind, to_terminal_text(key), terminal_config_name),
            examples=tuple(group.examples),
        )
        for (kind, key), group in ordered
    ]


def to_message_without_count(message: str, count: int) -> str:
    """The sentence of a summary line's `message` after its leading count."""
    return message.removeprefix(_to_count_prefix(count))


def to_terminal_text(text: str, limit: int | None = TERMINAL_TEXT_LIMIT) -> str:
    """Make input-derived text safe to print: escape control and format characters, shorten.

    Unicode category C covers terminal escapes (ESC, OSC 52 clipboard writes), line breaks
    that could fake output lines, and bidirectional overrides that reorder what is shown.
    The line and paragraph separators (Zl, Zp) also break lines in terminals and viewers.
    The case table's script applies the same rule, so both must change together.
    """
    # Escapes only lengthen the text, so one character past the limit is enough to read.
    head = text if limit is None else text[: limit + 1]
    pieces = [_to_escape(char) if _is_hidden(unicodedata.category(char)) else char for char in head]
    escaped = "".join(pieces)
    if limit is None or len(escaped) <= limit:
        return escaped
    # Cut between pieces, so a half escape such as "\x1" never reads as a different character.
    kept: list[str] = []
    length = 0
    for piece in pieces:
        length += len(piece)
        if length > limit - 1:
            break
        kept.append(piece)
    return "".join(kept) + "…"


def to_visible_text(text: str) -> str:
    """Escape control and format characters as `to_terminal_text` does, but never shorten.

    For pages: autoescaping stops markup, not a bidirectional override reordering a label.
    """
    return to_terminal_text(text, limit=None)


def has_invalid_input(issues: Sequence[Issue]) -> bool:
    return any(SEVERITY[issue.kind] is Severity.INVALID_INPUT for issue in issues)


def is_low_coverage(matched: int, total: int) -> bool:
    """Below half matched; with nothing read there is no share to call low."""
    return matched * 2 < total


def coverage_lines(
    coverage: JoinCoverage, config_name: str = "detecttrace.yaml"
) -> list[CoverageLine]:
    """Two lines, verdicts then traces; a wrong case ID mapping shows up here, not as an error."""
    return [
        _coverage_line(
            coverage.verdicts_matched,
            coverage.verdicts_total,
            ("verdict", "verdicts"),
            "a trace",
            config_name,
        ),
        _coverage_line(
            coverage.traces_matched,
            coverage.traces_total,
            ("trace", "traces"),
            "a verdict",
            config_name,
        ),
    ]


def _to_group_key(issue: Issue) -> str:
    if issue.kind in _KEY_IN_DETAIL:
        return issue.detail
    # A mismatch's detail describes one call; its subject is the checklist item to group by.
    if issue.kind is IssueKind.RULE_TYPE_MISMATCH:
        return issue.subject
    return ""


def _render_message(kind: IssueKind, key: str, count: int) -> str:
    return _to_count_prefix(count) + _render_phrase(kind, key, count)


def _render_phrase(kind: IssueKind, key: str, count: int) -> str:
    singular, plural, _ = _TEMPLATES[kind]
    phrase = singular if count == 1 else plural
    # format() never re-reads substituted values, so braces in a label are safe.
    return f"{phrase}.".format(key=key)


def _to_count_prefix(count: int) -> str:
    return f"{count:,} "


def _render_hint(kind: IssueKind, key: str, config_name: str) -> str:
    return _TEMPLATES[kind][2].format(key=key, config=config_name)


def _is_hidden(category: str) -> bool:
    return category.startswith("C") or category in ("Zl", "Zp")


def _to_escape(char: str) -> str:
    code = ord(char)
    if code < 0x100:
        return f"\\x{code:02x}"
    if code < 0x10000:
        return f"\\u{code:04x}"
    return f"\\U{code:08x}"


def _coverage_line(
    matched: int,
    total: int,
    nouns: tuple[str, str],
    other_side: str,
    config_name: str,
) -> CoverageLine:
    noun = nouns[0] if total == 1 else nouns[1]
    if total == 0:
        sentence = f"0 of 0 {noun} matched {other_side}: no {noun} were read."
        return CoverageLine(sentence, None, False, sentence, None, None)
    share = _to_share_text(matched, total)
    sentence = f"{matched:,} of {total:,} {noun} matched {other_side} ({share})."
    share_text = f"{share} of {noun} matched {other_side}"
    if not is_low_coverage(matched, total):
        return CoverageLine(sentence, None, False, sentence, None, share_text)
    sentence += " Less than half matched, so the results may be misleading."
    hint = "Check mapping.case_id in {config}."
    return CoverageLine(
        f"WARNING: {sentence}",
        hint.format(config=to_terminal_text(config_name)),
        True,
        sentence,
        hint.format(config=config_name),
        share_text,
    )


def _to_share_text(matched: int, total: int) -> str:
    # Floored so a side just under half never shows as 50% next to the warning, and a few
    # matches never read as none.
    percent = matched * 100 // total
    return "<1%" if percent == 0 and matched > 0 else f"{percent}%"
