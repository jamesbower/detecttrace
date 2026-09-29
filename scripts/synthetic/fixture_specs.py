"""Test fixtures: trace format variants of the demo, verdict CSV variants and edge cases.

Every fixture is a folder under tests/fixtures/ with a detecttrace.yaml, its input files and
an expected.json. expected.json holds only the result fields the fixture's rule affects
(`keep`) plus the full issue list, so a fixture fails only for its own reason; input that
cannot be used at all gives the error instead. generate.py writes them all.

Edge cases are tiny hand-shaped traces built by the functions below. The format variants
write the demo's own cases, and a few fixtures that need many cases come from scenario files.
"""

import csv
import io
import json
import os
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from datetime import datetime
from functools import cache
from pathlib import Path
from typing import Any

import generate

from detecttrace import conventions
from detecttrace.model import InputFileError

FIXTURE_ROOT = generate.REPO_ROOT / "tests" / "fixtures"
CONFIG_NAME = "detecttrace.yaml"

Files = dict[str, bytes]


@dataclass(frozen=True, slots=True)
class FixtureSpec:
    name: str  # folder under the fixture root, POSIX form
    keep: tuple[str, ...]  # dotted result paths; a path through a list applies to every element
    write: Callable[[Path], list[Path]]


FIXTURES: list[FixtureSpec] = []


def write_fixture(spec: FixtureSpec, root: Path) -> list[Path]:
    """Write one fixture's inputs under `root`, remove files it no longer has; return them sorted."""
    folder = root / spec.name
    written = spec.write(folder)
    kept = {path.absolute() for path in written}
    for path in sorted(folder.rglob("*")):
        if path.is_file() and path.name != generate.GOLDEN_NAME and path.absolute() not in kept:
            path.unlink()
    return sorted(written, key=lambda path: path.as_posix())


def update_fixture_golden(spec: FixtureSpec, root: Path) -> Path:
    folder = root / spec.name
    return generate.write_text(
        folder / generate.GOLDEN_NAME, generate.to_golden_text(check_fixture(folder, spec.keep))
    )


def check_fixture(folder: Path, keep: Sequence[str]) -> dict[str, object]:
    """Run the check on a fixture folder and reduce the outcome to what its golden file holds."""
    # Imported here so writing fixtures never depends on the pipeline being importable.
    from detecttrace.pipeline import run_check
    from detecttrace.runconfig import load_run_config

    config_path = folder.absolute() / CONFIG_NAME
    try:
        run = run_check(load_run_config(config_path), config_path)
    except InputFileError as error:
        # Messages name files by their full path; only the part inside the fixture is stable.
        message = str(error).replace(f"{folder.absolute()}{os.sep}", "")
        return {"keep": list(keep), "error": {"type": type(error).__name__, "message": message}}
    return {
        "keep": list(keep),
        "results": reduce_results(run.results, keep),
        "issues": [
            {"kind": issue.kind.value, "subject": issue.subject, "detail": issue.detail}
            for issue in run.issues
        ],
    }


def reduce_results(value: object, paths: Sequence[str]) -> object:
    """Keep only `paths` of `value`; a list keeps them in each element, and "" keeps everything."""
    if isinstance(value, list):
        return [reduce_results(item, paths) for item in value]
    if "" in paths or not isinstance(value, dict):
        return value
    wanted: dict[str, list[str]] = {}
    for path in paths:
        head, _, rest = path.partition(".")
        wanted.setdefault(head, []).append(rest)
    # Indexed, not .get(): a mistyped path must fail rather than keep nothing.
    return {key: reduce_results(value[key], rests) for key, rests in wanted.items()}


def _register(name: str, keep: Sequence[str], write: Callable[[Path], list[Path]]) -> None:
    FIXTURES.append(FixtureSpec(name, tuple(keep), write))


def _fixture(
    name: str, keep: Sequence[str]
) -> Callable[[Callable[[], Files]], Callable[[], Files]]:
    """Register a function that returns a small fixture's files by relative path."""

    def register(build: Callable[[], Files]) -> Callable[[], Files]:
        _register(name, keep, lambda folder: _write_files(folder, build()))
        return build

    return register


def _write_files(folder: Path, files: Mapping[str, bytes]) -> list[Path]:
    paths: list[Path] = []
    for name, data in files.items():
        path = folder / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(data)
        paths.append(path)
    return paths


# Result fields each group of fixtures keeps.
TOTALS_KEEP = ("totals",)
JOIN_KEEP = ("totals.coverage", "case_rows.columns.case_id")
CLASS_KEEP = (
    "classes.alert_class",
    "case_rows.strings",
    "case_rows.columns.case_id",
    "case_rows.columns.class_index",
)
DETAIL_KEEP = ("totals.coverage", "case_rows.columns.case_id", "case_detail")
LABEL_KEEP = (
    "totals.coverage",
    "case_rows.verdict_codes",
    "case_rows.columns.case_id",
    "case_rows.columns.analyst",
    "case_rows.columns.agent",
)
ATTRIBUTE_KEEP = (
    "case_rows.strings",
    "case_rows.columns.case_id",
    "case_rows.columns.class_index",
    "case_rows.columns.version",
    "case_rows.columns.agent",
)
VERSION_KEEP = (
    "totals.versions",
    "classes.alert_class",
    "classes.versions",
    "classes.shown_versions",
    "classes.other_versions",
    "classes.by_version.version",
    "classes.by_version.first_week",
    "classes.by_version.metrics.case_count",
    "classes.other.metrics.case_count",
)
EVIDENCE_KEEP = (
    "case_rows.checklists",
    "case_rows.strings",
    "case_rows.columns.case_id",
    "case_rows.columns.satisfied",
    "case_rows.columns.not_called_items",
    "case_rows.columns.wrong_argument_items",
    "case_rows.columns.failed_items",
)
TREND_KEEP = ("classes.trend.week", "classes.trend.scope", "classes.trend.version")
KAPPA_KEEP = ("classes.overall.case_count", "classes.overall.confusion", "classes.overall.kappa")
WEEK_KEEP = (
    "totals.period",
    "case_rows.strings",
    "case_rows.columns.case_id",
    "case_rows.columns.week",
)

