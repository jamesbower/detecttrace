"""The results data object: summary, case rows and case detail, written as one JSON file.

Dicts keyed by a version are never serialized, because `json.dumps` turns a None key into
"null" and a real version named "null" would collide with it. Per-version data is a list of
records with a `version` field instead; the pooled group sits under its own `other` key.
"""

import json
import os
import stat
import tempfile
from collections.abc import Mapping, Sequence
from pathlib import Path

from detecttrace import __version__
from detecttrace.checklist import Checklist
from detecttrace.config import normalize_label
from detecttrace.evidence import CaseEvidence, ItemStatus
from detecttrace.metrics import (
    VERDICT_ORDER,
    ClassReport,
    MetricsReport,
    SkipRate,
    SliceMetrics,
    WeekPoint,
    iso_week,
)
from detecttrace.model import Case, ToolCall, Verdict
from detecttrace.stats import Interval
from detecttrace.summary import JoinCoverage, SummaryLine, is_low_coverage

SCHEMA_VERSION = 1
MAX_ARGUMENT_CHARS = 200
UNKNOWN_VERDICT_CODE = -1
GENERATED_BY_PREFIX = "detecttrace"
# generated_by is the second key this module writes, so the head of the file is enough.
_MARKER_READ_BYTES = 64 * 1024
_JSON_WHITESPACE = " \t\n\r"


def _read_umask() -> int:
    # The only way to read the umask is to set it; done once at import, not per write.
    umask = os.umask(0)
    os.umask(umask)
    return umask


_UMASK = _read_umask()

_VERDICT_CODE = {verdict: code for code, verdict in enumerate(VERDICT_ORDER)}
_CLOSED_AS_NOT_THREAT = (Verdict.FALSE_POSITIVE, Verdict.BENIGN)
_DANGEROUS, _DISAGREEMENT, _FAILED_CALLS, _MISSED_STEPS = range(4)


def build_results(
    report: MetricsReport,
    cases: Sequence[Case],
    checklists: Mapping[str, Checklist],
    summary_lines: Sequence[SummaryLine],
    coverage: JoinCoverage,
    source: Mapping[str, str | None],
    max_detail_cases: int,
) -> dict[str, object]:
    """Build the JSON-ready results object. Every container is a list or a str-keyed dict.

    `checklists` is keyed by the normalized alert class; `source` holds the input paths as the
    configuration gives them. Tool results are never read: calls carry only their arguments.
    """
    ordered = sorted(cases, key=lambda case: case.case_id)
    weeks = sorted({iso_week(case.start_ns) for case in ordered})
    versions = {version for class_report in report.classes for version in class_report.versions}
    return {
        "schema_version": SCHEMA_VERSION,
        "generated_by": f"{GENERATED_BY_PREFIX} {__version__}",
        "source": dict(source),
        "totals": {
            "cases": len(ordered),
            "classes": len(report.classes),
            "period": {
                "first_week": weeks[0] if weeks else None,
                "last_week": weeks[-1] if weeks else None,
            },
            "versions": sorted(versions, key=lambda version: (version is not None, version or "")),
            "coverage": {
                "verdicts_matched": coverage.verdicts_matched,
                "verdicts_total": coverage.verdicts_total,
                "traces_matched": coverage.traces_matched,
                "traces_total": coverage.traces_total,
                "verdicts_low": is_low_coverage(coverage.verdicts_matched, coverage.verdicts_total),
                "traces_low": is_low_coverage(coverage.traces_matched, coverage.traces_total),
            },
        },
        "classes": [_to_class_data(class_report) for class_report in report.classes],
        "data_notes": [_to_note_data(line) for line in summary_lines],
        "case_rows": _build_case_rows(report, ordered, checklists),
        "case_detail": _build_case_detail(ordered, report.evidence, max_detail_cases),
    }


def write_results_json(results: Mapping[str, object], path: Path) -> None:
    """Write `results` as compact UTF-8 JSON, replacing `path` only once the file is complete.

    A NaN or infinity raises ValueError: every metric is either a number or null, so one is a bug.
    """
    # Serialized before any file is created, so a ValueError leaves nothing behind.
    text = json.dumps(results, ensure_ascii=False, allow_nan=False, separators=(",", ":"))
    handle, temp_name = tempfile.mkstemp(prefix=f".{path.name}.", suffix=".tmp", dir=path.parent)
    try:
        with os.fdopen(handle, "w", encoding="utf-8", newline="\n") as file:
            file.write(text + "\n")
        # mkstemp creates the file owner-only; give it the mode a plain open() would.
        os.chmod(temp_name, 0o666 & ~_UMASK)
        os.replace(temp_name, path)
    except BaseException:
        Path(temp_name).unlink(missing_ok=True)
        raise


