"""Turn an `init` proposal into the text of detecttrace.yaml and an example checklist.

Nothing here writes files or prints. The command builds an InitDraft from the proposal,
applies `--set` overrides, renders the text, and checks that the text loads back to the
draft's values before writing it.
"""

import dataclasses
import os
import re
from collections.abc import Callable
from pathlib import Path, PurePath
from typing import Any, get_args

from pydantic import ValidationError

from detecttrace.checklist import EXAMPLE_SUFFIX
from detecttrace.config import MappingConfig, OperationConfig, normalize_label
from detecttrace.init_proposal import REQUIRED_FIELDS, REQUIRED_LABELS, FieldProposal, Proposal
from detecttrace.model import Verdict
from detecttrace.runconfig import (
    MAX_CONFIG_BYTES,
    ConfigFileError,
    DashboardConfig,
    RunConfig,
    TraceFormat,
    describe_validation_error,
    to_run_config,
    to_ui_config,
)
from detecttrace.yaml12 import Yaml12Error, parse_yaml12

CHECKLISTS_FOLDER = "checklists"
MAX_EXAMPLE_ITEMS = 50
MAX_FILE_NAME_LENGTH = 64

_LABEL_KEYS = ("label_map", "agent_label_map")
# The app keeps its inputs in its database and serves the dashboard, so its file omits these.
_APP_OMITTED_KEYS = ("traces", "verdicts", "output")
_INIT_INTRO = (
    "Configuration written by `detecttrace init`. Paths are relative to this file.",
    "Run `detecttrace check` to build the dashboard.",
)
_UI_INTRO = (
    "Configuration written by `detecttrace ui`. Paths are relative to this file.",
    "Traces and verdicts are uploaded in the app, so this file does not name them.",
)
_MAPPING_FIELDS = (
    "case_id",
    "alert_class",
    "verdict",
    "prompt_version",
    "prompt_version_lookup",
    "tool_name",
    "tool_arguments",
)
# The mapping fields init shows and asks about, in order; the operation rule is set with --set.
FIELD_NAMES = ("case_id", "alert_class", "verdict", "prompt_version", "tool_name", "tool_arguments")
_TOOL_FIELDS = ("tool_name", "tool_arguments")
_OPERATION_FIELDS = ("attribute", "agent_value", "tool_value", "span_name_fallback")
_TRACE_FORMAT_KEY = "traces.format"
_OTHER_KEYS = (
    _TRACE_FORMAT_KEY,
    "traces.path",
    "verdicts.path",
    "checklists",
    "output",
    "dashboard.max_detail_cases",
)
SET_KEYS = (
    *(f"mapping.{name}" for name in _MAPPING_FIELDS),
    *(f"mapping.operation.{name}" for name in _OPERATION_FIELDS),
    *_OTHER_KEYS,
    *(f"{name}.<label>" for name in _LABEL_KEYS),
)
SET_HELP = (
    "Keys: "
    + ", ".join(SET_KEYS)
    + ". The label is everything between the first '.' and the first '=', so a label "
    "containing '=' cannot be set this way. Paths are written as given, relative to the "
    "configuration file."
)
_NON_TEXT_KEYS = ("mapping.operation.span_name_fallback", "dashboard.max_detail_cases")
_SATISFIES = {f"mapping.{name}": name for name in REQUIRED_FIELDS}
_DEFAULT_MAPPING = MappingConfig()
_DEFAULT_OUTPUT = RunConfig.model_fields["output"].default.as_posix()
_DEFAULT_MAX_DETAIL_CASES = DashboardConfig().max_detail_cases
_WINDOWS_RESERVED = frozenset(
    ["con", "prn", "aux", "nul"]
    + [f"{prefix}{digit}" for prefix in ("com", "lpt") for digit in range(1, 10)]
)
# YAML refuses an implicit key over 1024 characters, so longer labels use the explicit
# `? key` form; the margin leaves room for the quotes.
_MAX_IMPLICIT_KEY = 1000
_NEEDS_ESCAPE = re.compile(r'[^\x20-\x7e]|["\\]')
_NOT_PRINTABLE_ASCII = re.compile(r"[^\x20-\x7e]")
_UNSAFE_NAME_CHARS = re.compile(r"[^a-z0-9_-]+")
_REPEATED_UNDERSCORES = re.compile(r"_{2,}")
_SURROGATE = re.compile("[\ud800-\udfff]")


