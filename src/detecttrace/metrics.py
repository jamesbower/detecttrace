"""Verdict agreement, Cohen's kappa, evidence completeness, skipped steps and weekly trends."""

import statistics
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from enum import StrEnum
from typing import Literal

from detecttrace.checklist import Checklist
from detecttrace.config import normalize_label
from detecttrace.evidence import (
    CaseEvidence,
    ItemStatus,
    evaluate_case,
    find_missing_tool_arguments,
    find_rule_type_mismatches,
)
from detecttrace.model import Case, Issue, IssueKind, Verdict
from detecttrace.stats import (
    Interval,
    cohens_kappa,
    kappa_analytic_interval,
    mean_t_interval,
    percentile_bootstrap,
    wilson_interval,
)

# Confusion matrix rows are analyst verdicts, columns agent verdicts, in this order.
VERDICT_ORDER = (Verdict.TRUE_POSITIVE, Verdict.FALSE_POSITIVE, Verdict.BENIGN)
ANALYTIC_KAPPA_MIN_CASES = 100
T_INTERVAL_MIN_CASES = 30
MAX_SHOWN_VERSIONS = 6

_VERDICT_INDEX = {verdict: index for index, verdict in enumerate(VERDICT_ORDER)}
_SIZE = len(VERDICT_ORDER)
_CLOSED_AS_NOT_THREAT = (Verdict.FALSE_POSITIVE, Verdict.BENIGN)
_EPOCH = datetime(1970, 1, 1, tzinfo=UTC)


class KappaNote(StrEnum):
    NO_CASES = "no_cases"
    ALL_SAME_VERDICT = "all_same_verdict"
    ONE_SIDE_SAME_VERDICT = "one_side_same_verdict"
    INTERVAL_NOT_AVAILABLE = "interval_not_available"


@dataclass(frozen=True, slots=True)
class Agreement:
    agreed: int
    n: int
    rate: float | None
    interval: Interval | None


@dataclass(frozen=True, slots=True)
class Kappa:
    value: float | None
    interval: Interval | None
    method: Literal["analytic", "bootstrap"] | None  # None when no interval was attempted
    note: KappaNote | None
    dropped_resamples: int


@dataclass(frozen=True, slots=True)
class Completeness:
    mean: float
    interval: Interval | None
    n: int
    method: Literal["t", "bootstrap"] | None  # None when no interval was attempted


@dataclass(frozen=True, slots=True)
class SliceMetrics:
    case_count: int
    agreement: Agreement
    kappa: Kappa
    confusion: tuple[tuple[int, int, int], ...]
    dangerous_false_closes: tuple[str, ...]  # sorted case IDs
    # Sorted case IDs. Not dangerous closes, but a safety risk the data notes must show.
    true_positives_without_agent_verdict: tuple[str, ...]
    completeness: Completeness | None


@dataclass(frozen=True, slots=True)
class SkipRate:
    item_id: str
    skipped: int  # failed or missed
    n: int
    rate: float


class TrendScope(StrEnum):
    ALL = "all"
    VERSION = "version"
    OTHER = "other"


@dataclass(frozen=True, slots=True)
class WeekPoint:
    week: str  # ISO week in UTC, e.g. "2026-W38"
    scope: TrendScope
    version: str | None  # meaningful only for VERSION scope, where None means no version
    completeness: float | None
    completeness_n: int
    agreement: float | None
    agreement_n: int
    completeness_interval: Interval | None
    agreement_interval: Interval | None


@dataclass(frozen=True, slots=True)
class ClassReport:
    alert_class: str  # CSV form of the first case by case ID
    # None: no version. Ordered by first case start, then name, None as if it were "".
    versions: tuple[str | None, ...]
    # The top real versions by case count, plus None when it has cases, in `versions` order.
    # None never takes one of the places: pooling it would mix "no version" with real ones.
    shown_versions: tuple[str | None, ...]
    other_versions: tuple[str, ...]  # the pooled rest, in `versions` order
    version_first_week: dict[str | None, str]
    overall: SliceMetrics
    by_version: dict[str | None, SliceMetrics]  # every version, shown or pooled
    other: SliceMetrics | None  # the pooled cases; None when nothing is pooled
    checklist_item_ids: tuple[str, ...]  # empty without a checklist
    skipped_overall: tuple[SkipRate, ...]
    skipped_by_version: dict[str | None, tuple[SkipRate, ...]]
    skipped_other: tuple[SkipRate, ...]
    # By week, then the all-versions point, the shown versions in order, then the pooled point.
    trend: tuple[WeekPoint, ...]


