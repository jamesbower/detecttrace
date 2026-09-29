"""Verdict agreement, Cohen's kappa, and evidence completeness for groups of cases."""

import statistics
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from enum import StrEnum
from typing import Literal

from detecttrace.evidence import CaseEvidence
from detecttrace.model import Case, Verdict
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

_VERDICT_INDEX = {verdict: index for index, verdict in enumerate(VERDICT_ORDER)}
_SIZE = len(VERDICT_ORDER)
_CLOSED_AS_NOT_THREAT = (Verdict.FALSE_POSITIVE, Verdict.BENIGN)


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
    method: Literal["analytic", "bootstrap"] | None  # None when no interval was computed
    note: KappaNote | None
    dropped_resamples: int


@dataclass(frozen=True, slots=True)
class Completeness:
    mean: float
    interval: Interval
    n: int
    method: Literal["t", "bootstrap"]


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
    mean = statistics.fmean(values)
    if len(values) >= T_INTERVAL_MIN_CASES:
        return Completeness(mean, _clip(mean_t_interval(values)), len(values), "t")
    result = percentile_bootstrap(
        len(values), lambda indices: statistics.fmean(values[i] for i in indices)
    )
    # The mean of a non-empty resample is always defined, so every resample is valid.
    if result.interval is None:
        raise RuntimeError("bootstrap of a mean dropped resamples")
    return Completeness(mean, _clip(result.interval), len(values), "bootstrap")


def _clip(interval: Interval) -> Interval:
    return Interval(max(0.0, interval.low), min(1.0, interval.high))
