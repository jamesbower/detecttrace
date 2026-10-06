"""Turn a file uploaded on the `detecttrace ui` Data page into stored input; no web framework.

Traces and verdicts go into the same store `detecttrace serve` fills, with tool results
dropped before anything is stored. A checklist is written into the checklists folder, one
file per alert class. Each upload either stores what it can and reports the rest, as `check`
would, or raises UploadRefused having stored nothing. All display text is formatted here.
"""

import re
from dataclasses import dataclass
from pathlib import Path

from detecttrace.checklist import MAX_FILE_BYTES, ChecklistFileError, load_checklists
from detecttrace.files import write_text_atomically
from detecttrace.init_writer import to_checklist_stem
from detecttrace.model import Issue
from detecttrace.serve.receiver import remove_tool_result
from detecttrace.serve.store import Store, TraceFamily
from detecttrace.summary import SummaryLine, summarize_issues
from detecttrace.traces import TraceFileError, detect_format, load_spans
from detecttrace.verdicts import VerdictFileError, read_verdicts

MAX_TRACE_FILE_BYTES = 256 << 20
MAX_VERDICT_FILE_BYTES = 64 << 20
MAX_CHECKLIST_FILE_BYTES = MAX_FILE_BYTES
MAX_CHECKLISTS = 100
TRACE_SUFFIXES = (".jsonl", ".json", ".jsonl.gz", ".json.gz", ".jsonl.zst", ".json.zst")
VERDICT_SUFFIXES = (".csv",)
CHECKLIST_SUFFIXES = (".yaml", ".yml")
# The token name put_verdicts records for rows that came from an uploaded file.
UPLOAD_TOKEN_NAME = "upload"

_MAX_NAME_LENGTH = 128
_UNSAFE_CHARACTERS = re.compile(r"[^A-Za-z0-9._-]")
_FAMILIES: dict[str, TraceFamily] = {
    "otlp_json": "otlp",
    "otlp_jsonl": "otlp",
    "langfuse": "langfuse",
}
_FAMILY_NAMES: dict[TraceFamily, str] = {"otlp": "OTLP", "langfuse": "Langfuse"}


class UploadRefused(Exception):
    """The upload was not used and nothing was stored; the message is shown on the upload card."""


@dataclass(frozen=True, slots=True)
class UploadReport:
    stored_text: str  # what was stored, such as "2,129 spans added; 3 duplicates dropped."
    problems: tuple[SummaryLine, ...]  # the issues in the file, as check reports them


def to_safe_upload_name(name: str, suffixes: tuple[str, ...]) -> str:
    """The upload's base name with every character outside [A-Za-z0-9._-] made "_".

    The name keeps its longest allowed suffix (any case) and is cut to 128 characters before
    it, and a leading "." becomes "_". Raises UploadRefused for an empty name, "." or "..",
    a name that is only the suffix, or one without an allowed suffix.
    """
    base_name = re.split(r"[/\\]", name)[-1]
    if base_name in ("", ".", ".."):
        raise UploadRefused("The file has no name. Rename it and upload it again.")
    safe_name = _UNSAFE_CHARACTERS.sub("_", base_name)
    matches = [suffix for suffix in suffixes if safe_name.lower().endswith(suffix)]
    if not matches:
        raise UploadRefused(f"Only files ending in {', '.join(suffixes)} can be uploaded here.")
    suffix_length = max(len(suffix) for suffix in matches)
    stem = safe_name[:-suffix_length]
    if not stem:
        raise UploadRefused("The file name is only a suffix. Rename it and upload it again.")
    # A leading "." would hide the file from the folder loaders, which skip hidden files.
    if stem.startswith("."):
        stem = "_" + stem[1:]
    return stem[: _MAX_NAME_LENGTH - suffix_length] + safe_name[-suffix_length:]


def store_trace_file(store: Store, file_path: Path) -> UploadReport:
    """Store the spans of one trace file, without tool results, and the issues found in it.

    Raises UploadRefused when the file holds no trace format we read, or a format other
    than the one already stored; the store is then unchanged.
    """
    try:
        trace_format, format_issues = detect_format(file_path)
    except TraceFileError as error:
        raise _to_refusal(error, file_path) from None
    if trace_format is None:
        raise UploadRefused(_describe_unreadable_trace_file(file_path.name, format_issues))
    family = _FAMILIES[trace_format]
    stored_family = store.read_trace_family()
    if stored_family is not None and stored_family != family:
        raise UploadRefused(
            f"This data folder holds {_FAMILY_NAMES[stored_family]} traces. "
            f"Clear the data to switch to {_FAMILY_NAMES[family]}."
        )
    try:
        spans, issues = load_spans(file_path, format=trace_format)
    except TraceFileError as error:
        raise _to_refusal(error, file_path) from None
    result = store.add_spans(
        [remove_tool_result(span) for span in spans],
        issues,
        subject=file_path.name,
        trace_family=family,
    )
    stored_text = _describe_counts(
        (result.accepted, "span added", "spans added"),
        (result.duplicates, "duplicate dropped", "duplicates dropped"),
        (result.conflicts, "conflicting span not stored", "conflicting spans not stored"),
    )
    return UploadReport(stored_text, tuple(summarize_issues(issues)))