# ---------------------------------------------------------------------------------------------
# Trace format variants: the demo's cases, written the way other exporters write them.
# ---------------------------------------------------------------------------------------------

_OPENINFERENCE_KIND_ATTRIBUTE = "openinference.span.kind"
_OPENINFERENCE_KINDS = {
    conventions.INVOKE_AGENT: "AGENT",
    conventions.EXECUTE_TOOL: "TOOL",
    "chat": "LLM",
}
# Attribute names from the OpenInference semantic conventions:
# https://github.com/Arize-ai/openinference/blob/main/spec/semantic_conventions.md
# A TOOL span carries its call arguments in `input.value` (with `input.mime_type`), while
# `tool.parameters` is the tool's parameter schema, not the arguments of this call.
_OPENINFERENCE_NAMES = {
    "detecttrace.case_id": "soc.case.id",
    "detecttrace.alert_class": "soc.alert.class",
    "detecttrace.verdict": "soc.agent.verdict",
    "detecttrace.prompt_version": "soc.prompt.version",
    conventions.TOOL_NAME: "tool.name",
    conventions.TOOL_CALL_ARGUMENTS: "input.value",
}
_JSON_SCHEMA_TYPES = {
    bool: "boolean",
    int: "integer",
    float: "number",
    str: "string",
    list: "array",
    dict: "object",
}


@cache
def _demo() -> tuple[generate.Scenario, tuple[Any, ...]]:
    # Cached: six variants share the cases, and the span dicts are never changed in place.
    scenario = generate.load_scenario("demo")
    return scenario, tuple(generate.make_cases(scenario))


def _write_demo_variant(
    folder: Path,
    write_traces: Callable[[generate.Scenario, Sequence[Any], Path], list[Path]],
    config: Mapping[str, object],
) -> list[Path]:
    scenario, cases = _demo()
    return [
        *write_traces(scenario, cases, folder / "traces"),
        generate.write_verdicts(cases, folder / "verdicts.csv"),
        *generate.write_checklists(scenario, folder / "checklists"),
        _write_config(folder, {**scenario.config, **config}),
    ]


def _write_config(folder: Path, config: Mapping[str, object]) -> Path:
    header = "# Test fixture configuration. Paths are relative to this file.\n"
    return generate.write_text(folder / CONFIG_NAME, header + generate.to_yaml(dict(config)))


def _to_openinference(span: dict[str, Any]) -> dict[str, Any]:
    attributes: list[dict[str, Any]] = []
    for item in span["attributes"]:
        if item["key"] == conventions.OPERATION_ATTRIBUTE:
            kind = _OPENINFERENCE_KINDS[item["value"]["stringValue"]]
            attributes.append(
                {"key": _OPENINFERENCE_KIND_ATTRIBUTE, "value": {"stringValue": kind}}
            )
        elif item["key"] == conventions.TOOL_CALL_ARGUMENTS:
            arguments = item["value"]["stringValue"]
            attributes += [
                {"key": "input.value", "value": {"stringValue": arguments}},
                {"key": "input.mime_type", "value": {"stringValue": "application/json"}},
                {"key": "tool.parameters", "value": {"stringValue": _to_schema(arguments)}},
            ]
        else:
            attributes.append({**item, "key": _OPENINFERENCE_NAMES.get(item["key"], item["key"])})
    # OpenInference names a span after the agent, tool or model alone.
    return {**span, "name": span["name"].partition(" ")[2], "attributes": attributes}


def _to_schema(arguments: str) -> str:
    properties = {
        name: {"type": _JSON_SCHEMA_TYPES[type(value)]}
        for name, value in json.loads(arguments).items()
    }
    return json.dumps({"type": "object", "properties": properties})


def _without_operation_name(span: dict[str, Any]) -> dict[str, Any]:
    attributes = [
        item for item in span["attributes"] if item["key"] != conventions.OPERATION_ATTRIBUTE
    ]
    return {**span, "attributes": attributes}


def _write_single_document(
    scenario: generate.Scenario, cases: Sequence[Any], folder: Path
) -> list[Path]:
    documents = generate.to_trace_documents(scenario, cases)
    merged = {
        "resourceSpans": [
            resource_spans
            for _, document in documents
            for resource_spans in document["resourceSpans"]
        ]
    }
    # Compressed because the pretty-printed demo is several MB; the reader decompresses first.
    text = json.dumps(merged, indent=1) + "\n"
    return [generate.write_compressed(folder / "traces.json.gz", text.encode("utf-8"), "gzip")]


_register(
    "formats/jsonl_rotated",
    TOTALS_KEEP,
    lambda folder: _write_demo_variant(
        folder,
        lambda scenario, cases, out: generate.write_traces(
            scenario, cases, out, compression="none"
        ),
        {},
    ),
)
_register(
    "formats/gzip",
    TOTALS_KEEP,
    lambda folder: _write_demo_variant(folder, generate.write_traces, {}),
)
_register(
    "formats/zstd",
    TOTALS_KEEP,
    lambda folder: _write_demo_variant(
        folder,
        lambda scenario, cases, out: generate.write_traces(
            scenario, cases, out, compression="zstd"
        ),
        {},
    ),
)
_register(
    "formats/single_document",
    TOTALS_KEEP,
    lambda folder: _write_demo_variant(
        folder,
        _write_single_document,
        {"traces": {"path": "traces/", "format": "otlp_json"}},
    ),
)
_register(
    "formats/openinference",
    TOTALS_KEEP,
    lambda folder: _write_demo_variant(
        folder,
        lambda scenario, cases, out: generate.write_traces(
            scenario, cases, out, transform=_to_openinference
        ),
        {
            "mapping": {
                "case_id": _OPENINFERENCE_NAMES["detecttrace.case_id"],
                "alert_class": _OPENINFERENCE_NAMES["detecttrace.alert_class"],
                "verdict": _OPENINFERENCE_NAMES["detecttrace.verdict"],
                "prompt_version": _OPENINFERENCE_NAMES["detecttrace.prompt_version"],
                "tool_name": _OPENINFERENCE_NAMES[conventions.TOOL_NAME],
                "tool_arguments": _OPENINFERENCE_NAMES[conventions.TOOL_CALL_ARGUMENTS],
                "operation": {
                    "attribute": _OPENINFERENCE_KIND_ATTRIBUTE,
                    "agent_value": _OPENINFERENCE_KINDS[conventions.INVOKE_AGENT],
                    "tool_value": _OPENINFERENCE_KINDS[conventions.EXECUTE_TOOL],
                },
            }
        },
    ),
)
_register(
    "formats/no_operation_name",
    TOTALS_KEEP,
    lambda folder: _write_demo_variant(
        folder,
        lambda scenario, cases, out: generate.write_traces(
            scenario, cases, out, transform=_without_operation_name
        ),
        {},
    ),
)


