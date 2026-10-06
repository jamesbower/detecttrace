"""The local app's configuration: propose it from the stored input, and save the user's choice.

The proposal is the one `detecttrace init` makes from the same spans and verdict rows. The
user's edits arrive as an attribute key per mapping field and a verdict per label, never as
`--set` text: a field edit is measured by propose_init, so it reads "set by you" with its
coverage, and a label edit goes through set_label. Every sentence the form shows is
formatted here.
"""

import dataclasses
import os
from pathlib import Path

from detecttrace.config import Config
from detecttrace.files import write_text_atomically
from detecttrace.init_proposal import (
    REQUIRED_LABELS,
    Proposal,
    list_run_attribute_keys,
    propose_init,
)
from detecttrace.init_writer import (
    FIELD_NAMES,
    InitDraft,
    check_ui_round_trip,
    create_draft,
    describe_field,
    render_ui_config_yaml,
    set_label,
)
from detecttrace.model import Span, Verdict
from detecttrace.pipeline import load_run_checklists, to_source
from detecttrace.runconfig import TraceFormat, UiConfig, load_ui_config
from detecttrace.serve.recompute import RecomputeSettings
from detecttrace.serve.store import Store

CONFIG_NAME = "detecttrace.yaml"
CHECKLISTS_FOLDER = "checklists"
UI_TRACES_SOURCE = "uploaded traces"
UI_VERDICTS_SOURCE = "uploaded verdicts"

FIELD_LABELS = {
    "case_id": "Case ID",
    "alert_class": "Alert class",
    "verdict": "Agent verdict",
    "prompt_version": "Prompt version",
    "tool_name": "Tool name",
    "tool_arguments": "Tool arguments",
}
_LABEL_AREAS = ("label_map", "agent_label_map")
_CHECKLIST_SUFFIXES = (".yaml", ".yml")
# The store records only the family; every OTLP layout reads the same once stored.
_TRACE_FORMATS: dict[str, TraceFormat] = {"otlp": "otlp_jsonl", "langfuse": "langfuse"}


class UiConfigError(Exception):
    """The configuration can't be proposed or saved yet; the message is for the user."""


def build_proposal_content(
    store: Store,
    data_dir: Path,
    fields: dict[str, str],
    labels: dict[str, dict[str, Verdict]],
) -> dict[str, object]:
    """The configuration form's content: the proposal with `fields` and `labels` applied.

    `fields` maps a name in FIELD_NAMES to an attribute key; `labels` maps label_map or
    agent_label_map to {label: verdict}. Raises UiConfigError for an edit that can't apply
    or when traces or verdicts are not uploaded yet.
    """
    spans, proposal, draft = _create_ui_draft(store, data_dir, fields, labels)
    return {
        "fields": [
            {
                "name": name,
                "label": FIELD_LABELS[name],
                "value": getattr(proposal.mapping, name).value,
                "share_text": describe_field(name, getattr(proposal.mapping, name), str),
                "is_missing": name in draft.missing_required,
            }
            for name in FIELD_NAMES
        ],
        "suggestions": list(list_run_attribute_keys(spans, draft.mapping)),
        "label_map": {label: verdict.value for label, verdict in draft.label_map.items()},
        "agent_label_map": {
            label: verdict.value for label, verdict in draft.agent_label_map.items()
        },
        "unmapped_analyst_labels": list(draft.unmapped_analyst_labels),
        "unmapped_agent_labels": list(draft.unmapped_agent_labels),
        "verdict_choices": [verdict.value for verdict in Verdict],
        "notes": list(draft.notes),
        "missing_required": list(draft.missing_required),
        "missing_text": _describe_missing(draft.missing_required),
        "agent_run_count_text": _describe_agent_runs(draft.agent_run_count),
    }


def write_ui_config(
    store: Store,
    data_dir: Path,
    fields: dict[str, str],
    labels: dict[str, dict[str, Verdict]],
) -> UiConfig:
    """Save the proposal with the user's edits as `data_dir / CONFIG_NAME` and load it back.

    Raises UiConfigError, writing nothing, while a required field or label is still missing,
    or for any reason build_proposal_content would.
    """
    _, _, draft = _create_ui_draft(store, data_dir, fields, labels)
    if draft.missing_required:
        raise UiConfigError(_describe_missing(draft.missing_required))
    text = render_ui_config_yaml(draft)
    check_ui_round_trip(text, draft)
    config_path = data_dir / CONFIG_NAME
    write_text_atomically(text, config_path)
    return load_ui_config(config_path)


