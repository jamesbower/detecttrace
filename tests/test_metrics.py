from collections.abc import Mapping

import pytest

from detecttrace.evidence import CaseEvidence, ItemOutcome, ItemStatus, MissedReason
from detecttrace.metrics import (
    Agreement,
    Kappa,
    KappaNote,
    SliceMetrics,
    compute_slice,
)
from detecttrace.model import Case, Verdict
from detecttrace.stats import (
    BOOTSTRAP_MIN_VALID,
    BOOTSTRAP_RESAMPLES,
    Interval,
    kappa_analytic_interval,
    wilson_interval,
)

TP = Verdict.TRUE_POSITIVE
FP = Verdict.FALSE_POSITIVE
BENIGN = Verdict.BENIGN
SATISFIED = ItemOutcome(ItemStatus.SATISFIED, None)
NOT_CALLED = ItemOutcome(ItemStatus.MISSED, MissedReason.NOT_CALLED)

VerdictCounts = Mapping[tuple[Verdict | None, Verdict | None], int]

# 100 cases with every verdict on both sides, so kappa is defined with an interval.
MIXED_100: VerdictCounts = {
    (TP, TP): 40,
    (FP, FP): 30,
    (BENIGN, BENIGN): 20,
    (TP, FP): 5,
    (FP, BENIGN): 5,
}
MIXED_99: VerdictCounts = {**MIXED_100, (BENIGN, BENIGN): 19}


def make_case(
    case_id: str, analyst: Verdict | None, agent: Verdict | None, alert_class: str = "Phishing"
) -> Case:
    return Case(
        case_id=case_id,
        alert_class=alert_class,
        prompt_version="v1",
        analyst_verdict=analyst,
        agent_verdict=agent,
        start_ns=0,
        tool_calls=(),
        is_incomplete_trace=False,
    )


def make_cases(counts: VerdictCounts) -> list[Case]:
    pairs = [pair for pair, count in counts.items() for _ in range(count)]
    return [
        make_case(f"case-{index:05d}", analyst, agent)
        for index, (analyst, agent) in enumerate(pairs)
    ]


def make_evidence(case_id: str, satisfied: int, item_count: int) -> CaseEvidence:
    outcomes = (SATISFIED,) * satisfied + (NOT_CALLED,) * (item_count - satisfied)
    return CaseEvidence(case_id, outcomes)


def evidence_for(
    cases: list[Case], satisfied: list[int], item_count: int
) -> dict[str, CaseEvidence]:
    return {
        case.case_id: make_evidence(case.case_id, count, item_count)
        for case, count in zip(cases, satisfied, strict=True)
    }


def slice_of(counts: VerdictCounts) -> SliceMetrics:
    return compute_slice(make_cases(counts), {}, None)


# Agreement


def test_agreement_rate_counts_matching_verdicts() -> None:
    metrics = slice_of({(TP, TP): 5, (FP, FP): 3, (TP, FP): 2})

    assert metrics.agreement == Agreement(8, 10, 0.8, wilson_interval(8, 10))


def test_case_with_unknown_verdict_is_left_out_of_agreement() -> None:
    metrics = slice_of({(TP, TP): 3, (None, TP): 1, (FP, None): 1})

    assert metrics.agreement.n == 3


def test_case_with_unknown_verdict_still_counts_toward_case_count() -> None:
    metrics = slice_of({(TP, TP): 3, (None, TP): 1, (FP, None): 1})

    assert metrics.case_count == 5


def test_case_with_unknown_verdict_is_left_out_of_confusion() -> None:
    metrics = slice_of({(TP, TP): 3, (None, TP): 1, (TP, None): 1})

    assert metrics.confusion == ((3, 0, 0), (0, 0, 0), (0, 0, 0))


def test_no_cases_with_both_verdicts_gives_no_rate() -> None:
    metrics = slice_of({(None, TP): 2, (FP, None): 1})

    assert metrics.agreement == Agreement(0, 0, None, None)


# Confusion and dangerous closes


def test_confusion_places_analyst_rows_and_agent_columns() -> None:
    metrics = slice_of({(TP, BENIGN): 1})

    assert metrics.confusion == ((0, 0, 1), (0, 0, 0), (0, 0, 0))