@_fixture("formats/console_exporter", TOTALS_KEEP)
def _console_exporter() -> Files:
    # The captured console output next to this folder is reused, not copied.
    config = {
        "traces": {"path": "../../console_exporter/console.json"},
        "verdicts": {"path": "verdicts.csv"},
        "label_map": {"TP": "true_positive"},
    }
    return {
        CONFIG_NAME: _config_text(config),
        "verdicts.csv": _csv([("CASE-1", "triage", "TP")]),
    }


# ---------------------------------------------------------------------------------------------
# Hand-shaped traces for verdict variants and edge cases.
# ---------------------------------------------------------------------------------------------

T0 = "2026-08-04T10:00:00Z"  # a Tuesday in ISO week 2026-W32
CLASS = "impossible_travel"
TOOL = "get_user_profile"
_AGENT = "triage-agent"
_NS = 1_000_000_000
_STATUS_ERROR = 2
_LABELS = {"TP": "true_positive", "FP": "false_positive", "Benign": "benign"}
_VERDICT_HEADER = ("case_id", "alert_class", "verdict")
_USER = "user0001@example.com"


def _ns(moment: str) -> int:
    parsed = datetime.fromisoformat(moment)
    return int(parsed.replace(microsecond=0).timestamp()) * _NS + parsed.microsecond * 1000


def _span(
    trace: int,
    span: int,
    name: str,
    *,
    parent: int | None = None,
    start: str = T0,
    offset: float = 0.0,
    seconds: float = 1.0,
    attributes: Mapping[str, object] | None = None,
    is_error: bool = False,
) -> dict[str, Any]:
    start_ns = _ns(start) + round(offset * _NS)
    return {
        "traceId": f"{trace:032x}",
        "spanId": f"{span:016x}",
        "parentSpanId": "" if parent is None else f"{parent:016x}",
        "name": name,
        "kind": 1,
        "startTimeUnixNano": str(start_ns),
        "endTimeUnixNano": str(start_ns + round(seconds * _NS)),
        "attributes": [
            {"key": key, "value": _to_any_value(value)} for key, value in (attributes or {}).items()
        ],
        "status": {"code": _STATUS_ERROR, "message": "call failed"} if is_error else {},
    }


def _to_any_value(value: object) -> dict[str, object]:
    if isinstance(value, bool):
        return {"boolValue": value}
    if isinstance(value, int):
        return {"intValue": str(value)}
    if isinstance(value, float):
        return {"doubleValue": value}
    if isinstance(value, list):
        return {"arrayValue": {"values": [_to_any_value(item) for item in value]}}
    return {"stringValue": value}


def _root(
    trace: int,
    case_id: object,
    *,
    span: int = 1,
    parent: int | None = None,
    start: str = T0,
    seconds: float = 60.0,
    alert_class: object = CLASS,
    verdict: object = "TP",
    version: object = "v1",
    extra: Mapping[str, object] | None = None,
) -> dict[str, Any]:
    """An agent span; a None value leaves its attribute out."""
    attributes = {
        conventions.OPERATION_ATTRIBUTE: conventions.INVOKE_AGENT,
        "detecttrace.case_id": case_id,
        "detecttrace.alert_class": alert_class,
        "detecttrace.verdict": verdict,
        "detecttrace.prompt_version": version,
        **(extra or {}),
    }
    return _span(
        trace,
        span,
        f"{conventions.INVOKE_AGENT} {_AGENT}",
        parent=parent,
        start=start,
        seconds=seconds,
        attributes={key: value for key, value in attributes.items() if value is not None},
    )


def _tool(
    trace: int,
    span: int,
    tool: str = TOOL,
    *,
    parent: int | None = 1,
    start: str = T0,
    arguments: object = None,
    is_error: bool = False,
    extra: Mapping[str, object] | None = None,
) -> dict[str, Any]:
    """A tool span starting `span` seconds after `start`, so span order is call order."""
    attributes: dict[str, object] = {
        conventions.OPERATION_ATTRIBUTE: conventions.EXECUTE_TOOL,
        conventions.TOOL_NAME: tool,
    }
    if arguments is not None:
        attributes[conventions.TOOL_CALL_ARGUMENTS] = (
            arguments if isinstance(arguments, str) else json.dumps(arguments, ensure_ascii=False)
        )
    attributes.update(extra or {})
    return _span(
        trace,
        span,
        f"{conventions.EXECUTE_TOOL} {tool}",
        parent=parent,
        start=start,
        offset=span,
        seconds=0.5,
        attributes=attributes,
        is_error=is_error,
    )


def _case(trace: int, case_id: object, **root: Any) -> list[dict[str, Any]]:
    """A case with one tool call."""
    start = root.get("start", T0)
    return [_root(trace, case_id, **root), _tool(trace, 2, start=start, arguments={"user": _USER})]


def _document(
    spans: Sequence[dict[str, Any]], resource: Mapping[str, object] | None = None
) -> dict[str, Any]:
    return {
        "resourceSpans": [
            {
                "resource": {
                    "attributes": [
                        {"key": key, "value": _to_any_value(value)}
                        for key, value in {"service.name": _AGENT, **(resource or {})}.items()
                    ]
                },
                "scopeSpans": [{"scope": {"name": "fixture"}, "spans": list(spans)}],
            }
        ]
    }


def _jsonl(*documents: dict[str, Any]) -> bytes:
    return "".join(
        json.dumps(document, separators=(",", ":"), ensure_ascii=False) + "\n"
        for document in documents
    ).encode("utf-8")


def _csv(
    rows: Sequence[Sequence[str]],
    header: Sequence[str] = _VERDICT_HEADER,
    line_end: str = "\n",
) -> bytes:
    buffer = io.StringIO()
    writer = csv.writer(buffer, lineterminator=line_end)
    writer.writerow(header)
    writer.writerows(rows)
    return buffer.getvalue().encode("utf-8")