def is_results_file(path: Path) -> bool:
    """Whether `path` is a regular file whose top-level `generated_by` names detecttrace.

    Reads at most the first 64 KiB, so a large or hostile file costs little to check.
    """
    try:
        # stat() before open(), so a named pipe never blocks the run.
        if not stat.S_ISREG(path.stat().st_mode):
            return False
        with path.open("rb") as file:
            head = file.read(_MARKER_READ_BYTES)
    except OSError:
        return False
    generated_by = _read_generated_by(head.decode("utf-8", errors="replace"))
    return generated_by is not None and generated_by.startswith(GENERATED_BY_PREFIX)


def _read_generated_by(text: str) -> str | None:
    """Scan the top-level keys of a JSON object prefix; json.loads needs the whole file."""
    decoder = json.JSONDecoder()
    index = _skip_whitespace(text, 0)
    if not text.startswith("{", index):
        return None
    try:
        while True:
            key, index = decoder.raw_decode(text, _skip_whitespace(text, index + 1))
            index = _skip_whitespace(text, index)
            if not isinstance(key, str) or not text.startswith(":", index):
                return None
            value, index = decoder.raw_decode(text, _skip_whitespace(text, index + 1))
            if key == "generated_by":
                return value if isinstance(value, str) else None
            index = _skip_whitespace(text, index)
            if not text.startswith(",", index):
                return None
    except (ValueError, RecursionError):
        return None


def _skip_whitespace(text: str, index: int) -> int:
    while index < len(text) and text[index] in _JSON_WHITESPACE:
        index += 1
    return index


def _to_class_data(report: ClassReport) -> dict[str, object]:
    return {
        "alert_class": report.alert_class,
        "checklist_item_ids": list(report.checklist_item_ids),
        "versions": list(report.versions),
        "shown_versions": list(report.shown_versions),
        "other_versions": list(report.other_versions),
        "overall": _to_slice_data(report.overall),
        "skipped_overall": _to_skip_data(report.skipped_overall),
        "by_version": [
            {
                "version": version,
                "first_week": report.version_first_week[version],
                "metrics": _to_slice_data(report.by_version[version]),
                "skipped": _to_skip_data(report.skipped_by_version.get(version, ())),
            }
            for version in report.versions
        ],
        "other": None
        if report.other is None
        else {
            "metrics": _to_slice_data(report.other),
            "skipped": _to_skip_data(report.skipped_other),
        },
        "trend": [_to_week_data(point) for point in report.trend],
    }


def _to_slice_data(metrics: SliceMetrics) -> dict[str, object]:
    agreement, kappa, completeness = metrics.agreement, metrics.kappa, metrics.completeness
    return {
        "case_count": metrics.case_count,
        "agreement": {
            "agreed": agreement.agreed,
            "n": agreement.n,
            "rate": agreement.rate,
            "interval": _to_interval_data(agreement.interval),
        },
        "kappa": {
            "value": kappa.value,
            "interval": _to_interval_data(kappa.interval),
            "method": kappa.method,
            "note": None if kappa.note is None else kappa.note.value,
            "dropped_resamples": kappa.dropped_resamples,
        },
        "confusion": [list(row) for row in metrics.confusion],
        "dangerous_false_closes": list(metrics.dangerous_false_closes),
        "true_positives_without_agent_verdict": list(metrics.true_positives_without_agent_verdict),
        "completeness": None
        if completeness is None
        else {
            "mean": completeness.mean,
            "interval": _to_interval_data(completeness.interval),
            "n": completeness.n,
            "method": completeness.method,
        },
    }


def _to_interval_data(interval: Interval | None) -> dict[str, float] | None:
    return None if interval is None else {"low": interval.low, "high": interval.high}


def _to_skip_data(rates: Sequence[SkipRate]) -> list[dict[str, object]]:
    return [
        {"item_id": rate.item_id, "skipped": rate.skipped, "n": rate.n, "rate": rate.rate}
        for rate in rates
    ]


def _to_week_data(point: WeekPoint) -> dict[str, object]:
    return {
        "week": point.week,
        "scope": point.scope.value,
        "version": point.version,
        "completeness": point.completeness,
        "completeness_n": point.completeness_n,
        "agreement": point.agreement,
        "agreement_n": point.agreement_n,
    }