def test_dangerous_close_is_analyst_tp_closed_by_agent_as_fp_or_benign() -> None:
    cases = [
        make_case("case-c", TP, BENIGN),
        make_case("case-a", TP, FP),
        make_case("case-b", TP, TP),
        make_case("case-d", FP, BENIGN),
    ]

    metrics = compute_slice(cases, {}, None)

    assert metrics.dangerous_false_closes == ("case-a", "case-c")


def test_analyst_tp_without_agent_verdict_is_not_a_dangerous_close() -> None:
    metrics = slice_of({(TP, None): 1})

    assert metrics.dangerous_false_closes == ()


def test_analyst_tp_without_agent_verdict_is_listed_as_safety_note() -> None:
    cases = [make_case("case-b", TP, None), make_case("case-a", TP, None)]

    metrics = compute_slice(cases, {}, None)

    assert metrics.true_positives_without_agent_verdict == ("case-a", "case-b")


def test_analyst_fp_without_agent_verdict_is_not_listed_as_safety_note() -> None:
    metrics = slice_of({(FP, None): 1})

    assert metrics.true_positives_without_agent_verdict == ()


# Kappa


def test_kappa_with_no_cases_has_note_no_cases() -> None:
    metrics = slice_of({(None, TP): 1})

    assert metrics.kappa == Kappa(None, None, None, KappaNote.NO_CASES, 0)


def test_kappa_when_all_cases_benign_on_both_sides_is_undefined() -> None:
    metrics = slice_of({(BENIGN, BENIGN): 20})

    assert metrics.kappa == Kappa(None, None, None, KappaNote.ALL_SAME_VERDICT, 0)


def test_kappa_when_agent_always_says_tp_is_zero_without_interval_at_analytic_size() -> None:
    metrics = slice_of({(TP, TP): 100, (FP, TP): 50})

    assert metrics.kappa == Kappa(0.0, None, None, KappaNote.ONE_SIDE_SAME_VERDICT, 0)


def test_kappa_when_agent_always_says_tp_is_zero_without_interval_at_bootstrap_size() -> None:
    metrics = slice_of({(TP, TP): 30, (FP, TP): 10, (BENIGN, TP): 10})

    assert metrics.kappa == Kappa(0.0, None, None, KappaNote.ONE_SIDE_SAME_VERDICT, 0)


def test_kappa_when_analysts_always_say_benign_is_zero_without_interval() -> None:
    metrics = slice_of({(BENIGN, BENIGN): 30, (BENIGN, FP): 10})

    assert metrics.kappa == Kappa(0.0, None, None, KappaNote.ONE_SIDE_SAME_VERDICT, 0)


def test_kappa_at_100_cases_uses_analytic_method() -> None:
    metrics = slice_of(MIXED_100)

    assert metrics.kappa.method == "analytic"


def test_kappa_at_100_cases_uses_analytic_interval() -> None:
    metrics = slice_of(MIXED_100)

    assert metrics.kappa.interval == kappa_analytic_interval(metrics.confusion)


def test_kappa_at_99_cases_uses_bootstrap_method() -> None:
    metrics = slice_of(MIXED_99)

    assert metrics.kappa.method == "bootstrap"


def test_kappa_bootstrap_with_enough_valid_resamples_has_interval() -> None:
    metrics = slice_of(MIXED_99)

    assert metrics.kappa.interval is not None


# 98 agreed TP cases and one agreed FP case: a resample misses the FP case with probability
# (98/99)^99 ≈ 0.37, leaving both sides all TP, so kappa is undefined. About 370 of 1,000
# resamples are dropped (351 with the fixed seed), well over the 100 that the interval allows.
DEGENERATE_99: VerdictCounts = {(TP, TP): 98, (FP, FP): 1}


def test_kappa_bootstrap_with_many_undefined_resamples_has_no_interval() -> None:
    metrics = slice_of(DEGENERATE_99)

    assert metrics.kappa.interval is None