class OverrideError(ValueError):
    """A `--set` argument names no known key or gives a value the configuration rejects."""


class RoundTripError(Exception):
    """The rendered configuration does not load back to the draft's values: a bug in init."""


@dataclasses.dataclass(frozen=True, slots=True)
class InitDraft:
    """Everything detecttrace.yaml will say, plus what its comments report.

    Paths are POSIX text relative to the configuration file's folder, as they are written.
    """

    trace_format: TraceFormat
    traces_path: str
    verdicts_path: str
    checklists_path: str | None  # set when an example checklist is written there
    output: str
    max_detail_cases: int
    mapping: MappingConfig
    label_map: dict[str, Verdict]  # keys spelled as written
    agent_label_map: dict[str, Verdict]
    unmapped_analyst_labels: tuple[str, ...]  # written as commented-out entries
    unmapped_agent_labels: tuple[str, ...]
    missing_required: tuple[str, ...]
    agent_run_count: int
    coverage: dict[str, str]  # mapping field -> how init found its key, for the header
    notes: tuple[str, ...]
    example_class: str | None
    example_tools: tuple[str, ...]  # most called first
    example_tool_count: int  # the class's distinct tools, including any not in example_tools


def create_draft(
    proposal: Proposal, *, config_path: Path, traces_path: Path, verdicts_path: Path
) -> InitDraft:
    """The draft for `proposal`, with input paths rewritten relative to `config_path`'s folder."""
    folder = config_path.absolute().parent
    mapping = proposal.mapping
    example_class = choose_example_class(proposal)
    operation = mapping.operation
    return InitDraft(
        trace_format=proposal.trace_format,
        traces_path=_to_relative_text(traces_path, folder),
        verdicts_path=_to_relative_text(verdicts_path, folder),
        checklists_path=None if example_class is None else CHECKLISTS_FOLDER,
        output=_DEFAULT_OUTPUT,
        max_detail_cases=_DEFAULT_MAX_DETAIL_CASES,
        mapping=mapping.to_mapping_config(),
        label_map=dict(proposal.label_map),
        agent_label_map=dict(proposal.agent_label_map),
        unmapped_analyst_labels=proposal.unmapped_analyst_labels,
        unmapped_agent_labels=proposal.unmapped_agent_labels,
        missing_required=proposal.missing_required,
        agent_run_count=proposal.agent_run_count,
        coverage={
            **{
                name: describe_field(name, getattr(mapping, name), to_yaml_string)
                for name in FIELD_NAMES
            },
            "operation": (
                f"{to_yaml_string(operation.attribute)}: agent runs "
                f"{to_yaml_string(operation.agent_value)}, tool calls "
                f"{to_yaml_string(operation.tool_value)} ({operation.source})"
            ),
        },
        notes=proposal.notes,
        example_class=example_class,
        example_tools=() if example_class is None else proposal.tool_names_by_class[example_class],
        example_tool_count=(
            0 if example_class is None else proposal.tool_counts_by_class[example_class]
        ),
    )


def apply_overrides(draft: InitDraft, sets: list[str]) -> InitDraft:
    """Apply `--set key=value` arguments in order; see SET_HELP for the keys.

    A value is checked by the same models as the configuration file, and a bad one raises
    OverrideError with the model's message.
    """
    for text in sets:
        draft = _apply_override(draft, text)
    return draft


def find_set_trace_format(sets: list[str]) -> TraceFormat | None:
    """The trace format the last `--set traces.format=...` names, or None when none names a
    known one; an unknown one is left for apply_overrides to report.
    """
    trace_format = None
    for text in sets:
        key, _, value = text.partition("=")
        if key == _TRACE_FORMAT_KEY:
            trace_format = value
    known: tuple[TraceFormat, ...] = get_args(TraceFormat)
    return next((name for name in known if name == trace_format), None)