def _config_text(config: Mapping[str, object]) -> bytes:
    header = "# Test fixture configuration. Paths are relative to this file.\n"
    return (header + generate.to_yaml(dict(config))).encode("utf-8")


def _files(
    spans: Sequence[dict[str, Any]],
    rows: Sequence[Sequence[str]],
    *,
    checklist: Sequence[Mapping[str, object]] = (),
    resource: Mapping[str, object] | None = None,
    config: Mapping[str, object] | None = None,
    traces: Files | None = None,
    verdicts: bytes | None = None,
) -> Files:
    """A fixture's files: one trace file of `spans` (or `traces`), the verdict CSV of `rows`
    (or `verdicts`), a checklist for CLASS when `checklist` lists items, and the configuration.
    """
    settings: dict[str, object] = {
        "traces": {"path": "traces/"},
        "verdicts": {"path": "verdicts.csv"},
        "label_map": _LABELS,
    }
    files: Files = (
        dict(traces)
        if traces is not None
        else {"traces/traces.jsonl": _jsonl(_document(spans, resource))}
    )
    files["verdicts.csv"] = verdicts if verdicts is not None else _csv(rows)
    if checklist:
        settings["checklists"] = "checklists/"
        files[f"checklists/{CLASS}.yaml"] = generate.to_yaml(
            {"alert_class": CLASS, "items": [dict(item) for item in checklist]}
        ).encode("utf-8")
    files[CONFIG_NAME] = _config_text({**settings, **(config or {})})
    return files


def _row(case_id: str, label: str = "TP", alert_class: str = CLASS) -> tuple[str, str, str]:
    return (case_id, alert_class, label)


# ----- Verdict CSV variants -----------------------------------------------------------------

_VERDICT_TRACES = [
    *_case(1, "C-1"),
    *_case(2, "C-2", verdict="FP"),
    *_case(3, "C-3", verdict="Benign"),
    *_case(4, "C-4", verdict="Benign"),
]


@_fixture("verdicts/label_case_and_whitespace", LABEL_KEEP)
def _label_case_and_whitespace() -> Files:
    return _files(
        _VERDICT_TRACES,
        [
            _row("C-1", "Malicious"),
            _row("C-2", "fp"),
            _row("C-3", "closed - benign"),
            _row("C-4", "Closed -  Benign "),
        ],
        config={
            "label_map": {**_LABELS, "Malicious": "true_positive", "Closed - Benign": "benign"}
        },
    )


@_fixture("verdicts/colliding_label_map_keys", LABEL_KEEP)
def _colliding_label_map_keys() -> Files:
    return _files(
        _VERDICT_TRACES,
        [_row("C-1")],
        config={
            "label_map": {
                **_LABELS,
                "Closed - Benign": "benign",
                "closed  -  BENIGN": "false_positive",
            }
        },
    )


@_fixture("verdicts/agent_label_map", LABEL_KEEP)
def _agent_label_map() -> Files:
    return _files(
        [
            *_case(1, "C-1", verdict="Escalate"),
            *_case(2, "C-2", verdict="Close - False Positive"),
            *_case(3, "C-3", verdict="close - no threat"),
            *_case(4, "C-4", verdict="Benign"),
        ],
        [_row("C-1"), _row("C-2", "FP"), _row("C-3", "Benign"), _row("C-4", "Benign")],
        config={
            "agent_label_map": {
                "Escalate": "true_positive",
                "Close - False Positive": "false_positive",
                "Close - No Threat": "benign",
            }
        },
    )


@_fixture("verdicts/unmapped_labels", LABEL_KEEP)
def _unmapped_labels() -> Files:
    return _files(
        [*_case(1, "C-1"), *_case(2, "C-2", verdict="Needs review")],
        [_row("C-1", "Escalated"), _row("C-2", "FP")],
    )


@_fixture("verdicts/missing_required_column", LABEL_KEEP)
def _missing_required_column() -> Files:
    return _files(
        _VERDICT_TRACES,
        [],
        verdicts=_csv([("C-1", CLASS, "TP")], header=("case_id", "alert_class", "disposition")),
    )


@_fixture("verdicts/extra_columns", LABEL_KEEP)
def _extra_columns() -> Files:
    header = ("ticket", "case_id", "opened_at", "alert_class", "verdict", "analyst", "notes")
    rows = [
        ("T-100", "C-1", "2026-08-04T09:00:00Z", CLASS, "TP", "analyst1", "Reset, then revoked"),
        ("T-101", "C-2", "2026-08-04T09:05:00Z", CLASS, "FP", "analyst2", "VPN exit\nknown"),
        ("T-102", "C-3", "2026-08-04T09:10:00Z", CLASS, "Benign", "analyst1", ""),
    ]
    return _files(_VERDICT_TRACES[:6], [], verdicts=_csv(rows, header=header))


@_fixture("verdicts/excel_csv", LABEL_KEEP)
def _excel_csv() -> Files:
    # Excel's "CSV UTF-8": a byte-order mark and Windows line endings.
    rows = [_row("C-1"), _row("C-2", "FP"), _row("C-3", "Benign"), _row("C-4", "Benign")]
    return _files(_VERDICT_TRACES, [], verdicts=b"\xef\xbb\xbf" + _csv(rows, line_end="\r\n"))


@_fixture("verdicts/timestamp_offsets", LABEL_KEEP)
def _timestamp_offsets() -> Files:
    rows = [
        ("C-1", CLASS, "TP", "2026-08-04T12:00:00+02:00"),
        ("C-2", CLASS, "FP", "2026-08-04T05:00:00-05:00"),
        ("C-3", CLASS, "Benign", "2026-08-04T10:00:00Z"),
        ("C-4", CLASS, "Benign", "2026-08-04T15:30:00+05:30"),
    ]
    return _files(_VERDICT_TRACES, [], verdicts=_csv(rows, header=(*_VERDICT_HEADER, "closed_at")))


# ----- Joining -------------------------------------------------------------------------------


@_fixture("edge/joining/root_without_case_id", JOIN_KEEP)
def _root_without_case_id() -> Files:
    return _files([*_case(1, "C-1"), _root(2, None)], [_row("C-1")])