def _to_note_data(line: SummaryLine) -> dict[str, object]:
    return {
        "severity": line.severity.value,
        "kind": line.kind.value,
        "count": line.count,
        "message": line.message,
        "examples": [
            {"subject": example.subject, "detail": example.detail} for example in line.examples
        ],
    }


def _build_case_rows(
    report: MetricsReport, cases: Sequence[Case], checklists: Mapping[str, Checklist]
) -> dict[str, object]:
    strings: dict[str, int] = {}

    def to_index(text: str) -> int:
        return strings.setdefault(text, len(strings))

    class_names = {
        normalize_label(class_report.alert_class): class_report.alert_class
        for class_report in report.classes
    }
    checklist_rows = [
        {
            "class": to_index(class_names[key]),
            "items": [to_index(item.id) for item in checklists[key].items],
        }
        for key in sorted(class_names)
        if key in checklists
    ]
    columns: dict[str, list[object]] = {
        name: []
        for name in (
            "case_id",
            "class",
            "week",
            "version",
            "analyst",
            "agent",
            "satisfied",
            "missed_items",
            "failed_items",
        )
    }
    for case in cases:
        evidence = report.evidence.get(case.case_id)
        outcomes = evidence.outcomes if evidence is not None else ()
        columns["case_id"].append(case.case_id)
        columns["class"].append(to_index(class_names[normalize_label(case.alert_class)]))
        columns["week"].append(to_index(iso_week(case.start_ns)))
        # None stays null rather than an index, so "no version" never meets a version's text.
        version = case.prompt_version
        columns["version"].append(None if version is None else to_index(version))
        columns["analyst"].append(_to_verdict_code(case.analyst_verdict))
        columns["agent"].append(_to_verdict_code(case.agent_verdict))
        columns["satisfied"].append(None if evidence is None else evidence.satisfied_count)
        columns["missed_items"].append(
            [index for index, outcome in enumerate(outcomes) if outcome.status is ItemStatus.MISSED]
        )
        columns["failed_items"].append(
            [index for index, outcome in enumerate(outcomes) if outcome.status is ItemStatus.FAILED]
        )
    return {
        "verdict_codes": [verdict.value for verdict in VERDICT_ORDER],
        "unknown_verdict_code": UNKNOWN_VERDICT_CODE,
        "checklists": checklist_rows,
        "strings": list(strings),
        "columns": columns,
    }


def _to_verdict_code(verdict: Verdict | None) -> int:
    return UNKNOWN_VERDICT_CODE if verdict is None else _VERDICT_CODE[verdict]


def _build_case_detail(
    cases: Sequence[Case], evidence: Mapping[str, CaseEvidence], max_detail_cases: int
) -> list[dict[str, object]]:
    ranked: list[tuple[int, int, str, Case]] = []
    for case in cases:
        group = _to_detail_group(case, evidence.get(case.case_id))
        if group is not None:
            ranked.append((group, -case.start_ns, case.case_id, case))
    ranked.sort(key=lambda entry: entry[:3])
    # A list, not a dict keyed by case ID, so the priority order survives any JSON reader.
    return [
        {"case_id": case.case_id, "calls": [_to_call_data(call) for call in case.tool_calls]}
        for _, _, _, case in ranked[: max(max_detail_cases, 0)]
    ]


def _to_detail_group(case: Case, evidence: CaseEvidence | None) -> int | None:
    analyst, agent = case.analyst_verdict, case.agent_verdict
    if analyst is Verdict.TRUE_POSITIVE and agent in _CLOSED_AS_NOT_THREAT:
        return _DANGEROUS
    if analyst is not None and agent is not None and analyst is not agent:
        return _DISAGREEMENT
    if any(call.is_failed for call in case.tool_calls):
        return _FAILED_CALLS
    if evidence is not None and any(
        outcome.status is ItemStatus.MISSED for outcome in evidence.outcomes
    ):
        return _MISSED_STEPS
    return None


def _to_call_data(call: ToolCall) -> dict[str, object]:
    return {
        "tool": call.tool_name,
        "status": "failed" if call.is_failed else "success",
        "duration_ms": (call.end_ns - call.start_ns) / 1_000_000,
        "arguments": _to_argument_text(call.arguments),
    }


def _to_argument_text(arguments: str | dict[str, object] | None) -> str | None:
    if arguments is None:
        return None
    # Text as the agent sent it; a decoded map keeps its original key order.
    text = arguments if isinstance(arguments, str) else json.dumps(arguments, ensure_ascii=False)
    if len(text) <= MAX_ARGUMENT_CHARS:
        return text
    return text[: MAX_ARGUMENT_CHARS - 1] + "…"