def set_label(draft: InitDraft, area: str, label: str, verdict: Verdict) -> InitDraft:
    """Map `label` in `area` (label_map or agent_label_map), replacing other spellings of it.

    Unlike `--set`, the label may contain any character, so a label read from the input
    can always be mapped.
    """
    key = normalize_label(label)

    def others(labels: dict[str, Verdict]) -> dict[str, Verdict]:
        return {name: value for name, value in labels.items() if normalize_label(name) != key}

    def still_unmapped(labels: tuple[str, ...]) -> tuple[str, ...]:
        return tuple(name for name in labels if normalize_label(name) != key)

    if area == "label_map":
        return dataclasses.replace(
            draft,
            label_map={**others(draft.label_map), label: verdict},
            unmapped_analyst_labels=still_unmapped(draft.unmapped_analyst_labels),
            missing_required=tuple(
                name for name in draft.missing_required if name != REQUIRED_LABELS
            ),
        )
    return dataclasses.replace(
        draft,
        agent_label_map={**others(draft.agent_label_map), label: verdict},
        unmapped_agent_labels=still_unmapped(draft.unmapped_agent_labels),
    )


def render_config_yaml(draft: InitDraft) -> str:
    """detecttrace.yaml as text: a header comment, then the keys in a fixed order.

    Every string is double-quoted with only printable ASCII inside, so a label such as
    `yes`, `null`, `: x` or one with a line break keeps its meaning and its line.
    """
    return _render_config(draft, is_for_app=False)


def render_ui_config_yaml(draft: InitDraft) -> str:
    """The local app's detecttrace.yaml: as render_config_yaml, but without the traces,
    verdicts and output keys, and with a header for `detecttrace ui`.
    """
    return _render_config(draft, is_for_app=True)


def check_round_trip(text: str, draft: InitDraft) -> None:
    """Raise RoundTripError unless `text` loads, as check would load it, to `draft`'s values.

    The parsed document must equal the draft's exactly, spellings included, and must pass
    the configuration models.
    """
    _check_round_trip(text, _to_document(draft), to_run_config)


def check_ui_round_trip(text: str, draft: InitDraft) -> None:
    """Raise RoundTripError unless `text` loads, as the app would load it, to `draft`'s values
    without the traces, verdicts and output keys; otherwise as check_round_trip.
    """
    expected = {
        key: value for key, value in _to_document(draft).items() if key not in _APP_OMITTED_KEYS
    }
    _check_round_trip(text, expected, to_ui_config)


def choose_example_class(proposal: Proposal) -> str | None:
    """The alert class with the most cases among those with tool calls; ties by normalized name.

    A class without tool calls cannot give a valid checklist, which needs one item.
    """
    candidates = [
        alert_class
        for alert_class, tools in proposal.tool_names_by_class.items()
        if any(tool.strip() for tool in tools)
    ]
    if not candidates:
        return None
    return min(
        candidates,
        key=lambda name: (-proposal.case_counts_by_class[name], normalize_label(name), name),
    )


def to_checklist_file_name(alert_class: str) -> str:
    """A file name for the class's example checklist that cannot leave the checklists folder.

    The class is reduced to lowercase [a-z0-9_-], at most 64 characters, falling back to
    "example"; a name Windows reserves for devices (con, nul, com1, ...) gets "_checklist".
    """
    name = _to_safe_name(alert_class) or "example"
    if name in _WINDOWS_RESERVED:
        name += "_checklist"
    return name + EXAMPLE_SUFFIX


