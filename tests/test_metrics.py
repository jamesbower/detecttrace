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
    Kappa,
    KappaNote,
    MetricsReport,
    SkipRate,
    SliceMetrics,
    WeekPoint,
    compute_metrics,
    compute_slice,
    iso_week,
)
from detecttrace.model import Case, IssueKind, ToolCall, Verdict
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
    case_id: str,
    analyst: Verdict | None,
    agent: Verdict | None,
    alert_class: str = "Phishing",
    *,
    version: str = "v1",
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


# Full report

W38 = "2026-W38"
W39 = "2026-W39"
W40 = "2026-W40"
UNREADABLE = IssueKind.UNREADABLE_ARGUMENTS
MISMATCH = IssueKind.RULE_TYPE_MISMATCH
UNKNOWN_TOOL = IssueKind.UNKNOWN_CHECKLIST_TOOL
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


def trend_keys(report: MetricsReport) -> list[tuple[str, str | None]]:
    return [(point.week, point.version) for point in report.classes[0].trend]


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
            version=generator.choice(("v1", "v2", "unknown")),
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
        make_case("a-3", TP, TP, version="unknown", start_ns=IN_W40),
    ]

    assert report_of(cases).classes[0].versions == ("v2", "v1", "unknown")


def test_versions_starting_together_are_ordered_by_name() -> None:
    cases = [make_case("a-1", TP, TP, version="b"), make_case("a-2", TP, TP, version="a")]

    assert report_of(cases).classes[0].versions == ("a", "b")


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
        (W38, None),
        (W38, "v1"),
        (W38, "v2"),
        (W39, None),
        (W39, "v1"),
        (W39, "v2"),
    ]


def test_rollback_marks_the_first_week_of_each_version() -> None:
    cases = [
        make_case("a-1", TP, TP, version="v1", start_ns=IN_W38),
        make_case("a-2", TP, TP, version="v2", start_ns=IN_W39),
        make_case("a-3", TP, TP, version="v1", start_ns=IN_W40),
    ]

    assert report_of(cases).classes[0].version_first_week == {"v1": W38, "v2": W39}


def test_rollback_version_has_points_in_both_periods() -> None:
    cases = [
        make_case("a-1", TP, TP, version="v1", start_ns=IN_W38),
        make_case("a-2", TP, TP, version="v2", start_ns=IN_W39),
        make_case("a-3", TP, TP, version="v1", start_ns=IN_W40),
    ]

    assert trend_keys(report_of(cases)) == [
        (W38, None),
        (W38, "v1"),
        (W39, None),
        (W39, "v2"),
        (W40, None),
        (W40, "v1"),
    ]


def test_trend_point_averages_completeness_and_agreement_of_the_week() -> None:
    cases = [
        make_case("a-1", TP, TP, start_ns=IN_W38, calls=(make_call("alpha"), make_call("beta"))),
        make_case("a-2", TP, FP, start_ns=IN_W38, calls=(make_call("alpha"),)),
    ]

    assert report_of(cases, TWO_ITEMS).classes[0].trend[0] == WeekPoint(W38, None, 0.75, 2, 0.5, 2)


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


def test_issues_follow_evidence_mismatch_unknown_tool_unused_order() -> None:
    checklists = {
        "phishing": make_checklist(
            make_item("a", "alpha", {"window": {"min_duration": "24h"}}), make_item("b", "ghost")
        ),
        "malware": make_checklist(make_item("a", "alpha"), alert_class="Malware"),
    }
    cases = [
        make_case("a-1", TP, TP, calls=(make_call("alpha", {"window": 48}),)),
        make_case("a-2", TP, TP, calls=(make_call("alpha", "{bad"),)),
    ]

    assert issues_of(cases, checklists) == [
        (UNREADABLE, "a-2"),
        (MISMATCH, "Phishing/a"),
        (UNKNOWN_TOOL, "Phishing/b"),
        (UNUSED, "Malware"),
    ]


def test_shuffled_input_gives_identical_report_and_issues() -> None:
    cases = make_mixed_cases()
    shuffled = list(cases)
    random.Random(1).shuffle(shuffled)

    assert compute_metrics(shuffled, MIXED_CHECKLISTS) == compute_metrics(cases, MIXED_CHECKLISTS)