def to_recompute_settings(config: UiConfig, config_path: Path) -> RecomputeSettings:
    """The settings every app recompute uses; raises ChecklistFileError as serve's do.

    The app's own checklists folder holding no checklist gives none.
    """
    folder = config.checklists
    app_folder = config_path.absolute().parent / CHECKLISTS_FOLDER
    # The app's folder is empty until a checklist is uploaded; any other folder keeps the
    # loader's refusal of one without checklists, which catches a mistyped path.
    if folder == app_folder and not _has_checklists(app_folder):
        folder = None
    checklists, checklist_issues = load_run_checklists(folder, config_path)
    return RecomputeSettings(
        config=Config(
            mapping=config.mapping,
            label_map=config.label_map,
            agent_label_map=config.agent_label_map,
        ),
        # Uploaded files are complete, so no case waits for spans still on their way.
        settle_seconds=0,
        max_detail_cases=config.dashboard.max_detail_cases,
        checklists=checklists,
        checklist_issues=checklist_issues,
        config_name=config_path.name,
        checklist_source=None
        if folder is None
        else to_source(folder, config_path.absolute().parent),
        traces_source=UI_TRACES_SOURCE,
        verdicts_source=UI_VERDICTS_SOURCE,
        page_mode="ui",
    )


def _create_ui_draft(
    store: Store,
    data_dir: Path,
    fields: dict[str, str],
    labels: dict[str, dict[str, Verdict]],
) -> tuple[list[Span], Proposal, InitDraft]:
    _check_edits(fields, labels)
    inputs = store.read_inputs()
    if not inputs.spans or not inputs.verdict_rows:
        raise UiConfigError("Upload traces and verdicts first.")
    trace_format = _TRACE_FORMATS[store.read_trace_family() or "otlp"]
    proposal = propose_init(inputs.spans, trace_format, inputs.verdict_rows)
    if fields:
        mapping = proposal.mapping.to_mapping_config().model_copy(update=fields)
        proposal = propose_init(inputs.spans, trace_format, inputs.verdict_rows, mapping=mapping)
    config_path = data_dir / CONFIG_NAME
    draft = create_draft(
        proposal, config_path=config_path, traces_path=data_dir, verdicts_path=data_dir
    )
    # Always named, so a checklist uploaded after saving is used without saving again. The
    # app never writes an example checklist; checklists are uploaded.
    draft = dataclasses.replace(
        draft,
        checklists_path=CHECKLISTS_FOLDER,
        example_class=None,
        example_tools=(),
        example_tool_count=0,
    )
    for area, area_labels in labels.items():
        for label, verdict in area_labels.items():
            draft = set_label(draft, area, label, verdict)
    return inputs.spans, proposal, draft


def _check_edits(fields: dict[str, str], labels: dict[str, dict[str, Verdict]]) -> None:
    for name, key in fields.items():
        if name not in FIELD_NAMES:
            raise UiConfigError(f"Unknown field '{name}'.")
        if not key.strip():
            raise UiConfigError(f"Choose an attribute for {FIELD_LABELS[name]}.")
    for area in labels:
        if area not in _LABEL_AREAS:
            raise UiConfigError(f"Unknown label map '{area}'.")


def _has_checklists(folder: Path) -> bool:
    # The same files the checklist loader reads: hidden files and folders are skipped.
    for _, folder_names, file_names in os.walk(folder):
        folder_names[:] = [name for name in folder_names if not name.startswith(".")]
        if any(
            not name.startswith(".") and name.lower().endswith(_CHECKLIST_SUFFIXES)
            for name in file_names
        ):
            return True
    return False


def _describe_missing(missing: tuple[str, ...]) -> str | None:
    if not missing:
        return None
    field_labels = [FIELD_LABELS[name] for name in missing if name in FIELD_LABELS]
    steps = []
    if field_labels:
        noun = "an attribute" if len(field_labels) == 1 else "attributes"
        steps.append(f"choose {noun} for {' and '.join(field_labels)}")
    if REQUIRED_LABELS in missing:
        steps.append("map at least one analyst label to a verdict")
    sentence = " and ".join(steps)
    return f"Before saving, {sentence}."


def _describe_agent_runs(count: int) -> str:
    return f"{count:,} agent run{'' if count == 1 else 's'} found."