def render_example_checklist(alert_class: str, tool_names: tuple[str, ...], tool_count: int) -> str:
    """An inactive checklist with one item per tool name, most called first, and no argument
    rules; `tool_count` is the class's distinct tools, so a note can count those left out.
    """
    usable = [name for name in tool_names if name.strip()]
    if not usable:
        raise ValueError("an example checklist needs at least one tool name")
    file_name = to_checklist_file_name(alert_class)
    active_name = file_name.removesuffix(EXAMPLE_SUFFIX) + ".yaml"
    listed = usable[:MAX_EXAMPLE_ITEMS]
    lines = [
        f"# Example checklist written by `detecttrace init`. It is inactive until renamed to {active_name}.",
        "# It lists the tools the agent called for this alert class, most called first, so as written",
        "# it would score close to 100% evidence completeness. Keep only the calls the playbook",
        "# requires, add argument rules where a call must cover a range or a value, then rename.",
    ]
    if tool_count > len(listed):
        lines.append(f"# {tool_count - len(listed):,} more tools are not listed.")
    lines += [f"alert_class: {to_yaml_string(alert_class)}", "items:"]
    ids: set[str] = set()
    for tool in listed:
        item_id = _to_unique_id(_to_safe_name(tool) or "tool", ids)
        ids.add(item_id)
        lines += [f"  - id: {to_yaml_string(item_id)}", f"    tool: {to_yaml_string(tool)}"]
    return "\n".join(lines) + "\n"


def describe_field(name: str, field: FieldProposal, quote: Callable[[str], str]) -> str:
    """How init found the key of mapping field `name`, with the key written by `quote`; the
    same words in the terminal summary and in the configuration's header.
    """
    if field.value is None:
        return "not found" + (" (required)" if name in REQUIRED_FIELDS else "")
    unit = "tool calls" if name in _TOOL_FIELDS else "agent runs"
    return (
        f"{quote(field.value)}, found on {field.covered:,} of {field.total:,} {unit} "
        f"({field.source})"
    )


def to_yaml_string(text: str) -> str:
    """`text` as a YAML 1.2 double-quoted scalar made of printable ASCII only.

    It is JSON's escaping except above U+FFFF, where YAML's \\U escape is used: PyYAML reads
    a JSON surrogate-pair escape as two lone surrogates.
    """
    return '"' + _NEEDS_ESCAPE.sub(_escape, text) + '"'


def _escape(match: re.Match[str]) -> str:
    char = match[0]
    if char in '"\\':
        return "\\" + char
    code = ord(char)
    return f"\\u{code:04x}" if code <= 0xFFFF else f"\\U{code:08x}"


def _to_relative_text(path: Path, folder: Path) -> str:
    try:
        relative = os.path.relpath(path.absolute(), folder)
    except ValueError:
        # On Windows a path on another drive has no relative form.
        return path.absolute().as_posix()
    return PurePath(relative).as_posix()


def _apply_override(draft: InitDraft, text: str) -> InitDraft:
    key, is_split, value = text.partition("=")
    if not is_split:
        raise OverrideError(f"--set {_to_comment_text(text)}: expected key=value. {SET_HELP}")
    if _SURROGATE.search(text):
        raise OverrideError(f"--set {_to_comment_text(key)}: not valid text")
    area, _, label = key.partition(".")
    is_label_key = area in _LABEL_KEYS and "." in key
    if not is_label_key and key not in SET_KEYS:
        raise OverrideError(f"--set: unknown key '{_to_comment_text(key)}'. {SET_HELP}")

    document = _to_document(draft)
    if is_label_key:
        labels = {
            existing: verdict
            for existing, verdict in document.get(area, {}).items()
            if normalize_label(existing) != normalize_label(label)
        }
        document[area] = {**labels, label: value}
    else:
        parts = key.split(".")
        target = document
        for part in parts[:-1]:
            target = target.setdefault(part, {})
        target[parts[-1]] = _to_typed_value(value) if key in _NON_TEXT_KEYS else value
    try:
        config = RunConfig.model_validate(document)
    except ValidationError as error:
        problems = "; ".join(describe_validation_error(error))
        raise OverrideError(f"--set {_to_comment_text(key)}: {problems}") from None

    if is_label_key:
        return set_label(draft, area, label, Verdict(value))
    missing = tuple(name for name in draft.missing_required if name != _SATISFIES.get(key))
    coverage = dict(draft.coverage)
    if key.startswith("mapping.operation."):
        coverage["operation"] = "set with --set"
    elif key.startswith("mapping."):
        coverage[key.removeprefix("mapping.")] = "set with --set"
    return dataclasses.replace(
        draft,
        trace_format=config.traces.format,
        traces_path=document["traces"]["path"],
        verdicts_path=document["verdicts"]["path"],
        checklists_path=document.get("checklists"),
        output=document["output"],
        max_detail_cases=config.dashboard.max_detail_cases,
        mapping=config.mapping,
        missing_required=missing,
        coverage=coverage,
    )