def store_verdict_file(store: Store, file_path: Path) -> UploadReport:
    """Store each verdict row as its case's current verdict, and the issues found in the file.

    Raises UploadRefused when the file cannot be read as a verdict CSV at all.
    """
    try:
        rows, issues = read_verdicts(file_path)
    except VerdictFileError as error:
        raise _to_refusal(error, file_path) from None
    result = store.put_verdicts(rows, UPLOAD_TOKEN_NAME, issues=issues)
    stored_text = _describe_counts(
        (result.added, "verdict added", "verdicts added"),
        (result.replaced, "replaced", "replaced"),
        (result.unchanged, "unchanged", "unchanged"),
    )
    return UploadReport(stored_text, tuple(summarize_issues(issues)))


def save_checklist_file(file_path: Path, checklists_folder: Path) -> UploadReport:
    """Save one checklist into the folder, replacing any earlier file for the same alert class.

    Raises UploadRefused when the file is not a valid checklist, when the folder already
    holds MAX_CHECKLISTS checklists and this class is new, or when the file name for this
    class is taken by another class's checklist; the folder is then unchanged.
    """
    try:
        checklists = load_checklists(file_path)
    except ChecklistFileError as error:
        raise _to_refusal(error, file_path) from None
    # A single file holds exactly one checklist.
    (key, checklist), *_ = checklists.items()
    checklists_folder.mkdir(parents=True, exist_ok=True)
    existing = _list_checklist_files(checklists_folder)
    replaced = [path for path in existing if _read_class_key(path) == key]
    if len(existing) >= MAX_CHECKLISTS and not replaced:
        raise UploadRefused(
            f"The checklists folder already holds {MAX_CHECKLISTS} checklists. "
            "Replace one of them, or remove one, before adding another alert class."
        )
    target = checklists_folder / f"{to_checklist_stem(checklist.alert_class)}.yaml"
    if target.exists() and target not in replaced:
        raise UploadRefused(
            f"{target.name} already holds the checklist for another alert class. "
            "Remove it before adding this one."
        )
    write_text_atomically(file_path.read_text(encoding="utf-8-sig"), target)
    for path in replaced:
        if path != target:
            path.unlink()
    text = f"Checklist saved for alert class '{checklist.alert_class}'."
    if replaced:
        text += " It replaces the earlier one."
    return UploadReport(text, ())


def _to_refusal(error: Exception, file_path: Path) -> UploadRefused:
    """The loader's message with the file named only by its name, never the server's folder."""
    message = str(error)
    for form in (str(file_path.absolute()), str(file_path)):
        message = message.replace(form, file_path.name)
    return UploadRefused(message)


def _describe_unreadable_trace_file(name: str, issues: list[Issue]) -> str:
    lines = summarize_issues(issues)
    if not lines:
        return f"{name} is not a trace file we can read."
    reasons = " ".join(f"{line.message} {line.hint}" for line in lines)
    return f"{name} is not a trace file we can read. {reasons}"


def _describe_counts(*counts: tuple[int, str, str]) -> str:
    """One "count noun" part per (count, singular, plural); after the first, only non-zero ones."""
    first, *rest = counts
    kept = [first, *(item for item in rest if item[0])]
    return (
        "; ".join(
            f"{count:,} {singular if count == 1 else plural}" for count, singular, plural in kept
        )
        + "."
    )


def _list_checklist_files(folder: Path) -> list[Path]:
    return sorted(
        path
        for path in folder.iterdir()
        if not path.name.startswith(".")
        and path.name.lower().endswith(CHECKLIST_SUFFIXES)
        and path.is_file()
    )


def _read_class_key(path: Path) -> str | None:
    """The normalized alert class of the checklist in `path`, or None when it can't be read."""
    try:
        checklists = load_checklists(path)
    except ChecklistFileError:
        return None
    return next(iter(checklists), None)