@_fixture("edge/joining/verdict_without_trace", JOIN_KEEP)
def _verdict_without_trace() -> Files:
    return _files(_case(1, "C-1"), [_row("C-1"), _row("C-2")])


@_fixture("edge/joining/trace_without_verdict", JOIN_KEEP)
def _trace_without_verdict() -> Files:
    return _files([*_case(1, "C-1"), *_case(2, "C-2")], [_row("C-1")])


@_fixture("edge/joining/alert_class_conflict", CLASS_KEEP)
def _alert_class_conflict() -> Files:
    return _files(
        [*_case(1, "C-1"), *_case(2, "C-2")],
        [_row("C-1", alert_class="oauth_consent"), _row("C-2")],
    )


@_fixture("edge/joining/alert_class_case_and_whitespace", CLASS_KEEP)
def _alert_class_case_and_whitespace() -> Files:
    return _files(
        _case(1, "C-1", alert_class=" impossible  TRAVEL "),
        [_row("C-1", alert_class="Impossible Travel")],
    )


# ----- Case structure ------------------------------------------------------------------------
# The analyst disagrees with the agent on every case, so each case lists its tool calls.


@_fixture("edge/case_structure/duplicate_roots", DETAIL_KEEP)
def _duplicate_roots() -> Files:
    return _files(
        [
            _root(1, "C-1"),
            _tool(1, 2, "get_signin_logs"),
            _root(2, "C-1", start="2026-08-04T11:00:00Z"),
            _tool(2, 2, "check_mfa_status", start="2026-08-04T11:00:00Z"),
        ],
        [_row("C-1", "FP")],
    )


@_fixture("edge/case_structure/duplicate_roots_same_start", DETAIL_KEEP)
def _duplicate_roots_same_start() -> Files:
    return _files(
        [
            _root(1, "C-1", seconds=120),
            _tool(1, 2, "get_signin_logs"),
            _root(2, "C-1", seconds=60),
            _tool(2, 2, "check_mfa_status"),
        ],
        [_row("C-1", "FP")],
    )


@_fixture("edge/case_structure/duplicate_roots_same_start_and_end", DETAIL_KEEP)
def _duplicate_roots_same_start_and_end() -> Files:
    # Every time ties, so the higher trace ID wins.
    return _files(
        [
            _root(1, "C-1"),
            _tool(1, 2, "get_signin_logs"),
            _root(2, "C-1"),
            _tool(2, 2, "check_mfa_status"),
        ],
        [_row("C-1", "FP")],
    )


@_fixture("edge/case_structure/several_roots_in_one_trace", DETAIL_KEEP)
def _several_roots_in_one_trace() -> Files:
    return _files(
        [
            _root(1, "C-1"),
            _tool(1, 2, "get_signin_logs"),
            _root(1, "C-2", span=10),
            _tool(1, 11, "check_mfa_status", parent=10),
        ],
        [_row("C-1", "FP"), _row("C-2", "FP")],
    )


@_fixture("edge/case_structure/nested_sub_agent_without_case_id", DETAIL_KEEP)
def _nested_sub_agent_without_case_id() -> Files:
    return _files(
        [
            _root(1, "C-1"),
            _tool(1, 2, "get_signin_logs"),
            _root(1, None, span=3, parent=1, alert_class=None, verdict=None, version=None),
            _tool(1, 4, "get_ip_reputation", parent=3),
        ],
        [_row("C-1", "FP")],
    )


@_fixture("edge/case_structure/nested_same_case_id", DETAIL_KEEP)
def _nested_same_case_id() -> Files:
    return _files(
        [
            _root(1, "C-1"),
            _tool(1, 2, "get_signin_logs"),
            _root(1, "C-1", span=3, parent=1, verdict=None),
            _tool(1, 4, "get_ip_reputation", parent=3),
        ],
        [_row("C-1", "FP")],
    )


@_fixture("edge/case_structure/nested_different_case_id", DETAIL_KEEP)
def _nested_different_case_id() -> Files:
    return _files(
        [
            _root(1, "C-1"),
            _tool(1, 2, "get_signin_logs"),
            _root(1, "C-2", span=3, parent=1),
            _tool(1, 4, "get_ip_reputation", parent=3),
        ],
        [_row("C-1", "FP"), _row("C-2", "FP")],
    )


@_fixture("edge/case_structure/tool_spans_outside_any_root", DETAIL_KEEP)
def _tool_spans_outside_any_root() -> Files:
    return _files(
        [
            _root(1, "C-1"),
            _tool(1, 2, "get_signin_logs"),
            _tool(2, 1, "get_ip_reputation", parent=None),
            _tool(2, 2, "check_mfa_status", parent=None),
        ],
        [_row("C-1", "FP")],
    )


# ----- Loading -------------------------------------------------------------------------------


@_fixture("edge/loading/trace_split_across_rotated_files", DETAIL_KEEP)
def _trace_split_across_rotated_files() -> Files:
    older = [_tool(1, 2, "get_signin_logs")]
    live = [_tool(1, 3, "check_mfa_status"), _root(1, "C-1")]
    return _files(
        [],
        [_row("C-1", "FP")],
        traces={
            "traces/traces-2026-08-04T10-00-03.000.jsonl": _jsonl(_document(older)),
            "traces/traces.jsonl": _jsonl(_document(live)),
        },
    )


@_fixture("edge/loading/same_span_in_two_files", DETAIL_KEEP)
def _same_span_in_two_files() -> Files:
    tool = _tool(1, 2, "get_signin_logs")
    return _files(
        [],
        [_row("C-1", "FP")],
        traces={
            "traces/traces-2026-08-04T10-00-03.000.jsonl": _jsonl(_document([tool])),
            "traces/traces.jsonl": _jsonl(_document([tool, _root(1, "C-1")])),
        },
    )


@_fixture("edge/loading/tool_span_with_missing_parent", DETAIL_KEEP)
def _tool_span_with_missing_parent() -> Files:
    return _files(
        [
            _root(1, "C-1"),
            _tool(1, 2, "get_signin_logs"),
            _tool(1, 3, "check_mfa_status", parent=9),
        ],
        [_row("C-1", "FP")],
    )