def _to_typed_value(value: str) -> object:
    # Read as YAML would read it in the file, so `true` and `500` work; anything else goes
    # to the model as text and gets the model's message.
    try:
        return parse_yaml12(value, Path("--set"))
    except Yaml12Error:
        return value


def _to_document(draft: InitDraft) -> dict[str, Any]:
    """The data render_config_yaml writes, as the YAML loader returns it."""
    document: dict[str, Any] = {
        "traces": {"path": draft.traces_path, "format": draft.trace_format},
        "verdicts": {"path": draft.verdicts_path},
    }
    for name, labels in (
        ("label_map", draft.label_map),
        ("agent_label_map", draft.agent_label_map),
    ):
        if labels:
            document[name] = {label: verdict.value for label, verdict in labels.items()}
    if draft.checklists_path is not None:
        document["checklists"] = draft.checklists_path
    document["output"] = draft.output
    if draft.max_detail_cases != _DEFAULT_MAX_DETAIL_CASES:
        document["dashboard"] = {"max_detail_cases": draft.max_detail_cases}
    mapping = _changed_fields(draft.mapping, _DEFAULT_MAPPING, _MAPPING_FIELDS)
    operation = _changed_fields(
        draft.mapping.operation, _DEFAULT_MAPPING.operation, _OPERATION_FIELDS
    )
    if operation:
        mapping["operation"] = operation
    if mapping:
        document["mapping"] = mapping
    return document


def _changed_fields(
    model: MappingConfig | OperationConfig,
    default: MappingConfig | OperationConfig,
    names: tuple[str, ...],
) -> dict[str, Any]:
    return {
        name: getattr(model, name)
        for name in names
        if getattr(model, name) != getattr(default, name)
    }


def _render_config(draft: InitDraft, *, is_for_app: bool) -> str:
    lines = _render_header(draft, _UI_INTRO if is_for_app else _INIT_INTRO)
    if not is_for_app:
        lines += ["traces:", f"  path: {to_yaml_string(draft.traces_path)}"]
        lines.append(f"  format: {to_yaml_string(draft.trace_format)}")
        lines += ["verdicts:", f"  path: {to_yaml_string(draft.verdicts_path)}"]
    lines += _render_label_map(
        "label_map",
        draft.label_map,
        draft.unmapped_analyst_labels,
        "Analyst labels not mapped yet; check reports them as unmapped.",
    )
    lines += _render_label_map(
        "agent_label_map",
        draft.agent_label_map,
        draft.unmapped_agent_labels,
        "Agent labels that match no analyst label and are not mapped yet.",
    )
    if draft.checklists_path is not None:
        lines.append(f"checklists: {to_yaml_string(draft.checklists_path)}")
    if not is_for_app:
        lines.append(f"output: {to_yaml_string(draft.output)}")
    if draft.max_detail_cases != _DEFAULT_MAX_DETAIL_CASES:
        lines += ["dashboard:", f"  max_detail_cases: {draft.max_detail_cases}"]
    lines += _render_mapping(draft.mapping)
    return "\n".join(lines) + "\n"


def _check_round_trip(
    text: str, expected: dict[str, Any], validate: Callable[[object, Path], object]
) -> None:
    source = Path("detecttrace.yaml")
    if len(text.encode()) > MAX_CONFIG_BYTES:
        raise RoundTripError(f"rendered configuration is over {MAX_CONFIG_BYTES:,} bytes")
    try:
        document = parse_yaml12(text, source)
        validate(document, source)
    except (Yaml12Error, ConfigFileError) as error:
        raise RoundTripError(f"rendered configuration does not load: {error}") from None
    if document != expected:
        raise RoundTripError("rendered configuration loads to different values than proposed")