def test_kappa_bootstrap_with_many_undefined_resamples_notes_why() -> None:
    metrics = slice_of(DEGENERATE_99)

    assert metrics.kappa.note is KappaNote.INTERVAL_NOT_AVAILABLE


def test_kappa_bootstrap_with_many_undefined_resamples_reports_dropped_count() -> None:
    metrics = slice_of(DEGENERATE_99)

    assert metrics.kappa.dropped_resamples > BOOTSTRAP_RESAMPLES - BOOTSTRAP_MIN_VALID


def test_kappa_bootstrap_with_many_undefined_resamples_still_has_a_value() -> None:
    metrics = slice_of(DEGENERATE_99)

    assert metrics.kappa.value == 1.0


# Completeness


def test_completeness_at_30_cases_uses_t_method() -> None:
    cases = make_cases({(TP, TP): 30})
    evidence = evidence_for(cases, [2] * 30, 4)

    metrics = compute_slice(cases, evidence, 4)

    assert metrics.completeness is not None and metrics.completeness.method == "t"


def test_completeness_t_interval_is_clipped_to_one() -> None:
    # 29 complete cases and one empty one: the unclipped upper bound is about 1.03.
    cases = make_cases({(TP, TP): 30})
    evidence = evidence_for(cases, [4] * 29 + [0], 4)

    metrics = compute_slice(cases, evidence, 4)

    assert metrics.completeness is not None and metrics.completeness.interval.high == 1.0


def test_completeness_at_29_cases_uses_bootstrap_method() -> None:
    cases = make_cases({(TP, TP): 29})
    evidence = evidence_for(cases, [2] * 29, 4)

    metrics = compute_slice(cases, evidence, 4)

    assert metrics.completeness is not None and metrics.completeness.method == "bootstrap"


def test_completeness_mean_is_share_of_items_satisfied() -> None:
    cases = make_cases({(TP, TP): 2})
    evidence = evidence_for(cases, [1, 4], 4)

    metrics = compute_slice(cases, evidence, 4)

    assert metrics.completeness is not None and metrics.completeness.mean == 0.625


def test_completeness_with_equal_values_has_zero_width_interval() -> None:
    cases = make_cases({(TP, TP): 10})
    evidence = evidence_for(cases, [2] * 10, 4)

    metrics = compute_slice(cases, evidence, 4)

    assert metrics.completeness is not None and metrics.completeness.interval == Interval(0.5, 0.5)


def test_completeness_of_one_case_has_zero_width_interval() -> None:
    cases = make_cases({(TP, TP): 1})
    evidence = evidence_for(cases, [3], 4)

    metrics = compute_slice(cases, evidence, 4)

    assert metrics.completeness is not None and metrics.completeness.interval == Interval(
        0.75, 0.75
    )


def test_completeness_without_checklist_is_none() -> None:
    metrics = slice_of({(TP, TP): 5})

    assert metrics.completeness is None


def test_completeness_with_no_cases_is_none() -> None:
    metrics = compute_slice([], {}, 4)

    assert metrics.completeness is None


def test_completeness_counts_case_without_agent_verdict() -> None:
    cases = make_cases({(TP, TP): 2, (TP, None): 1})
    evidence = evidence_for(cases, [1, 1, 1], 4)

    metrics = compute_slice(cases, evidence, 4)

    assert metrics.completeness is not None and metrics.completeness.n == 3


def test_completeness_with_case_missing_from_evidence_raises() -> None:
    cases = [make_case("case-lost", TP, TP)]

    with pytest.raises(ValueError, match="case-lost"):
        compute_slice(cases, {}, 4)


def test_completeness_with_zero_items_raises() -> None:
    cases = [make_case("case-1", TP, TP)]

    with pytest.raises(ValueError, match="at least one item"):
        compute_slice(cases, {"case-1": CaseEvidence("case-1", ())}, 0)


# Determinism


def test_same_input_gives_same_result() -> None:
    cases = make_cases(MIXED_99)
    evidence = evidence_for(cases, [0, 1, 2, 3, 4] * 19 + [0, 1, 2, 3], 4)

    assert compute_slice(cases, evidence, 4) == compute_slice(cases, evidence, 4)