@_fixture("edge/loading/incomplete_trace", DETAIL_KEEP)
def _incomplete_trace() -> Files:
    return _files(
        [
            _root(1, "C-1"),
            _tool(1, 2, "get_signin_logs"),
            _tool(1, 3, "check_mfa_status", parent=None),
        ],
        [_row("C-1", "FP")],
    )


# ----- Attributes ----------------------------------------------------------------------------


@_fixture("edge/attributes/class_and_version_on_resource_only", ATTRIBUTE_KEEP)
def _class_and_version_on_resource_only() -> Files:
    return _files(
        _case(1, "C-1", alert_class=None, version=None),
        [_row("C-1")],
        resource={"detecttrace.alert_class": CLASS, "detecttrace.prompt_version": "v7"},
    )


def _version_on_child(lookup: str | None) -> Files:
    return _files(
        [
            _root(1, "C-1", version=None),
            _tool(1, 2, extra={"detecttrace.prompt_version": "v2"}),
        ],
        [_row("C-1")],
        config=None if lookup is None else {"mapping": {"prompt_version_lookup": lookup}},
    )


@_fixture("edge/attributes/version_on_child_span_only", ATTRIBUTE_KEEP)
def _version_on_child_span_only() -> Files:
    return _version_on_child(None)


@_fixture("edge/attributes/version_on_child_span_descendant_lookup", ATTRIBUTE_KEEP)
def _version_on_child_span_descendant_lookup() -> Files:
    return _version_on_child("descendant")


@_fixture("edge/attributes/different_versions_on_descendants", ATTRIBUTE_KEEP)
def _different_versions_on_descendants() -> Files:
    return _files(
        [
            _root(1, "C-1", version=None),
            _tool(1, 2, extra={"detecttrace.prompt_version": "v2"}),
            _tool(1, 3, extra={"detecttrace.prompt_version": "v3"}),
        ],
        [_row("C-1")],
        config={"mapping": {"prompt_version_lookup": "descendant"}},
    )


@_fixture("edge/attributes/integer_float_and_boolean_values", ATTRIBUTE_KEEP)
def _integer_float_and_boolean_values() -> Files:
    return _files(
        [*_case(1, 1001, version=3), *_case(2, "C-2", version=2.5), *_case(3, "C-3", version=True)],
        [_row("1001"), _row("C-2"), _row("C-3")],
    )


@_fixture("edge/attributes/array_value", ATTRIBUTE_KEEP)
def _array_value() -> Files:
    return _files(_case(1, "C-1", version=["v1", "v2"]), [_row("C-1")])


@_fixture("edge/attributes/empty_string", ATTRIBUTE_KEEP)
def _empty_string() -> Files:
    # An empty class on the root falls back to the resource; an empty verdict is missing.
    return _files(
        _case(1, "C-1", alert_class="", verdict="", version=""),
        [_row("C-1")],
        resource={"detecttrace.alert_class": CLASS},
    )


@_fixture("edge/attributes/surrounding_whitespace", ATTRIBUTE_KEEP)
def _surrounding_whitespace() -> Files:
    return _files(
        _case(1, "  C-1 ", alert_class=f" {CLASS}\t", verdict=" TP ", version=" v1 "),
        [_row("C-1")],
    )


# ----- Versions ------------------------------------------------------------------------------


@_fixture("edge/versions/no_version", VERSION_KEEP)
def _no_version() -> Files:
    return _files(
        [
            *_case(1, "C-1"),
            *_case(2, "C-2", version=None, start="2026-08-04T11:00:00Z"),
            *_case(3, "C-3", version=None, start="2026-08-11T11:00:00Z"),
        ],
        [_row("C-1"), _row("C-2"), _row("C-3")],
    )


@_fixture("edge/versions/ab_split", VERSION_KEEP)
def _ab_split() -> Files:
    starts = ("2026-08-04T10:00:00Z", "2026-08-05T10:00:00Z", "2026-08-11T10:00:00Z")
    spans = [
        span
        for number, start in enumerate(starts, start=1)
        for span in (
            *_case(number * 2 - 1, f"A-{number}", start=start, version="v1"),
            *_case(number * 2, f"B-{number}", start=start, version="v2"),
        )
    ]
    rows = [_row(f"{arm}-{number}") for number in range(1, 4) for arm in ("A", "B")]
    return _files(spans, rows)


def _scenario_fixture(name: str, scenario: str, keep: Sequence[str]) -> None:
    _register(
        name, keep, lambda folder: generate.generate(generate.load_scenario(scenario), folder)
    )


_scenario_fixture("edge/versions/rollback", "versions_rollback", (*VERSION_KEEP, *TREND_KEEP))
_scenario_fixture("edge/versions/more_than_six", "versions_more_than_six", VERSION_KEEP)


# ----- Checklist rules -----------------------------------------------------------------------


def _evidence_files(
    tool: str,
    items: Sequence[Mapping[str, object]],
    arguments: Sequence[object],
) -> Files:
    """One case per argument value, each calling `tool` once with it."""
    spans = [
        span
        for number, value in enumerate(arguments, start=1)
        for span in (_root(number, f"C-{number}"), _tool(number, 2, tool, arguments=value))
    ]
    rows = [_row(f"C-{number}") for number in range(1, len(arguments) + 1)]
    return _files(spans, rows, checklist=items)


@_fixture("edge/checklist/start_end_under_a_prefix_path", EVIDENCE_KEEP)
def _start_end_under_a_prefix_path() -> Files:
    return _evidence_files(
        "get_oauth_grants",
        [
            {
                "id": "grant_window",
                "tool": "get_oauth_grants",
                "args": {"window": {"min_duration": "24h", "start": "from", "end": "to"}},
            }
        ],
        [
            {"window": {"from": "2026-08-02T10:00:00Z", "to": "2026-08-04T10:00:00Z"}},
            {"window": {"from": "2026-08-04T00:00:00Z", "to": "2026-08-04T10:00:00Z"}},
            {"window": {"from": "2026-08-03T12:00:00+02:00", "to": "2026-08-04T10:00:00Z"}},
        ],
    )