def _render_header(draft: InitDraft, intro: tuple[str, ...]) -> list[str]:
    lines = [
        *intro,
        "",
        f"Found: {draft.trace_format} traces, {draft.agent_run_count:,} agent runs.",
        *(f"  {name}: {text}" for name, text in draft.coverage.items()),
    ]
    if draft.missing_required:
        lines.append("Still missing: " + ", ".join(draft.missing_required))
    if draft.notes:
        lines += ["Notes:", *(f"  - {note}" for note in draft.notes)]
    if draft.example_class is not None and draft.checklists_path is not None:
        file_name = to_checklist_file_name(draft.example_class)
        lines += [
            "",
            f"{draft.checklists_path}/{file_name} is inactive until renamed to .yaml.",
            "It lists the tools the agent called for "
            f"{to_yaml_string(draft.example_class)}, most called first;",
            "keep only the calls the playbook requires, then rename it.",
        ]
    return [f"# {_to_comment_text(line)}".rstrip() for line in lines]


def _render_label_map(
    name: str, labels: dict[str, Verdict], unmapped: tuple[str, ...], unmapped_note: str
) -> list[str]:
    lines = [f"{name}:"] if labels else []
    for label, verdict in labels.items():
        lines += _render_entry(to_yaml_string(label), to_yaml_string(verdict.value))
    if unmapped:
        if not labels:
            lines.append(f"# {name}:")
        lines += [
            f"  # {unmapped_note}",
            "  # Uncomment each and set true_positive, false_positive or benign.",
        ]
        # Each label is one quoted, printable-ASCII token, so it cannot end the comment line.
        lines += [f'  # {to_yaml_string(label)}: ""' for label in unmapped]
    return lines


def _render_entry(key: str, value: str) -> list[str]:
    if len(key) > _MAX_IMPLICIT_KEY:
        return [f"  ? {key}", f"  : {value}"]
    return [f"  {key}: {value}"]


def _render_mapping(mapping: MappingConfig) -> list[str]:
    changed = _changed_fields(mapping, _DEFAULT_MAPPING, _MAPPING_FIELDS)
    operation = _changed_fields(mapping.operation, _DEFAULT_MAPPING.operation, _OPERATION_FIELDS)
    header = "mapping:" if changed or operation else "# mapping:"
    lines = [
        "# Where each value is read from. Commented keys use their default.",
        header,
    ]
    for name in _MAPPING_FIELDS:
        lines.append(_render_mapping_line(name, getattr(mapping, name), name not in changed, 2))
    lines.append("  operation:" if operation else "  # operation:")
    for name in _OPERATION_FIELDS:
        value = getattr(mapping.operation, name)
        lines.append(_render_mapping_line(name, value, name not in operation, 4))
    return lines


def _render_mapping_line(name: str, value: str | bool, is_default: bool, indent: int) -> str:
    text = ("true" if value else "false") if isinstance(value, bool) else to_yaml_string(value)
    return " " * indent + ("# " if is_default else "") + f"{name}: {text}"


def _to_comment_text(text: str) -> str:
    # PyYAML ends a comment at U+0085, U+2028 and U+2029 as well as CR and LF, and rejects
    # control characters outright, so a comment keeps printable ASCII only.
    return _NOT_PRINTABLE_ASCII.sub(lambda match: to_yaml_string(match[0])[1:-1], text)


def _to_safe_name(text: str) -> str:
    name = _UNSAFE_NAME_CHARS.sub("_", text.lower())
    name = _REPEATED_UNDERSCORES.sub("_", name).strip("_-.")
    return name[:MAX_FILE_NAME_LENGTH].strip("_-.")


def _to_unique_id(base: str, taken: set[str]) -> str:
    if base not in taken:
        return base
    number = 2
    while f"{base}_{number}" in taken:
        number += 1
    return f"{base}_{number}"