@dataclass(frozen=True, slots=True)
class MetricsReport:
    classes: tuple[ClassReport, ...]  # sorted by normalized class
    evidence: dict[str, CaseEvidence]  # by case ID; cases of classes with a checklist


def iso_week(start_ns: int) -> str:
    """The ISO 8601 week, in UTC, that a timestamp in Unix nanoseconds falls in: "2026-W38"."""
    moment = _EPOCH + timedelta(microseconds=start_ns // 1_000)
    year, week, _ = moment.isocalendar()
    return f"{year}-W{week:02d}"


def compute_metrics(
    cases: Sequence[Case], checklists: Mapping[str, Checklist]
) -> tuple[MetricsReport, list[Issue]]:
    """Build one report per alert class. `checklists` is keyed by the normalized alert class.

    Issues come in this order: evidence issues (classes in normalized order, cases in case-ID
    order), rule type mismatches and then missing tool arguments (each in class key order, then
    item order), unknown checklist tools (the same order), then unused checklists (class key
    order). With no cases, the last two are left out.
    """
    ordered = sorted(cases, key=lambda case: case.case_id)
    groups: dict[str, list[Case]] = {}
    for case in ordered:
        groups.setdefault(normalize_label(case.alert_class), []).append(case)
    issues: list[Issue] = []
    evidence: dict[str, CaseEvidence] = {}
    classes: list[ClassReport] = []
    for key in sorted(groups):
        group = groups[key]
        checklist = checklists.get(key)
        if checklist is not None:
            # Evaluated once here and reused by every slice, skip rate and trend point.
            for case in group:
                evidence[case.case_id] = evaluate_case(case, checklist, issues)
        classes.append(_build_class_report(group, checklist, evidence))
    issues.extend(find_rule_type_mismatches(ordered, checklists))
    issues.extend(find_missing_tool_arguments(ordered, checklists))
    # With no case at all, every checklist looks unused and every tool unknown; the real
    # problem is reported elsewhere, and these warnings would only point the wrong way.
    if not ordered:
        return MetricsReport(tuple(classes), evidence), issues
    called_tools = {call.tool_name for case in ordered for call in case.tool_calls}
    for key in sorted(checklists):
        checklist = checklists[key]
        for item in checklist.items:
            if item.tool not in called_tools:
                subject = f"{checklist.alert_class}/{item.id}"
                issues.append(Issue(IssueKind.UNKNOWN_CHECKLIST_TOOL, subject, item.tool))
    for key in sorted(checklists):
        if key not in groups:
            subject = checklists[key].alert_class
            issues.append(
                Issue(IssueKind.UNUSED_CHECKLIST, subject, "no case has this alert class")
            )
    return MetricsReport(tuple(classes), evidence), issues


def compute_slice(
    cases: Sequence[Case], evidence: Mapping[str, CaseEvidence], item_count: int | None
) -> SliceMetrics:
    """Metrics for one group of cases.

    Agreement, kappa, the confusion matrix and dangerous closes use only cases with both
    verdicts; completeness uses every case. `item_count` is None when the class has no
    checklist, and `evidence` is keyed by case ID.
    """
    cells: list[int] = []
    dangerous: list[str] = []
    without_agent: list[str] = []
    for case in cases:
        analyst, agent = case.analyst_verdict, case.agent_verdict
        if analyst is Verdict.TRUE_POSITIVE and agent is None:
            without_agent.append(case.case_id)
        if analyst is None or agent is None:
            continue
        cells.append(_VERDICT_INDEX[analyst] * _SIZE + _VERDICT_INDEX[agent])
        if analyst is Verdict.TRUE_POSITIVE and agent in _CLOSED_AS_NOT_THREAT:
            dangerous.append(case.case_id)
    matrix = _count_cells(cells, range(len(cells)))
    confusion = tuple((row[0], row[1], row[2]) for row in matrix)
    agreed = sum(matrix[i][i] for i in range(_SIZE))
    n = len(cells)
    return SliceMetrics(
        case_count=len(cases),
        agreement=Agreement(agreed, n, agreed / n if n else None, wilson_interval(agreed, n)),
        kappa=_kappa(matrix, cells),
        confusion=confusion,
        dangerous_false_closes=tuple(sorted(dangerous)),
        true_positives_without_agent_verdict=tuple(sorted(without_agent)),
        completeness=_completeness(cases, evidence, item_count),
    )


def _build_class_report(
    cases: list[Case], checklist: Checklist | None, evidence: Mapping[str, CaseEvidence]
) -> ClassReport:
    item_count = len(checklist.items) if checklist is not None else None
    cases_by_version: dict[str | None, list[Case]] = {}
    first_start: dict[str | None, int] = {}
    for case in cases:
        version = case.prompt_version
        cases_by_version.setdefault(version, []).append(case)
        first_start[version] = min(first_start.get(version, case.start_ns), case.start_ns)
    versions = tuple(
        sorted(cases_by_version, key=lambda version: (first_start[version], version or ""))
    )
    real_versions = [version for version in versions if version is not None]
    # Ranked from arrival order, not `versions`, so the tie-break below decides the cut on its own.
    ranked = sorted(
        (version for version in cases_by_version if version is not None),
        key=lambda version: (-len(cases_by_version[version]), first_start[version], version),
    )
    pooled = set(ranked[MAX_SHOWN_VERSIONS:])
    other_cases = [case for case in cases if case.prompt_version in pooled]
    item_ids = tuple(item.id for item in checklist.items) if checklist is not None else ()
    shown_versions = tuple(version for version in versions if version not in pooled)
    return ClassReport(
        alert_class=cases[0].alert_class,
        versions=versions,
        shown_versions=shown_versions,
        other_versions=tuple(version for version in real_versions if version in pooled),
        version_first_week={version: iso_week(first_start[version]) for version in versions},
        overall=compute_slice(cases, evidence, item_count),
        by_version={
            version: compute_slice(cases_by_version[version], evidence, item_count)
            for version in versions
        },
        other=compute_slice(other_cases, evidence, item_count) if other_cases else None,
        checklist_item_ids=item_ids,
        skipped_overall=_skip_rates(cases, evidence, item_ids),
        skipped_by_version={
            version: _skip_rates(cases_by_version[version], evidence, item_ids)
            for version in versions
        }
        if item_ids
        else {},
        skipped_other=_skip_rates(other_cases, evidence, item_ids) if other_cases else (),
        trend=_trend(cases, shown_versions, pooled, evidence, item_count),
    )


def _skip_rates(
    cases: list[Case], evidence: Mapping[str, CaseEvidence], item_ids: tuple[str, ...]
) -> tuple[SkipRate, ...]:
    if not item_ids:
        return ()
    skipped = [0] * len(item_ids)
    for case in cases:
        for index, outcome in enumerate(evidence[case.case_id].outcomes):
            if outcome.status is not ItemStatus.SATISFIED:
                skipped[index] += 1
    n = len(cases)
    return tuple(
        SkipRate(item_id, count, n, count / n)
        for item_id, count in zip(item_ids, skipped, strict=True)
    )


def _trend(
    cases: list[Case],
    shown_versions: tuple[str | None, ...],
    pooled: set[str],
    evidence: Mapping[str, CaseEvidence],
    item_count: int | None,
) -> tuple[WeekPoint, ...]:
    # A trend key is (week, scope, version); the version is None outside VERSION scope.
    completeness: dict[tuple[str, TrendScope, str | None], list[float]] = {}
    agreement: dict[tuple[str, TrendScope, str | None], list[bool]] = {}
    for case in cases:
        week = iso_week(case.start_ns)
        version_key = (
            (week, TrendScope.OTHER, None)
            if case.prompt_version in pooled
            else (week, TrendScope.VERSION, case.prompt_version)
        )
        keys = ((week, TrendScope.ALL, None), version_key)
        for key in keys:
            completeness.setdefault(key, [])
            agreement.setdefault(key, [])
        if item_count is not None:
            value = evidence[case.case_id].satisfied_count / item_count
            for key in keys:
                completeness[key].append(value)
        if case.analyst_verdict is not None and case.agent_verdict is not None:
            is_agreed = case.analyst_verdict is case.agent_verdict
            for key in keys:
                agreement[key].append(is_agreed)
    weeks = sorted({week for week, _, _ in completeness})
    order = (
        (TrendScope.ALL, None),
        *((TrendScope.VERSION, version) for version in shown_versions),
        (TrendScope.OTHER, None),
    )
    return tuple(
        _week_point(week, scope, version, completeness[key], agreement[key])
        for week in weeks
        for scope, version in order
        if (key := (week, scope, version)) in completeness
    )


def _week_point(
    week: str,
    scope: TrendScope,
    version: str | None,
    completeness: list[float],
    agreement: list[bool],
) -> WeekPoint:
    return WeekPoint(
        week=week,
        scope=scope,
        version=version,
        completeness=statistics.fmean(completeness) if completeness else None,
        completeness_n=len(completeness),
        agreement=sum(agreement) / len(agreement) if agreement else None,
        agreement_n=len(agreement),
        completeness_interval=_completeness_interval(completeness)[0],
        agreement_interval=wilson_interval(sum(agreement), len(agreement)),
    )


def _count_cells(cells: Sequence[int], indices: Sequence[int]) -> list[list[int]]:
    flat = [0] * (_SIZE * _SIZE)
    for index in indices:
        flat[cells[index]] += 1
    return [flat[row * _SIZE : (row + 1) * _SIZE] for row in range(_SIZE)]


def _kappa(matrix: list[list[int]], cells: list[int]) -> Kappa:
    n = len(cells)
    if n == 0:
        return Kappa(None, None, None, KappaNote.NO_CASES, 0)
    value = cohens_kappa(matrix)
    if value is None:
        return Kappa(None, None, None, KappaNote.ALL_SAME_VERDICT, 0)
    # One constant side makes kappa exactly 0 with a standard error of 0; a zero-width
    # interval would look precise, so none is shown.
    rows_used = sum(any(row) for row in matrix)
    columns_used = sum(any(matrix[i][j] for i in range(_SIZE)) for j in range(_SIZE))
    if rows_used == 1 or columns_used == 1:
        return Kappa(0.0, None, None, KappaNote.ONE_SIDE_SAME_VERDICT, 0)
    if n >= ANALYTIC_KAPPA_MIN_CASES:
        return Kappa(value, kappa_analytic_interval(matrix), "analytic", None, 0)
    result = percentile_bootstrap(n, lambda indices: cohens_kappa(_count_cells(cells, indices)))
    note = KappaNote.INTERVAL_NOT_AVAILABLE if result.interval is None else None
    return Kappa(value, result.interval, "bootstrap", note, result.dropped)


def _completeness(
    cases: Sequence[Case], evidence: Mapping[str, CaseEvidence], item_count: int | None
) -> Completeness | None:
    if item_count is None:
        return None
    if item_count < 1:
        raise ValueError(f"a checklist has at least one item, got {item_count}")
    if not cases:
        return None
    values: list[float] = []
    for case in cases:
        case_evidence = evidence.get(case.case_id)
        if case_evidence is None:
            raise ValueError(f"no evidence for case '{case.case_id}' although a checklist exists")
        values.append(case_evidence.satisfied_count / item_count)
    interval, method = _completeness_interval(values)
    return Completeness(statistics.fmean(values), interval, len(values), method)


def _completeness_interval(
    values: Sequence[float],
) -> tuple[Interval | None, Literal["t", "bootstrap"] | None]:
    # One case or equal values give a zero-width interval that would look precise, so none
    # is shown.
    if len(values) < 2 or min(values) == max(values):
        return None, None
    if len(values) >= T_INTERVAL_MIN_CASES:
        return _clip(mean_t_interval(values)), "t"
    result = percentile_bootstrap(
        len(values), lambda indices: statistics.fmean(values[i] for i in indices)
    )
    # The mean of a non-empty resample of finite values is always defined, so no resample is
    # dropped and the interval always exists.
    assert result.interval is not None
    return _clip(result.interval), "bootstrap"


def _clip(interval: Interval) -> Interval:
    return Interval(max(0.0, interval.low), min(1.0, interval.high))