@_fixture("edge/checklist/start_end_at_the_top_level", EVIDENCE_KEEP)
def _start_end_at_the_top_level() -> Files:
    return _evidence_files(
        "get_oauth_grants",
        [
            {
                "id": "grant_window",
                "tool": "get_oauth_grants",
                "args": {"$": {"min_duration": "24h", "start": "start", "end": "end"}},
            }
        ],
        [
            {"start": "2026-08-03T10:00:00Z", "end": "2026-08-04T10:00:00Z"},
            {"start": "2026-08-04T04:00:00Z", "end": "2026-08-04T10:00:00Z"},
            {"start": "2026-08-03T10:00:00", "end": "2026-08-04T10:00:00Z"},
        ],
    )


def _query_item(rule: Mapping[str, object]) -> list[dict[str, object]]:
    return [{"id": "audit_query", "tool": "query_sentinel", "args": {"query": dict(rule)}}]


@_fixture("edge/checklist/matches_in_the_middle_of_a_string", EVIDENCE_KEEP)
def _matches_in_the_middle_of_a_string() -> Files:
    return _evidence_files(
        "query_sentinel",
        _query_item({"matches": "Consent to application"}),
        [
            {"query": "AuditLogs | where OperationName == 'Consent to application' | take 10"},
            {"query": "AuditLogs | where OperationName == 'consent to application'"},
        ],
    )


@_fixture("edge/checklist/matches_ignore_case_flag", EVIDENCE_KEEP)
def _matches_ignore_case_flag() -> Files:
    return _evidence_files(
        "query_sentinel",
        _query_item({"matches": "(?i)consent to application"}),
        [
            {"query": "AuditLogs | where OperationName == 'CONSENT TO APPLICATION'"},
            {"query": "AuditLogs | where OperationName == 'Add member to role'"},
        ],
    )


@_fixture("edge/checklist/kql_min_ago", EVIDENCE_KEEP)
def _kql_min_ago() -> Files:
    return _evidence_files(
        "query_sentinel",
        _query_item({"kql_min_ago": "24h"}),
        [
            {"query": "SigninLogs | where TimeGenerated > ago(1d)"},
            {"query": "SigninLogs | where TimeGenerated > ago(24h)"},
            {"query": "SigninLogs | where TimeGenerated > ago(1.5d)"},
            {"query": "SigninLogs | where TimeGenerated > ago(90m)"},
            {"query": "SigninLogs | where TimeGenerated between (ago(7d) .. ago(1h))"},
            {"query": "SigninLogs | where TimeGenerated > ago(lookback)"},
        ],
    )


@_fixture("edge/checklist/equals_string_and_number", EVIDENCE_KEEP)
def _equals_string_and_number() -> Files:
    return _evidence_files(
        "get_signin_logs",
        [
            {"id": "hours_text", "tool": "get_signin_logs", "args": {"hours": {"equals": "24"}}},
            {"id": "hours_number", "tool": "get_signin_logs", "args": {"hours": {"equals": 24}}},
        ],
        [{"hours": 24}, {"hours": 24.0}, {"hours": "24"}],
    )


@_fixture("edge/checklist/in_with_mixed_types", EVIDENCE_KEEP)
def _in_with_mixed_types() -> Files:
    return _evidence_files(
        "get_signin_logs",
        [
            {
                "id": "lookback",
                "tool": "get_signin_logs",
                "args": {"range": {"in": [24, "24h", True]}},
            }
        ],
        [
            {"range": 24},
            {"range": 24.0},
            {"range": "24h"},
            {"range": True},
            {"range": "24"},
            {"range": 1},
        ],
    )


# ----- Tool calls ----------------------------------------------------------------------------

_MFA_ITEM = [{"id": "mfa_check", "tool": "check_mfa_status"}]


@_fixture("edge/tool_calls/failed_with_error_status", (*EVIDENCE_KEEP, "case_detail"))
def _failed_with_error_status() -> Files:
    return _files(
        [
            _root(1, "C-1"),
            _tool(1, 2, "check_mfa_status", arguments={"user": _USER}, is_error=True),
        ],
        [_row("C-1")],
        checklist=_MFA_ITEM,
    )


@_fixture("edge/tool_calls/failed_with_error_type", (*EVIDENCE_KEEP, "case_detail"))
def _failed_with_error_type() -> Files:
    return _files(
        [
            _root(1, "C-1"),
            _tool(
                1,
                2,
                "check_mfa_status",
                arguments={"user": _USER},
                extra={conventions.ERROR_TYPE: "timeout"},
            ),
        ],
        [_row("C-1")],
        checklist=_MFA_ITEM,
    )


@_fixture("edge/tool_calls/failed_then_retried", (*EVIDENCE_KEEP, "case_detail"))
def _failed_then_retried() -> Files:
    return _files(
        [
            _root(1, "C-1"),
            _tool(1, 2, "check_mfa_status", arguments={"user": _USER}, is_error=True),
            _tool(1, 3, "check_mfa_status", arguments={"user": _USER}),
        ],
        [_row("C-1")],
        checklist=_MFA_ITEM,
    )


# ----- Arguments -----------------------------------------------------------------------------


@_fixture("edge/arguments/invalid_json", EVIDENCE_KEEP)
def _invalid_json() -> Files:
    return _evidence_files(
        "get_signin_logs",
        [{"id": "signins", "tool": "get_signin_logs", "args": {"user": {"exists": True}}}],
        ['{"user": "user0001@example.com", "range": ', {"user": _USER}],
    )


@_fixture("edge/arguments/nested_path", EVIDENCE_KEEP)
def _nested_path() -> Files:
    return _evidence_files(
        "get_signin_logs",
        [
            {
                "id": "signins",
                "tool": "get_signin_logs",
                "args": {"filter.user.domain": {"equals": "example.com"}},
            }
        ],
        [
            {"filter": {"user": {"domain": "example.com"}}},
            {"filter": {"user": "user0001@example.com"}},
        ],
    )


@_fixture("edge/arguments/list_index", EVIDENCE_KEEP)
def _list_index() -> Files:
    return _evidence_files(
        "query_sentinel",
        [
            {
                "id": "audit_query",
                "tool": "query_sentinel",
                "args": {"filters[1].field": {"equals": "UserPrincipalName"}},
            }
        ],
        [
            {"filters": [{"field": "TimeGenerated"}, {"field": "UserPrincipalName"}]},
            {"filters": [{"field": "UserPrincipalName"}]},
        ],
    )


