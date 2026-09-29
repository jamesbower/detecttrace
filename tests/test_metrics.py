import json
import random
from collections.abc import Mapping
from datetime import UTC, datetime
from typing import Any

import pytest

from detecttrace.checklist import Checklist
from detecttrace.evidence import CaseEvidence, ItemOutcome, ItemStatus, MissedReason
from detecttrace.metrics import (
    Agreement,
    Completeness,
    Kappa,
    KappaNote,
    MetricsReport,
    SkipRate,
    SliceMetrics,
    TrendScope,
    WeekPoint,
    compute_metrics,
    compute_slice,
    iso_week,
)
from detecttrace.model import Case, IssueKind, ToolCall, Verdict
from detecttrace.stats import (
    Interval,
    kappa_analytic_interval,
    mean_t_interval,
    wilson_interval,
)
from detecttrace.summary import summarize_issues

TP = Verdict.TRUE_POSITIVE
FP = Verdict.FALSE_POSITIVE
BENIGN = Verdict.BENIGN
SATISFIED = ItemOutcome(ItemStatus.SATISFIED, None)
NOT_CALLED = ItemOutcome(ItemStatus.MISSED, MissedReason.NOT_CALLED)
ALL = TrendScope.ALL
VERSION = TrendScope.VERSION
OTHER = TrendScope.OTHER

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
    case_id: str,
    analyst: Verdict | None,
    agent: Verdict | None,
    alert_class: str = "Phishing",
    *,
    version: str | None = "v1",
    start_ns: int = 0,
    calls: tuple[ToolCall, ...] = (),
) -> Case:
    return Case(
        case_id=case_id,
        alert_class=alert_class,
        prompt_version=version,
        analyst_verdict=analyst,
        agent_verdict=agent,
        start_ns=start_ns,
        tool_calls=calls,
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


def test_kappa_when_each_side_always_gives_a_different_verdict_is_zero_without_interval() -> None:
    metrics = slice_of({(TP, FP): 40})

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
    # Computed by a throwaway script that does not import detecttrace: random.Random(20260928),
    # 1,000 resamples of rng.choices(range(99), k=99) over the MIXED_99 verdict pairs in
    # make_cases order, kappa from scratch with fractions, then
    # statistics.quantiles(values, n=40, method="inclusive") -> (cuts[0], cuts[-1]).
    expected = pytest.approx((0.7430119713899326, 0.9331243219151467), rel=1e-12)
    metrics = slice_of(MIXED_99)

    assert (
        metrics.kappa.interval is not None
        and (metrics.kappa.interval.low, metrics.kappa.interval.high) == expected
    )


# 98 agreed TP cases and one agreed FP case: a resample misses the FP case with probability
# (98/99)^99 ≈ 0.37, leaving both sides all TP, so kappa is undefined. About 370 of 1,000
# resamples are dropped, well over the 100 that the interval allows. The exact count, 351, comes
# from a throwaway script that does not import detecttrace: random.Random(20260928), 1,000
# resamples of rng.choices(range(99), k=99), counting those that miss index 98 (the FP case).
DEGENERATE_99: VerdictCounts = {(TP, TP): 98, (FP, FP): 1}


def test_kappa_bootstrap_with_many_undefined_resamples_has_no_interval() -> None:
    metrics = slice_of(DEGENERATE_99)

    assert metrics.kappa.interval is None


def test_kappa_bootstrap_with_many_undefined_resamples_notes_why() -> None:
    metrics = slice_of(DEGENERATE_99)

    assert metrics.kappa.note is KappaNote.INTERVAL_NOT_AVAILABLE


def test_kappa_bootstrap_with_many_undefined_resamples_reports_dropped_count() -> None:
    metrics = slice_of(DEGENERATE_99)

    assert metrics.kappa.dropped_resamples == 351


def test_kappa_bootstrap_with_many_undefined_resamples_still_has_a_value() -> None:
    metrics = slice_of(DEGENERATE_99)

    assert metrics.kappa.value == 1.0


# Completeness


def completeness_of(cases: list[Case], satisfied: list[int], item_count: int) -> Completeness:
    metrics = compute_slice(cases, evidence_for(cases, satisfied, item_count), item_count)
    assert metrics.completeness is not None
    return metrics.completeness


# Every value from 0 to 4 satisfied items, so the case values vary.
VARIED_30 = [0, 1, 2, 3, 4] * 6
VARIED_29 = VARIED_30[:29]


def test_completeness_at_30_cases_uses_t_method() -> None:
    completeness = completeness_of(make_cases({(TP, TP): 30}), VARIED_30, 4)

    assert completeness.method == "t"


def test_completeness_with_30_varied_cases_has_t_interval() -> None:
    completeness = completeness_of(make_cases({(TP, TP): 30}), VARIED_30, 4)

    assert completeness.interval == mean_t_interval([0.0, 0.25, 0.5, 0.75, 1.0] * 6)


def test_completeness_t_interval_is_clipped_to_one() -> None:
    # 29 complete cases and one empty one: the unclipped upper bound is about 1.03.
    completeness = completeness_of(make_cases({(TP, TP): 30}), [4] * 29 + [0], 4)

    assert completeness.interval == Interval(mean_t_interval([1.0] * 29 + [0.0]).low, 1.0)


def test_completeness_t_interval_is_clipped_to_zero() -> None:
    # 29 empty cases and one complete one: the unclipped lower bound is about -0.035.
    completeness = completeness_of(make_cases({(TP, TP): 30}), [0] * 29 + [4], 4)

    assert completeness.interval is not None and completeness.interval.low == 0.0


def test_completeness_at_29_cases_has_bootstrap_interval() -> None:
    # Computed by a throwaway script that does not import detecttrace: random.Random(20260928),
    # 1,000 resamples of rng.choices(range(29), k=29) over the VARIED_29 shares, the mean of
    # each from scratch, then statistics.quantiles(values, n=40, method="inclusive")
    # -> (cuts[0], cuts[-1]).
    expected = pytest.approx((0.3706896551724138, 0.6120689655172413), rel=1e-12)
    completeness = completeness_of(make_cases({(TP, TP): 29}), VARIED_29, 4)

    assert (
        completeness.interval is not None
        and (completeness.interval.low, completeness.interval.high) == expected
    )


def test_completeness_at_29_cases_uses_bootstrap_method() -> None:
    completeness = completeness_of(make_cases({(TP, TP): 29}), VARIED_29, 4)

    assert completeness.method == "bootstrap"


def test_completeness_mean_is_share_of_items_satisfied() -> None:
    completeness = completeness_of(make_cases({(TP, TP): 2}), [1, 4], 4)

    assert completeness.mean == 0.625


def test_completeness_with_40_equal_values_has_no_interval() -> None:
    completeness = completeness_of(make_cases({(TP, TP): 40}), [2] * 40, 4)

    assert completeness == Completeness(0.5, None, 40, None)


def test_completeness_with_10_equal_values_has_no_interval() -> None:
    completeness = completeness_of(make_cases({(TP, TP): 10}), [2] * 10, 4)

    assert completeness == Completeness(0.5, None, 10, None)


def test_completeness_of_one_case_has_no_interval() -> None:
    completeness = completeness_of(make_cases({(TP, TP): 1}), [3], 4)

    assert completeness == Completeness(0.75, None, 1, None)


def test_completeness_without_checklist_is_none() -> None:
    metrics = slice_of({(TP, TP): 5})

    assert metrics.completeness is None


def test_completeness_with_no_cases_is_none() -> None:
    metrics = compute_slice([], {}, 4)

    assert metrics.completeness is None


def test_completeness_counts_case_without_agent_verdict() -> None:
    completeness = completeness_of(make_cases({(TP, TP): 2, (TP, None): 1}), [1, 1, 1], 4)

    assert completeness.n == 3


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


# Full report

W38 = "2026-W38"
W39 = "2026-W39"
W40 = "2026-W40"
UNREADABLE = IssueKind.UNREADABLE_ARGUMENTS
MISMATCH = IssueKind.RULE_TYPE_MISMATCH
UNKNOWN_TOOL = IssueKind.UNKNOWN_CHECKLIST_TOOL
NO_ARGUMENTS = IssueKind.MISSING_TOOL_ARGUMENTS
UNUSED = IssueKind.UNUSED_CHECKLIST


def at(year: int, month: int, day: int, hour: int = 12, minute: int = 0, second: int = 0) -> int:
    moment = datetime(year, month, day, hour, minute, second, tzinfo=UTC)
    return int(moment.timestamp()) * 1_000_000_000


IN_W38 = at(2026, 9, 15)
IN_W39 = at(2026, 9, 22)
IN_W40 = at(2026, 9, 29)


def make_call(
    tool: str = "alpha",
    arguments: str | Mapping[str, Any] | None = None,
    *,
    is_failed: bool = False,
) -> ToolCall:
    return ToolCall(
        span_id="00f0000000000001",
        tool_name=tool,
        arguments=arguments
        if arguments is None or isinstance(arguments, str)
        else json.dumps(arguments),
        start_ns=0,
        end_ns=100,
        is_failed=is_failed,
    )


def make_item(item_id: str, tool: str, args: Mapping[str, Any] | None = None) -> dict[str, Any]:
    return {"id": item_id, "tool": tool, "args": dict(args or {})}


def make_checklist(*items: dict[str, Any], alert_class: str = "Phishing") -> Checklist:
    return Checklist.model_validate({"alert_class": alert_class, "items": list(items)})


# Items "a" (tool alpha) and "b" (tool beta) for the Phishing class.
TWO_ITEMS = {"phishing": make_checklist(make_item("a", "alpha"), make_item("b", "beta"))}


def report_of(
    cases: list[Case], checklists: Mapping[str, Checklist] | None = None
) -> MetricsReport:
    return compute_metrics(cases, checklists or {})[0]


def issues_of(
    cases: list[Case], checklists: Mapping[str, Checklist]
) -> list[tuple[IssueKind, str]]:
    return [(issue.kind, issue.subject) for issue in compute_metrics(cases, checklists)[1]]


def trend_keys(report: MetricsReport) -> list[tuple[str, TrendScope, str | None]]:
    return [(point.week, point.scope, point.version) for point in report.classes[0].trend]


def class_names(report: MetricsReport) -> tuple[str, ...]:
    return tuple(entry.alert_class for entry in report.classes)


def make_mixed_cases() -> list[Case]:
    generator = random.Random(3)
    verdicts = (TP, FP, BENIGN, None)
    return [
        make_case(
            f"case-{index:03d}",
            generator.choice(verdicts),
            generator.choice(verdicts),
            generator.choice(("Phishing", "phishing ", "Malware", "Recon")),
            version=generator.choice(("v1", "v2", None)),
            start_ns=generator.choice((IN_W38, IN_W39, IN_W40)),
            calls=(
                make_call("alpha", generator.choice(({"window": 48}, "{bad", {"window": "2d"}))),
                make_call("beta", is_failed=generator.random() < 0.3),
            ),
        )
        for index in range(60)
    ]


MIXED_CHECKLISTS = {
    "phishing": make_checklist(
        make_item("a", "alpha", {"window": {"min_duration": "24h"}}),
        make_item("b", "beta"),
        make_item("c", "ghost"),
    ),
    "malware": make_checklist(make_item("a", "beta"), alert_class="Malware"),
    "insider": make_checklist(make_item("a", "alpha"), alert_class="Insider"),
}


@pytest.mark.parametrize(
    ("start_ns", "expected"),
    [
        (at(2026, 9, 20, 23, 59, 59), "2026-W38"),
        (at(2026, 9, 21, 0, 0, 0), "2026-W39"),
        (at(2026, 12, 31), "2026-W53"),
        (at(2027, 1, 2), "2026-W53"),
        (at(2027, 1, 4), "2027-W01"),
    ],
)
def test_iso_week_uses_utc_iso_weeks(start_ns: int, expected: str) -> None:
    assert iso_week(start_ns) == expected


def test_classes_differing_in_case_or_space_share_one_report() -> None:
    cases = [
        make_case("a-1", TP, TP, "Impossible Travel"),
        make_case("b-2", TP, TP, " impossible   TRAVEL"),
    ]

    assert len(report_of(cases).classes) == 1


def test_class_display_is_first_case_by_case_id() -> None:
    cases = [
        make_case("b-2", TP, TP, "Impossible Travel"),
        make_case("a-1", TP, TP, "impossible travel"),
    ]

    assert class_names(report_of(cases)) == ("impossible travel",)


def test_classes_are_sorted_by_normalized_class() -> None:
    cases = [make_case("a-1", TP, TP, "b"), make_case("b-2", TP, TP, "A")]

    assert class_names(report_of(cases)) == ("A", "b")


def test_versions_are_ordered_by_first_case_start() -> None:
    cases = [
        make_case("a-1", TP, TP, version="v1", start_ns=IN_W39),
        make_case("a-2", TP, TP, version="v2", start_ns=IN_W38),
        make_case("a-3", TP, TP, version=None, start_ns=IN_W40),
    ]

    assert report_of(cases).classes[0].versions == ("v2", "v1", None)


def test_versions_starting_together_are_ordered_by_name() -> None:
    cases = [make_case("a-1", TP, TP, version="b"), make_case("a-2", TP, TP, version="a")]

    assert report_of(cases).classes[0].versions == ("a", "b")


def test_no_version_and_a_version_named_unknown_stay_separate() -> None:
    cases = [make_case("a-1", TP, TP, version="unknown"), make_case("a-2", TP, TP, version=None)]

    assert report_of(cases).classes[0].versions == (None, "unknown")


def test_eight_or_more_versions_are_all_kept() -> None:
    cases = [
        make_case(f"a-{index}", TP, TP, version=f"v{index}", start_ns=index) for index in range(9)
    ]

    assert len(report_of(cases).classes[0].versions) == 9


def test_ab_versions_have_points_in_the_same_weeks() -> None:
    cases = [
        make_case("a-1", TP, TP, version="v1", start_ns=IN_W38),
        make_case("a-2", TP, TP, version="v2", start_ns=IN_W38),
        make_case("a-3", TP, TP, version="v1", start_ns=IN_W39),
        make_case("a-4", TP, TP, version="v2", start_ns=IN_W39),
    ]

    assert trend_keys(report_of(cases)) == [
        (W38, ALL, None),
        (W38, VERSION, "v1"),
        (W38, VERSION, "v2"),
        (W39, ALL, None),
        (W39, VERSION, "v1"),
        (W39, VERSION, "v2"),
    ]


def test_rollback_marks_the_first_week_of_each_version() -> None:
    cases = [
        make_case("a-1", TP, TP, version="v1", start_ns=IN_W38),
        make_case("a-2", TP, TP, version="v2", start_ns=IN_W39),
        make_case("a-3", TP, TP, version="v1", start_ns=IN_W40),
    ]

    assert report_of(cases).classes[0].version_first_week == {"v1": W38, "v2": W39}


def test_first_week_is_the_earliest_start_not_the_first_case_id() -> None:
    cases = [
        make_case("a-1", TP, TP, version="v1", start_ns=IN_W40),
        make_case("a-2", TP, TP, version="v1", start_ns=IN_W38),
    ]

    assert report_of(cases).classes[0].version_first_week["v1"] == W38


def test_rollback_version_has_points_in_both_periods() -> None:
    cases = [
        make_case("a-1", TP, TP, version="v1", start_ns=IN_W38),
        make_case("a-2", TP, TP, version="v2", start_ns=IN_W39),
        make_case("a-3", TP, TP, version="v1", start_ns=IN_W40),
    ]

    assert trend_keys(report_of(cases)) == [
        (W38, ALL, None),
        (W38, VERSION, "v1"),
        (W39, ALL, None),
        (W39, VERSION, "v2"),
        (W40, ALL, None),
        (W40, VERSION, "v1"),
    ]


def test_trend_point_averages_completeness_and_agreement_of_the_week() -> None:
    cases = [
        make_case("a-1", TP, TP, start_ns=IN_W38, calls=(make_call("alpha"), make_call("beta"))),
        make_case("a-2", TP, FP, start_ns=IN_W38, calls=(make_call("alpha"),)),
    ]

    assert report_of(cases, TWO_ITEMS).classes[0].trend[0] == WeekPoint(
        W38, ALL, None, 0.75, 2, 0.5, 2
    )


def test_trend_version_point_averages_only_that_version() -> None:
    cases = [
        make_case(
            "a-1",
            TP,
            TP,
            version="v1",
            start_ns=IN_W38,
            calls=(make_call("alpha"), make_call("beta")),
        ),
        make_case("a-2", TP, FP, version="v2", start_ns=IN_W38, calls=(make_call("alpha"),)),
    ]

    assert report_of(cases, TWO_ITEMS).classes[0].trend[2] == WeekPoint(
        W38, VERSION, "v2", 0.5, 1, 0.0, 1
    )


def test_trend_orders_versions_by_first_start_not_name() -> None:
    cases = [
        make_case("a-1", TP, TP, version="v1", start_ns=at(2026, 9, 15, 12)),
        make_case("a-2", TP, TP, version="v2", start_ns=at(2026, 9, 15, 11)),
    ]

    assert trend_keys(report_of(cases)) == [
        (W38, ALL, None),
        (W38, VERSION, "v2"),
        (W38, VERSION, "v1"),
    ]


def test_all_versions_trend_point_has_all_scope_and_no_version() -> None:
    cases = [make_case("a-1", TP, TP, version="v1", start_ns=IN_W38)]
    point = report_of(cases).classes[0].trend[0]

    assert (point.scope, point.version) == (ALL, None)


def test_trend_point_for_cases_without_version_has_version_scope_and_no_version() -> None:
    cases = [make_case("a-1", TP, TP, version=None, start_ns=IN_W38)]
    point = report_of(cases).classes[0].trend[1]

    assert (point.scope, point.version) == (VERSION, None)


def test_trend_point_without_agreement_cases_has_no_agreement() -> None:
    cases = [make_case("a-1", TP, None, start_ns=IN_W38)]
    point = report_of(cases).classes[0].trend[0]

    assert (point.agreement, point.agreement_n) == (None, 0)


def test_class_without_checklist_has_no_trend_completeness() -> None:
    point = report_of([make_case("a-1", TP, TP)]).classes[0].trend[0]

    assert (point.completeness, point.completeness_n) == (None, 0)


def test_class_without_checklist_has_no_skipped_rates() -> None:
    entry = report_of([make_case("a-1", TP, TP)]).classes[0]

    assert (entry.checklist_item_ids, entry.skipped_overall, entry.skipped_by_version) == (
        (),
        (),
        {},
    )


SKIP_CASES = [
    make_case("a-1", TP, TP, version="v1", calls=(make_call("alpha"), make_call("beta"))),
    make_case("a-2", TP, TP, version="v1", calls=(make_call("alpha", is_failed=True),)),
    make_case("a-3", TP, TP, version="v2", start_ns=1, calls=(make_call("alpha"),)),
]


def test_skipped_steps_per_version_count_failed_and_missed() -> None:
    assert report_of(SKIP_CASES, TWO_ITEMS).classes[0].skipped_by_version == {
        "v1": (SkipRate("a", 1, 2, 0.5), SkipRate("b", 1, 2, 0.5)),
        "v2": (SkipRate("a", 0, 1, 0.0), SkipRate("b", 1, 1, 1.0)),
    }


def test_skipped_steps_overall_cover_every_version() -> None:
    assert report_of(SKIP_CASES, TWO_ITEMS).classes[0].skipped_overall == (
        SkipRate("a", 1, 3, 1 / 3),
        SkipRate("b", 2, 3, 2 / 3),
    )


def test_overall_metrics_are_the_slice_of_every_case() -> None:
    report = report_of(SKIP_CASES, TWO_ITEMS)

    assert report.classes[0].overall == compute_slice(SKIP_CASES, report.evidence, 2)


def test_version_metrics_are_the_slice_of_that_version() -> None:
    report = report_of(SKIP_CASES, TWO_ITEMS)

    assert report.classes[0].by_version["v2"] == compute_slice(SKIP_CASES[2:], report.evidence, 2)


def test_evidence_covers_only_classes_with_a_checklist() -> None:
    cases = [make_case("a-1", TP, TP), make_case("b-1", TP, TP, "Malware")]

    assert sorted(report_of(cases, TWO_ITEMS).evidence) == ["a-1"]


def test_evidence_issues_are_reported() -> None:
    checklists = {"phishing": make_checklist(make_item("a", "alpha", {"x": {"exists": True}}))}
    cases = [make_case("a-1", TP, TP, calls=(make_call("alpha", "{bad"),))]

    assert issues_of(cases, checklists) == [(UNREADABLE, "a-1")]


def test_checklist_tool_no_case_calls_is_reported_per_item() -> None:
    checklists = {"phishing": make_checklist(make_item("a", "alpha"), make_item("b", "ghost"))}
    cases = [make_case("a-1", TP, TP, calls=(make_call("alpha"),))]

    assert issues_of(cases, checklists) == [(UNKNOWN_TOOL, "Phishing/b")]


def test_unknown_tool_detail_is_the_bare_tool_name() -> None:
    checklists = {"phishing": make_checklist(make_item("a", "ghost"))}
    cases = [make_case("a-1", TP, TP)]

    assert compute_metrics(cases, checklists)[1][0].detail == "ghost"


def test_unknown_tool_reaches_the_summary_grouped_under_its_name() -> None:
    checklists = {
        "phishing": make_checklist(make_item("a", "ghost")),
        "malware": make_checklist(make_item("b", "ghost"), alert_class="Malware"),
    }
    cases = [make_case("a-1", TP, TP), make_case("b-1", TP, TP, "Malware")]

    [line] = summarize_issues(compute_metrics(cases, checklists)[1])

    assert line.message == (
        "2 checklist items require the tool 'ghost', which no case calls. "
        "Check the tool name's spelling in the checklist."
    )


def test_unknown_tool_with_a_newline_in_its_name_keeps_the_whole_name() -> None:
    checklists = {"phishing": make_checklist(make_item("a", "gho\nst"))}
    cases = [make_case("a-1", TP, TP)]

    [line] = summarize_issues(compute_metrics(cases, checklists)[1])

    assert line.terminal_message.startswith("1 checklist item requires the tool 'gho\\x0ast'")


def test_checklist_tool_called_in_another_class_is_known() -> None:
    checklists = {"phishing": make_checklist(make_item("a", "alpha"))}
    cases = [
        make_case("a-1", TP, TP),
        make_case("b-1", TP, TP, "Malware", calls=(make_call("alpha"),)),
    ]

    assert issues_of(cases, checklists) == []


def test_checklist_without_cases_has_no_report_entry() -> None:
    checklists = {"malware": make_checklist(make_item("a", "alpha"), alert_class="Malware")}
    cases = [make_case("a-1", TP, TP, calls=(make_call("alpha"),))]

    assert class_names(report_of(cases, checklists)) == ("Phishing",)


def test_checklist_without_cases_is_reported_once() -> None:
    checklists = {"malware": make_checklist(make_item("a", "alpha"), alert_class="Malware")}
    cases = [make_case("a-1", TP, TP, calls=(make_call("alpha"),))]

    assert issues_of(cases, checklists) == [(UNUSED, "Malware")]


def test_rule_type_mismatch_is_reported_once_per_item() -> None:
    checklists = {
        "phishing": make_checklist(make_item("a", "alpha", {"window": {"min_duration": "24h"}}))
    }
    cases = [
        make_case("a-1", TP, TP, calls=(make_call("alpha", {"window": 48}),)),
        make_case("a-2", TP, TP, calls=(make_call("alpha", {"window": 72}),)),
    ]

    assert issues_of(cases, checklists) == [(MISMATCH, "Phishing/a")]


def test_issues_follow_evidence_mismatch_arguments_unknown_tool_unused_order() -> None:
    checklists = {
        "phishing": make_checklist(
            make_item("a", "alpha", {"window": {"min_duration": "24h"}}),
            make_item("b", "ghost"),
            make_item("c", "gamma", {"window": {"exists": True}}),
        ),
        "malware": make_checklist(make_item("a", "alpha"), alert_class="Malware"),
    }
    cases = [
        make_case("a-1", TP, TP, calls=(make_call("alpha", {"window": 48}),)),
        make_case("a-2", TP, TP, calls=(make_call("alpha", "{bad"), make_call("gamma"))),
    ]

    assert issues_of(cases, checklists) == [
        (UNREADABLE, "a-2"),
        (MISMATCH, "Phishing/a"),
        (NO_ARGUMENTS, "Phishing/c"),
        (UNKNOWN_TOOL, "Phishing/b"),
        (UNUSED, "Malware"),
    ]


def test_shuffled_input_gives_identical_report_and_issues() -> None:
    cases = make_mixed_cases()
    shuffled = list(cases)
    random.Random(1).shuffle(shuffled)

    assert compute_metrics(shuffled, MIXED_CHECKLISTS) == compute_metrics(cases, MIXED_CHECKLISTS)


# The other version group

# A group is (version, case count, first start). Case IDs are "<version>-<index>", verdicts and
# tool calls follow the index, so pooled slices have varied verdicts and completeness.
VersionGroup = tuple[str | None, int, int]
PATTERN_VERDICTS = (TP, FP, BENIGN)


def make_version_cases(*groups: VersionGroup) -> list[Case]:
    return [
        make_case(
            f"{version}-{index:03d}",
            PATTERN_VERDICTS[index % 3],
            PATTERN_VERDICTS[index % 2],
            version=version,
            start_ns=start_ns,
            calls=(make_call("alpha"), make_call("beta"))
            if index % 2 == 0
            else (make_call("alpha"),),
        )
        for version, count, start_ns in groups
        for index in range(count)
    ]


def other_trend_keys(report: MetricsReport) -> list[tuple[str, TrendScope, str | None]]:
    return [key for key in trend_keys(report) if key[1] is OTHER]


SIX_VERSIONS: tuple[VersionGroup, ...] = (
    ("v0", 10, 0),
    ("v1", 9, 10),
    ("v2", 8, 20),
    ("v3", 7, 30),
    ("v4", 6, 40),
    ("v5", 5, 50),
)
POOLED_VERSIONS: tuple[VersionGroup, ...] = (("v6", 4, 60), ("v7", 3, 70))
# Counts 10 down to 3, first starts in the reverse order, so rank and display order differ.
EIGHT_REVERSED: tuple[VersionGroup, ...] = tuple(
    (f"v{index}", 10 - index, 7 - index) for index in range(8)
)


def test_six_versions_have_no_other_group() -> None:
    entry = report_of(make_version_cases(*SIX_VERSIONS)).classes[0]

    assert entry.other is None


def test_six_versions_are_all_shown() -> None:
    entry = report_of(make_version_cases(*SIX_VERSIONS)).classes[0]

    assert entry.shown_versions == ("v0", "v1", "v2", "v3", "v4", "v5")


def test_six_versions_pool_nothing() -> None:
    entry = report_of(make_version_cases(*SIX_VERSIONS)).classes[0]

    assert entry.other_versions == ()


def test_top_six_by_count_are_shown_in_first_start_order() -> None:
    entry = report_of(make_version_cases(*EIGHT_REVERSED)).classes[0]

    assert entry.shown_versions == ("v5", "v4", "v3", "v2", "v1", "v0")


def test_versions_beyond_top_six_are_pooled_in_first_start_order() -> None:
    entry = report_of(make_version_cases(*EIGHT_REVERSED)).classes[0]

    assert entry.other_versions == ("v7", "v6")


def test_pooled_versions_keep_their_own_version_metrics() -> None:
    entry = report_of(make_version_cases(*EIGHT_REVERSED)).classes[0]

    assert entry.by_version["v7"].case_count == 3


def test_tie_at_the_cut_goes_to_the_earlier_first_start() -> None:
    cases = make_version_cases(
        *(("a", 5, 0), ("b", 5, 1), ("c", 5, 2), ("d", 5, 3), ("e", 5, 4)),
        ("late", 2, 20),
        ("early", 2, 10),
    )

    assert report_of(cases).classes[0].other_versions == ("late",)


def test_tie_at_the_cut_with_the_same_start_goes_to_the_name() -> None:
    cases = make_version_cases(
        *(("a", 5, 0), ("b", 5, 1), ("c", 5, 2), ("d", 5, 3), ("e", 5, 4)),
        ("y", 2, 10),
        ("x", 2, 10),
    )

    assert report_of(cases).classes[0].other_versions == ("y",)


def test_tie_at_the_cut_ignores_the_order_cases_arrive_in() -> None:
    # Cases are grouped in case ID order, so these IDs make "y" arrive before "x".
    tied = [
        make_case("0-y", TP, TP, version="y", start_ns=10),
        make_case("1-y", FP, FP, version="y", start_ns=10),
        make_case("2-x", TP, TP, version="x", start_ns=10),
        make_case("3-x", FP, FP, version="x", start_ns=10),
    ]
    cases = tied + make_version_cases(
        *(("a", 5, 0), ("b", 5, 1), ("c", 5, 2), ("d", 5, 3)),
        ("early", 2, 5),
        ("late", 2, 20),
    )

    assert report_of(cases).classes[0].other_versions == ("y", "late")


def test_no_version_is_shown_without_taking_one_of_the_six_places() -> None:
    cases = make_version_cases(*SIX_VERSIONS, ("v6", 4, 60), (None, 20, 25))

    assert report_of(cases).classes[0].shown_versions == (
        "v0",
        "v1",
        "v2",
        None,
        "v3",
        "v4",
        "v5",
    )


def test_no_version_does_not_count_toward_the_six() -> None:
    cases = make_version_cases(*SIX_VERSIONS, ("v6", 4, 60), (None, 20, 25))

    assert report_of(cases).classes[0].other_versions == ("v6",)


def test_no_version_with_the_fewest_cases_is_never_pooled() -> None:
    cases = make_version_cases(*SIX_VERSIONS, *POOLED_VERSIONS, (None, 1, 80))

    assert report_of(cases).classes[0].shown_versions[-1] is None


def test_no_version_alongside_six_real_versions_gives_no_other_group() -> None:
    cases = make_version_cases(*SIX_VERSIONS, (None, 1, 80))

    assert report_of(cases).classes[0].other is None


def test_real_version_named_other_is_shown_as_a_normal_version() -> None:
    cases = make_version_cases(("other", 20, 0), *SIX_VERSIONS[1:], *POOLED_VERSIONS)

    assert report_of(cases).classes[0].shown_versions == ("other", "v1", "v2", "v3", "v4", "v5")


def test_real_version_named_other_stays_separate_from_the_pooled_group() -> None:
    cases = make_version_cases(("other", 20, 0), *SIX_VERSIONS[1:], *POOLED_VERSIONS)
    entry = report_of(cases).classes[0]

    assert entry.other is not None and (
        entry.by_version["other"].case_count,
        entry.other.case_count,
    ) == (20, 7)


def test_other_slice_is_the_slice_of_the_pooled_cases() -> None:
    pooled = make_version_cases(*POOLED_VERSIONS)
    report = report_of(make_version_cases(*SIX_VERSIONS) + pooled, TWO_ITEMS)

    assert report.classes[0].other == compute_slice(pooled, report.evidence, 2)


def test_trend_has_one_other_point_per_week_with_an_other_version_case() -> None:
    cases = make_version_cases(
        *SIX_VERSIONS, ("v6", 4, IN_W39), ("v7", 1, IN_W39), ("v8", 1, IN_W40)
    )

    assert other_trend_keys(report_of(cases)) == [(W39, OTHER, None), (W40, OTHER, None)]


def test_trend_shows_other_after_the_shown_versions_instead_of_pooled_versions() -> None:
    cases = make_version_cases(*EIGHT_REVERSED)

    assert trend_keys(report_of(cases)) == [
        ("1970-W01", ALL, None),
        ("1970-W01", VERSION, "v5"),
        ("1970-W01", VERSION, "v4"),
        ("1970-W01", VERSION, "v3"),
        ("1970-W01", VERSION, "v2"),
        ("1970-W01", VERSION, "v1"),
        ("1970-W01", VERSION, "v0"),
        ("1970-W01", OTHER, None),
    ]


def test_other_trend_point_averages_the_pooled_cases_of_the_week() -> None:
    cases = make_version_cases(*SIX_VERSIONS, *POOLED_VERSIONS)

    assert report_of(cases, TWO_ITEMS).classes[0].trend[-1] == WeekPoint(
        "1970-W01", OTHER, None, 11 / 14, 7, 4 / 7, 7
    )


def test_skipped_steps_for_other_pool_the_counts() -> None:
    cases = make_version_cases(*SIX_VERSIONS, *POOLED_VERSIONS)

    assert report_of(cases, TWO_ITEMS).classes[0].skipped_other == (
        SkipRate("a", 0, 7, 0.0),
        SkipRate("b", 3, 7, 3 / 7),
    )


def test_skipped_steps_for_other_are_empty_without_an_other_group() -> None:
    entry = report_of(make_version_cases(*SIX_VERSIONS), TWO_ITEMS).classes[0]

    assert entry.skipped_other == ()