@_fixture("edge/arguments/durations", EVIDENCE_KEEP)
def _durations() -> Files:
    return _evidence_files(
        "get_signin_logs",
        [{"id": "signins", "tool": "get_signin_logs", "args": {"range": {"min_duration": "24h"}}}],
        [{"range": text} for text in ("24h", "P1D", "PT24H", "-24h", "PT12H")],
    )


@_fixture("edge/arguments/unreadable_duration", EVIDENCE_KEEP)
def _unreadable_duration() -> Files:
    return _evidence_files(
        "get_signin_logs",
        [{"id": "signins", "tool": "get_signin_logs", "args": {"range": {"min_duration": "24h"}}}],
        [{"range": "one day"}, {"range": "48h"}],
    )


# ----- Kappa ---------------------------------------------------------------------------------


def _kappa_files(pairs: Sequence[tuple[str, str]]) -> Files:
    """One case per (analyst label, agent label) pair."""
    spans = [
        span
        for number, (_, agent) in enumerate(pairs, start=1)
        for span in _case(number, f"C-{number}", verdict=agent)
    ]
    rows = [_row(f"C-{number}", analyst) for number, (analyst, _) in enumerate(pairs, start=1)]
    return _files(spans, rows)


@_fixture("edge/kappa/all_benign", KAPPA_KEEP)
def _all_benign() -> Files:
    return _kappa_files([("Benign", "Benign")] * 4)


@_fixture("edge/kappa/fewer_than_ten_cases", KAPPA_KEEP)
def _fewer_than_ten_cases() -> Files:
    return _kappa_files(
        [
            ("TP", "TP"),
            ("TP", "Benign"),
            ("FP", "FP"),
            ("FP", "Benign"),
            ("Benign", "Benign"),
            ("Benign", "Benign"),
            ("Benign", "FP"),
        ]
    )


@_fixture("edge/kappa/degenerate_bootstrap_resamples", KAPPA_KEEP)
def _degenerate_bootstrap_resamples() -> Files:
    # About a third of resamples draw only the benign cases, so their kappa is undefined.
    return _kappa_files([("TP", "TP")] + [("Benign", "Benign")] * 4)


# ----- Weeks ---------------------------------------------------------------------------------


def _week_files(starts: Sequence[str]) -> Files:
    spans = [
        span
        for number, start in enumerate(starts, start=1)
        for span in _case(number, f"C-{number}", start=start)
    ]
    return _files(spans, [_row(f"C-{number}") for number in range(1, len(starts) + 1)])


@_fixture("edge/weeks/sunday_and_monday_boundary", WEEK_KEEP)
def _sunday_and_monday_boundary() -> Files:
    return _week_files(["2026-08-09T23:59:59Z", "2026-08-10T00:00:00Z"])


@_fixture("edge/weeks/iso_week_53", WEEK_KEEP)
def _iso_week_53() -> Files:
    return _week_files(["2026-12-31T12:00:00Z", "2027-01-03T23:00:00Z", "2027-01-04T00:00:00Z"])


@_fixture("edge/weeks/time_zone_offsets", WEEK_KEEP)
def _time_zone_offsets() -> Files:
    # Written from local times; weeks follow UTC, so the first is a Sunday, the second a Monday.
    return _week_files(["2026-08-10T01:30:00+02:00", "2026-08-09T20:00:00-05:00"])


# ----- OTLP details --------------------------------------------------------------------------


@_fixture(
    "edge/otlp/nanosecond_timestamps", (*DETAIL_KEEP, "case_rows.strings", "case_rows.columns.week")
)
def _nanosecond_timestamps() -> Files:
    root = _root(1, "C-1", start="2026-08-09T23:59:59.999999Z")
    tool = _tool(1, 2, "get_signin_logs", arguments={"user": _USER})
    tool["startTimeUnixNano"] = str(_ns("2026-08-09T23:59:59.999999Z") + 999)
    tool["endTimeUnixNano"] = str(int(tool["startTimeUnixNano"]) + 1_234_567)
    # Some exporters write the times as JSON numbers rather than strings.
    retry = _tool(1, 3, "get_signin_logs", arguments={"user": _USER})
    retry["startTimeUnixNano"] = int(retry["startTimeUnixNano"])
    retry["endTimeUnixNano"] = int(retry["endTimeUnixNano"]) + 1
    return _files([root, tool, retry], [_row("C-1", "FP")])


@_fixture("edge/otlp/uppercase_hex_ids", DETAIL_KEEP)
def _uppercase_hex_ids() -> Files:
    spans = [_root(0xABC, "C-1", span=0xDEF), _tool(0xABC, 2, "get_signin_logs", parent=0xDEF)]
    for span in spans:
        for key in ("traceId", "spanId", "parentSpanId"):
            span[key] = span[key].upper()
    return _files(spans, [_row("C-1", "FP")])


@_fixture("edge/otlp/unicode_values", (*CLASS_KEEP, "case_detail"))
def _unicode_values() -> Files:
    return _files(
        [
            _root(1, "FALL-Ω-1", alert_class="Reise_über_Grenzen"),
            _tool(
                1, 2, "get_signin_logs", arguments={"user": "müller@example.com", "note": "東京 ✓"}
            ),
        ],
        [_row("FALL-Ω-1", "FP", alert_class="Reise_über_Grenzen")],
    )


# ----- Broken files --------------------------------------------------------------------------


@_fixture("edge/broken_files/empty_file", JOIN_KEEP)
def _empty_file() -> Files:
    return _files(
        [],
        [_row("C-1")],
        traces={
            "traces/traces-2026-08-04T10-00-03.000.jsonl": b"",
            "traces/traces.jsonl": _jsonl(_document(_case(1, "C-1"))),
        },
    )


@_fixture("edge/broken_files/truncated_last_line", JOIN_KEEP)
def _truncated_last_line() -> Files:
    whole = _jsonl(_document(_case(1, "C-1")))
    # A writer was still busy: the last line stops halfway, with no newline.
    cut = _jsonl(_document(_case(2, "C-2")))
    return _files(
        [],
        [_row("C-1"), _row("C-2")],
        traces={"traces/traces.jsonl": whole + cut[: len(cut) // 2]},
    )
