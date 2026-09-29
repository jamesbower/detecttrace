import json
from collections.abc import Mapping
from typing import Any

import pytest

from detecttrace.checklist import Checklist
from detecttrace.evidence import (
    CaseEvidence,
    ItemOutcome,
    ItemStatus,
    MissedReason,
    evaluate_case,
    find_missing_tool_arguments,
    find_rule_type_mismatches,
    json_equal,
)
from detecttrace.model import Case, Issue, IssueKind, ToolCall

TOOL = "get_signin_logs"
SPAN = "00f0000000000001"
SATISFIED = ItemOutcome(ItemStatus.SATISFIED, None)
FAILED = ItemOutcome(ItemStatus.FAILED, None)
NOT_CALLED = ItemOutcome(ItemStatus.MISSED, MissedReason.NOT_CALLED)
WRONG_ARGUMENTS = ItemOutcome(ItemStatus.MISSED, MissedReason.WRONG_ARGUMENTS)


def make_call(
    arguments: str | Mapping[str, Any] | None = None,
    *,
    tool: str = TOOL,
    span_id: str = SPAN,
    is_failed: bool = False,
) -> ToolCall:
    return ToolCall(
        span_id=span_id,
        tool_name=tool,
        arguments=arguments
        if arguments is None or isinstance(arguments, str)
        else json.dumps(arguments),
        start_ns=0,
        end_ns=100,
        is_failed=is_failed,
    )


def make_case(
    *calls: ToolCall, case_id: str = "case-1", alert_class: str = "Impossible_Travel"
) -> Case:
    return Case(
        case_id=case_id,
        alert_class=alert_class,
        prompt_version="v1",
        analyst_verdict=None,
        agent_verdict=None,
        start_ns=0,
        tool_calls=calls,
        is_incomplete_trace=False,
    )


def make_item(args: Mapping[str, Any] | None = None, *, item_id: str = "signin_history") -> dict:
    return {"id": item_id, "tool": TOOL, "args": dict(args or {})}


def make_checklist(*items: dict, alert_class: str = "impossible_travel") -> Checklist:
    return Checklist.model_validate({"alert_class": alert_class, "items": list(items)})


def outcomes(items: list[dict], *calls: ToolCall) -> tuple[ItemOutcome, ...]:
    return evaluate_case(make_case(*calls), make_checklist(*items), []).outcomes


def reported(items: list[dict], *calls: ToolCall) -> list[Issue]:
    issues: list[Issue] = []
    evaluate_case(make_case(*calls), make_checklist(*items), issues)
    return issues


def passes(rules: Mapping[str, Any], arguments: str | Mapping[str, Any] | None) -> bool:
    return outcomes([make_item(rules)], make_call(arguments)) == (SATISFIED,)


def reported_kinds(rules: Mapping[str, Any], arguments: str | Mapping[str, Any] | None) -> list:
    return [issue.kind for issue in reported([make_item(rules)], make_call(arguments))]


# Item status


def test_item_without_args_is_satisfied_by_one_successful_call():
    assert outcomes([make_item()], make_call()) == (SATISFIED,)


def test_tool_never_called_is_missed_as_not_called():
    assert outcomes([make_item()], make_call(tool="check_mfa_status")) == (NOT_CALLED,)


def test_only_a_failed_call_is_failed():
    assert outcomes([make_item()], make_call(is_failed=True)) == (FAILED,)


def test_failed_call_then_passing_call_is_satisfied():
    calls = (make_call(is_failed=True), make_call(span_id="00f0000000000002"))
    assert outcomes([make_item()], *calls) == (SATISFIED,)


def test_wrong_arguments_plus_a_failed_call_is_failed():
    calls = (
        make_call({"range": "12h"}),
        make_call({"range": "24h"}, span_id="00f0000000000002", is_failed=True),
    )
    assert outcomes([make_item({"range": {"min_duration": "24h"}})], *calls) == (FAILED,)


def test_wrong_arguments_without_a_failed_call_is_missed_as_wrong_arguments():
    items = [make_item({"range": {"min_duration": "24h"}})]
    assert outcomes(items, make_call({"range": "12h"})) == (WRONG_ARGUMENTS,)


def test_unreadable_arguments_are_missed_as_wrong_arguments():
    items = [make_item({"range": {"exists": False}})]
    assert outcomes(items, make_call("{not json")) == (WRONG_ARGUMENTS,)


def test_tool_name_differing_only_in_case_is_not_called():
    assert outcomes([make_item()], make_call(tool="Get_Signin_Logs")) == (NOT_CALLED,)


def test_rules_passed_by_different_calls_are_not_combined():
    rules = {"range": {"min_duration": "24h"}, "tenant": {"equals": "example-prod"}}
    calls = (
        make_call({"range": "48h", "tenant": "example-dev"}),
        make_call({"range": "1h", "tenant": "example-prod"}, span_id="00f0000000000002"),
    )
    assert outcomes([make_item(rules)], *calls) == (WRONG_ARGUMENTS,)


def test_one_call_satisfies_two_items():
    items = [make_item(item_id="a"), make_item({"range": {"exists": True}}, item_id="b")]
    assert outcomes(items, make_call({"range": "24h"})) == (SATISFIED, SATISFIED)


def test_call_with_an_empty_tool_name_does_not_count_as_a_call_to_the_item_tool():
    assert outcomes([make_item()], make_call(tool="")) == (NOT_CALLED,)


def test_unreadable_arguments_plus_a_failed_call_is_failed():
    calls = (make_call("{not json"), make_call(span_id="00f0000000000002", is_failed=True))
    assert outcomes([make_item({"range": {"exists": True}})], *calls) == (FAILED,)


def test_satisfied_count_counts_satisfied_items():
    evidence = CaseEvidence("case-1", (SATISFIED, FAILED, SATISFIED, NOT_CALLED))
    assert evidence.satisfied_count == 2


def test_evaluate_case_keeps_the_case_id():
    evidence = evaluate_case(make_case(case_id="case-9"), make_checklist(make_item()), [])
    assert evidence.case_id == "case-9"


# json_equal


@pytest.mark.parametrize(
    ("left", "right", "expected"),
    [
        ("24", 24, False),
        (24, 24.0, True),
        (True, 1, False),
        (1, True, False),
        (False, 0.0, False),
        (True, True, True),
        (None, None, True),
        (None, "null", False),
        ([1, "a"], [1, "a"], True),
        ([1, "a"], [1, "a", 2], False),
        ([1, "a"], ["a", 1], False),
        ({"a": 1}, {"a": 1.0}, True),
        ({"a": 1}, {"a": 1, "b": 2}, False),
        ({"a": [True]}, {"a": [1]}, False),
        ([], {}, False),
        ("a", "a", True),
    ],
)
def test_json_equal_compares_strict_json_types(left, right, expected):
    assert json_equal(left, right) is expected


# equals, in, exists, matches


def test_equals_null_does_not_match_an_absent_path():
    assert not passes({"x": {"equals": None}}, {"y": 1})


def test_equals_null_matches_a_null_value():
    assert passes({"x": {"equals": None}}, {"x": None})


def test_in_with_mixed_types_matches_the_string():
    assert passes({"x": {"in": [24, "24"]}}, {"x": "24"})


def test_in_does_not_match_a_bool_against_a_number():
    assert not passes({"x": {"in": [1, 0]}}, {"x": True})


def test_exists_true_passes_on_a_null_value():
    assert passes({"x": {"exists": True}}, {"x": None})


def test_exists_false_passes_on_an_absent_path():
    assert passes({"x": {"exists": False}}, {"y": 1})


def test_exists_false_passes_when_the_call_has_no_arguments():
    assert passes({"x": {"exists": False}}, None)


def test_exists_true_on_top_level_fails_when_the_call_has_no_arguments():
    assert not passes({"$": {"exists": True}}, None)


def test_matches_finds_the_pattern_in_the_middle_of_a_string():
    assert passes({"q": {"matches": "ago\\("}}, {"q": "T | where t > ago(1d)"})


def test_matches_honours_an_inline_ignorecase_flag():
    assert passes({"q": {"matches": "(?i)signinlogs"}}, {"q": "SigninLogs | take 1"})


def test_matches_is_case_sensitive_by_default():
    assert not passes({"q": {"matches": "signinlogs"}}, {"q": "SigninLogs | take 1"})


def test_matches_fails_on_a_number():
    assert not passes({"q": {"matches": "24"}}, {"q": 24})


def test_matches_on_a_number_reports_nothing():
    assert reported_kinds({"q": {"matches": "24"}}, {"q": 24}) == []


def test_matches_searches_a_one_megabyte_string():
    assert passes({"q": {"matches": "needle$"}}, {"q": "a" * 1_000_000 + "needle"})


# Paths


def test_nested_path_is_read():
    assert passes({"time.range": {"equals": "24h"}}, {"time": {"range": "24h"}})


def test_list_index_path_is_read():
    arguments = {"filters": [{"field": "user"}]}
    assert passes({"filters[0].field": {"equals": "user"}}, arguments)


def test_list_index_out_of_range_fails():
    assert not passes({"filters[1].field": {"exists": True}}, {"filters": [{"field": "user"}]})


def test_key_path_into_a_list_is_absent():
    assert passes({"filters.field": {"exists": False}}, {"filters": [{"field": "user"}]})


def test_top_level_path_reads_a_json_list():
    assert passes({"$": {"equals": ["a", 1]}}, '["a", 1]')


def test_arguments_stored_as_a_map_are_used_directly():
    call = ToolCall(SPAN, TOOL, {"time": {"range": "24h"}}, 0, 100, False)
    assert outcomes([make_item({"time.range": {"min_duration": "24h"}})], call) == (SATISFIED,)


# min_duration with start and end


def test_top_level_start_and_end_25_hours_apart_pass_24h():
    rules = {"$": {"min_duration": "24h", "start": "start_time", "end": "end_time"}}
    arguments = {"start_time": "2026-09-01T00:00:00Z", "end_time": "2026-09-02T01:00:00Z"}
    assert passes(rules, arguments)


def test_start_and_end_are_read_under_the_key_path():
    rules = {"window": {"min_duration": "24h", "start": "start", "end": "end"}}
    arguments = {
        "start": "2026-09-01T00:00:00Z",
        "end": "2026-09-01T01:00:00Z",
        "window": {"start": "2026-09-01T00:00:00Z", "end": "2026-09-03T00:00:00Z"},
    }
    assert passes(rules, arguments)


def test_start_and_end_outside_the_key_path_are_not_read():
    rules = {"window": {"min_duration": "24h", "start": "start", "end": "end"}}
    arguments = {"start": "2026-09-01T00:00:00Z", "end": "2026-09-03T00:00:00Z", "window": {}}
    assert not passes(rules, arguments)


NAIVE_AND_AWARE = {"start": "2026-09-01T00:00:00", "end": "2026-09-03T00:00:00Z"}
WINDOW_RULE = {"$": {"min_duration": "24h", "start": "start", "end": "end"}}


def test_start_and_end_mixing_naive_and_aware_fail():
    assert not passes(WINDOW_RULE, NAIVE_AND_AWARE)


def test_start_and_end_mixing_naive_and_aware_report_unreadable_duration():
    assert reported_kinds(WINDOW_RULE, NAIVE_AND_AWARE) == [IssueKind.UNREADABLE_DURATION]


def test_missing_end_fails_without_a_report():
    assert reported_kinds(WINDOW_RULE, {"start": "2026-09-01T00:00:00Z"}) == []


def test_start_and_end_exactly_24_hours_apart_pass_24h():
    arguments = {"start": "2026-09-01T00:00:00Z", "end": "2026-09-02T00:00:00Z"}
    assert passes(WINDOW_RULE, arguments)


# min_duration on a value


@pytest.mark.parametrize("value", ["-24h", "P1D", "PT24H", "48h"])
def test_min_duration_passes_long_enough_durations(value):
    assert passes({"range": {"min_duration": "24h"}}, {"range": value})


def test_min_duration_fails_a_short_duration():
    assert not passes({"range": {"min_duration": "24h"}}, {"range": "12h"})


def test_min_duration_on_a_short_duration_reports_nothing():
    assert reported_kinds({"range": {"min_duration": "24h"}}, {"range": "12h"}) == []


def test_min_duration_fails_a_number():
    assert not passes({"range": {"min_duration": "24h"}}, {"range": 24})


def test_min_duration_on_a_number_reports_nothing_per_case():
    assert reported_kinds({"range": {"min_duration": "24h"}}, {"range": 24}) == []


def test_min_duration_reports_an_unreadable_duration():
    assert reported_kinds({"range": {"min_duration": "24h"}}, {"range": "soon"}) == [
        IssueKind.UNREADABLE_DURATION
    ]


def test_unreadable_duration_detail_names_the_item_and_the_call():
    issues = reported([make_item({"range": {"min_duration": "24h"}})], make_call({"range": "soon"}))
    assert issues == [
        Issue(
            IssueKind.UNREADABLE_DURATION,
            "case-1",
            f"signin_history: call {SPAN}: duration 'soon' could not be read",
        )
    ]


def test_unreadable_value_in_a_detail_is_shortened():
    issues = reported(
        [make_item({"range": {"min_duration": "24h"}})], make_call({"range": "x" * 1000})
    )
    assert (
        issues[0].detail
        == f"signin_history: call {SPAN}: duration '{'x' * 77}...' could not be read"
    )


# min and max


@pytest.mark.parametrize("value", [24, 24.0, 48])
def test_min_passes_numbers_at_or_above(value):
    assert passes({"hours": {"min": 24}}, {"hours": value})


@pytest.mark.parametrize("value", [23, True, "24", None])
def test_min_fails_smaller_numbers_and_non_numbers(value):
    assert not passes({"hours": {"min": 24}}, {"hours": value})


@pytest.mark.parametrize("value", [23, True, "24"])
def test_min_reports_nothing_per_case(value):
    assert reported_kinds({"hours": {"min": 24}}, {"hours": value}) == []


@pytest.mark.parametrize(("value", "expected"), [(48, True), (49, False)])
def test_max_is_inclusive(value, expected):
    assert passes({"hours": {"max": 48}}, {"hours": value}) is expected


def test_min_and_max_together_pass_a_value_between():
    assert passes({"hours": {"min": 24, "max": 48}}, {"hours": 36})


def test_min_and_max_under_one_key_must_both_pass():
    items = [make_item({"hours": {"min": 24, "max": 48}})]
    assert outcomes(items, make_call({"hours": 60})) == (WRONG_ARGUMENTS,)


# kql_min_ago


@pytest.mark.parametrize(
    "query", ["T | where t > ago(1d)", "T | where t > ago(24h)", "T | where t > ago(1.5d)"]
)
def test_kql_min_ago_passes_long_enough_lookbacks(query):
    assert passes({"query": {"kql_min_ago": "24h"}}, {"query": query})


def test_kql_min_ago_fails_a_short_lookback():
    assert not passes({"query": {"kql_min_ago": "24h"}}, {"query": "T | where t > ago(90m)"})


def test_kql_min_ago_on_a_short_lookback_reports_nothing():
    assert reported_kinds({"query": {"kql_min_ago": "24h"}}, {"query": "T | where ago(90m)"}) == []


def test_kql_min_ago_passes_when_any_lookback_is_long_enough():
    query = "T | where t > ago(1h) and t2 > ago(2d)"
    assert passes({"query": {"kql_min_ago": "24h"}}, {"query": query})


def test_kql_min_ago_fails_an_unreadable_lookback():
    assert not passes({"query": {"kql_min_ago": "24h"}}, {"query": "T | where t > ago(1day)"})


def test_kql_min_ago_reports_each_unreadable_lookback():
    query = "T | where t > ago(1day) or t < ago(2day)"
    assert reported_kinds({"query": {"kql_min_ago": "24h"}}, {"query": query}) == [
        IssueKind.UNREADABLE_KQL_TIMESPAN,
        IssueKind.UNREADABLE_KQL_TIMESPAN,
    ]


def test_kql_min_ago_reports_a_repeated_unreadable_lookback_once():
    query = "ago(x) " * 100_000
    assert len(reported_kinds({"query": {"kql_min_ago": "24h"}}, {"query": query})) == 1


def test_kql_min_ago_reports_at_most_five_unreadable_lookbacks_per_call():
    query = " ".join(f"ago({n}day)" for n in range(10))
    assert len(reported_kinds({"query": {"kql_min_ago": "24h"}}, {"query": query})) == 5


def test_kql_min_ago_reports_the_first_five_unreadable_lookbacks_in_sorted_order():
    query = "ago(f) ago(e) ago(d) ago(c) ago(b) ago(a)"
    issues = reported([make_item({"query": {"kql_min_ago": "24h"}})], make_call({"query": query}))
    prefix = f"signin_history: call {SPAN}: lookback"
    assert [issue.detail for issue in issues] == [
        f"{prefix} 'ago(a)' could not be read",
        f"{prefix} 'ago(b)' could not be read",
        f"{prefix} 'ago(c)' could not be read",
        f"{prefix} 'ago(d)' could not be read",
        f"{prefix} 'ago(e)' could not be read",
    ]


def test_unreadable_lookback_in_a_later_call_is_reported_after_an_earlier_call_passes():
    calls = (
        make_call({"query": "T | where ago(2d)"}),
        make_call({"query": "T | where ago(1day)"}, span_id="00f0000000000002"),
    )
    issues = reported([make_item({"query": {"kql_min_ago": "24h"}})], *calls)
    assert [issue.kind for issue in issues] == [IssueKind.UNREADABLE_KQL_TIMESPAN]


# Issue caps per call


FOUR_KQL_ITEMS = [
    make_item({"query": {"kql_min_ago": "24h"}}, item_id="a"),
    make_item({"query": {"kql_min_ago": "1h"}}, item_id="b"),
    make_item({"query": {"kql_min_ago": "7d"}}, item_id="c"),
    make_item({"query": {"kql_min_ago": "30m"}}, item_id="d"),
]


def test_unreadable_lookbacks_are_capped_per_call_across_items():
    query = " ".join(f"ago({n}day)" for n in range(10))
    assert len(reported(FOUR_KQL_ITEMS, make_call({"query": query}))) == 5


def test_an_unreadable_lookback_read_by_several_items_is_reported_once():
    assert len(reported(FOUR_KQL_ITEMS, make_call({"query": "ago(1day)"}))) == 1


def test_repeated_unreadable_duration_on_one_span_is_reported_once_per_case():
    items = [
        make_item({"range": {"min_duration": "24h"}}, item_id="a"),
        make_item({"range": {"min_duration": "1h"}}, item_id="b"),
    ]
    calls = (make_call({"range": "soon"}),) * 10_000
    assert len(reported(items, *calls)) == 1


def test_unreadable_durations_are_capped_at_five_per_call():
    rules = {key: {"min_duration": "24h"} for key in "abcdef"}
    arguments = {key: f"soon-{key}" for key in "abcdef"}
    assert len(reported_kinds(rules, arguments)) == 5


def test_issue_cap_is_counted_separately_per_kind():
    rules = {"range": {"min_duration": "24h"}, "query": {"kql_min_ago": "24h"}}
    query = " ".join(f"ago({n}day)" for n in range(10))
    assert len(reported_kinds(rules, {"range": "soon", "query": query})) == 6


def test_the_same_unreadable_duration_in_two_calls_is_reported_for_each_call():
    calls = (make_call({"range": "soon"}), make_call({"range": "soon"}, span_id="00f0000000000002"))
    assert len(reported([make_item({"range": {"min_duration": "24h"}})], *calls)) == 2


# Unreadable arguments


def test_unreadable_arguments_are_reported_once_per_call_across_items():
    items = [
        make_item({"a": {"exists": True}}, item_id="a"),
        make_item({"b": {"exists": True}}, item_id="b"),
    ]
    assert reported(items, make_call("{not json")) == [
        Issue(
            IssueKind.UNREADABLE_ARGUMENTS,
            "case-1",
            f"call {SPAN}: arguments for tool '{TOOL}' are not valid JSON",
        )
    ]


def test_arguments_nested_100000_deep_are_unreadable():
    arguments = "[" * 100_000 + "]" * 100_000
    assert reported_kinds({"x": {"exists": True}}, arguments) == [IssueKind.UNREADABLE_ARGUMENTS]


def test_arguments_with_a_10000_digit_integer_are_unreadable():
    arguments = '{"x": ' + "9" * 10_000 + "}"
    assert reported_kinds({"x": {"exists": True}}, arguments) == [IssueKind.UNREADABLE_ARGUMENTS]


@pytest.mark.parametrize("constant", ["NaN", "Infinity", "-Infinity"])
def test_arguments_with_a_non_finite_constant_are_unreadable(constant):
    issues = reported([make_item({"x": {"exists": True}})], make_call('{"x": ' + constant + "}"))
    assert issues == [
        Issue(
            IssueKind.UNREADABLE_ARGUMENTS,
            "case-1",
            f"call {SPAN}: arguments for tool '{TOOL}' contain NaN or Infinity, "
            "which is not valid JSON",
        )
    ]


def test_arguments_with_a_non_finite_constant_do_not_satisfy_an_item():
    assert outcomes([make_item({"x": {"exists": True}})], make_call('{"x": NaN}')) == (
        WRONG_ARGUMENTS,
    )


@pytest.mark.parametrize(
    "arguments",
    [{"x": float("nan")}, {"x": {"y": [1, float("nan")]}}, {"x": [float("-inf")]}],
)
def test_map_arguments_with_a_non_finite_number_are_unreadable(arguments):
    call = ToolCall(SPAN, TOOL, arguments, 0, 100, False)
    assert reported([make_item({"z": {"exists": False}})], call) == [
        Issue(
            IssueKind.UNREADABLE_ARGUMENTS,
            "case-1",
            f"call {SPAN}: arguments for tool '{TOOL}' contain NaN or Infinity, "
            "which is not valid JSON",
        )
    ]


def test_map_arguments_with_nan_do_not_satisfy_an_item():
    call = ToolCall(SPAN, TOOL, {"x": float("nan")}, 0, 100, False)
    assert outcomes([make_item({"z": {"exists": False}})], call) == (WRONG_ARGUMENTS,)


def test_item_without_args_does_not_read_arguments():
    assert reported([make_item()], make_call("{not json")) == []


# find_rule_type_mismatches


def test_mismatch_is_reported_once_per_item_across_many_cases():
    cases = [make_case(make_call({"range": 24}), case_id=f"case-{n:04d}") for n in range(1000)]
    checklists = {
        "impossible_travel": make_checklist(make_item({"range": {"min_duration": "24h"}}))
    }
    issues = find_rule_type_mismatches(cases, checklists)
    assert [issue.subject for issue in issues] == ["impossible_travel/signin_history"]


def mismatch_details(rules: Mapping[str, Any], *cases: Case) -> list[str]:
    checklists = {"impossible_travel": make_checklist(make_item(rules))}
    return [issue.detail for issue in find_rule_type_mismatches(cases, checklists)]


def test_mismatch_for_min_on_text_suggests_min_duration_or_equals():
    checklists = {"impossible_travel": make_checklist(make_item({"hours": {"min": 24}}))}
    issues = find_rule_type_mismatches([make_case(make_call({"hours": "24"}))], checklists)
    assert issues == [
        Issue(
            IssueKind.RULE_TYPE_MISMATCH,
            "impossible_travel/signin_history",
            "'hours' (min): the value is text; use min_duration or equals; "
            "first seen in case case-1",
        )
    ]


def test_mismatch_for_min_duration_on_a_number_suggests_min():
    details = mismatch_details(
        {"range": {"min_duration": "24h"}}, make_case(make_call({"range": 24}))
    )
    assert details == [
        "'range' (min_duration): the value is a number; use min; first seen in case case-1"
    ]


@pytest.mark.parametrize(
    ("value", "described"),
    [(None, "null"), (True, "a boolean"), ([24], "a list"), ({"h": 24}, "an object")],
)
def test_mismatch_for_min_and_max_on_other_values_suggests_no_rule(value, described):
    details = mismatch_details(
        {"hours": {"min": 1, "max": 48}}, make_case(make_call({"hours": value}))
    )
    assert details == [
        f"'hours' (min and max): the value is {described}; this rule can't read it; "
        "first seen in case case-1"
    ]


def test_mismatch_for_min_duration_on_a_list_suggests_no_rule():
    details = mismatch_details(
        {"range": {"min_duration": "24h"}}, make_case(make_call({"range": ["24h"]}))
    )
    assert details == [
        "'range' (min_duration): the value is a list; this rule can't read it; "
        "first seen in case case-1"
    ]


def test_mismatch_for_kql_min_ago_on_a_number_suggests_no_rule():
    details = mismatch_details(
        {"query": {"kql_min_ago": "24h"}}, make_case(make_call({"query": 24}))
    )
    assert details == [
        "'query' (kql_min_ago): the value is a number; this rule can't read it; "
        "first seen in case case-1"
    ]


def test_mismatch_names_the_first_case_with_a_mismatching_value():
    cases = (
        make_case(make_call({"hours": 24})),
        make_case(make_call({"hours": "24"}), case_id="case-2"),
        make_case(make_call({"hours": "48"}), case_id="case-3"),
    )
    assert mismatch_details({"hours": {"min": 24}}, *cases) == [
        "'hours' (min): the value is text; use min_duration or equals; first seen in case case-2"
    ]


def test_mismatch_is_found_in_a_failed_call():
    case = make_case(make_call({"hours": "24"}, is_failed=True))
    assert len(mismatch_details({"hours": {"min": 24}}, case)) == 1


def test_no_mismatch_from_unreadable_arguments_at_the_top_level():
    assert mismatch_details({"$": {"min": 24}}, make_case(make_call("{bad"))) == []


def test_no_mismatch_for_kql_min_ago_on_text():
    case = make_case(make_call({"query": "T | where ago(1h)"}))
    assert mismatch_details({"query": {"kql_min_ago": "24h"}}, case) == []


def test_mismatch_subject_uses_the_checklist_alert_class_as_written():
    checklists = {
        "impossible_travel": make_checklist(
            make_item({"hours": {"max": 48}}), alert_class="IMPOSSIBLE_TRAVEL"
        )
    }
    issues = find_rule_type_mismatches([make_case(make_call({"hours": True}))], checklists)
    assert [issue.subject for issue in issues] == ["IMPOSSIBLE_TRAVEL/signin_history"]


def test_no_mismatch_for_matching_types():
    rules = {"range": {"min_duration": "24h"}, "hours": {"min": 24}}
    checklists = {"impossible_travel": make_checklist(make_item(rules))}
    cases = [make_case(make_call({"range": "1h", "hours": 1.5}))]
    assert find_rule_type_mismatches(cases, checklists) == []


def test_no_mismatch_for_an_absent_path():
    checklists = {"impossible_travel": make_checklist(make_item({"hours": {"min": 24}}))}
    assert find_rule_type_mismatches([make_case(make_call({}))], checklists) == []


def test_mismatch_for_an_epoch_number_start_suggests_timestamps():
    details = mismatch_details(WINDOW_RULE, make_case(make_call({"start": 1, "end": 2})))
    assert details == [
        "'start' (start): the value is a number; this rule reads ISO 8601 timestamps; "
        "first seen in case case-1"
    ]


def test_epoch_number_start_and_end_fail_as_wrong_arguments():
    items = [make_item(WINDOW_RULE)]
    assert outcomes(items, make_call({"start": 1, "end": 2})) == (WRONG_ARGUMENTS,)


def test_mismatch_for_a_non_string_end_names_the_path_under_the_key():
    rules = {"window": {"min_duration": "24h", "start": "from", "end": "to"}}
    arguments = {"window": {"from": "2026-09-01T00:00:00Z", "to": True}}
    assert mismatch_details(rules, make_case(make_call(arguments))) == [
        "'window.to' (end): the value is a boolean; this rule reads ISO 8601 timestamps; "
        "first seen in case case-1"
    ]


def test_no_mismatch_for_string_start_and_end():
    arguments = {"start": "2026-09-01T00:00:00Z", "end": "2026-09-01T01:00:00Z"}
    assert mismatch_details(WINDOW_RULE, make_case(make_call(arguments))) == []


def test_mismatch_for_matches_on_a_number_suggests_text():
    details = mismatch_details({"q": {"matches": "24"}}, make_case(make_call({"q": 24})))
    assert details == [
        "'q' (matches): the value is a number; this rule reads text; first seen in case case-1"
    ]


@pytest.mark.parametrize(
    ("rule", "value", "detail"),
    [
        (
            {"equals": 24},
            "24",
            "'hours' (equals): the value is text; quote the expected value in the checklist",
        ),
        (
            {"in": [24, 48.5]},
            "-4.5e1",
            "'hours' (in): the value is text; quote the expected values in the checklist",
        ),
        (
            {"equals": "24"},
            24,
            "'hours' (equals): the value is a number; unquote the expected value in the checklist",
        ),
        (
            {"in": ["24", "48"]},
            24.0,
            "'hours' (in): the value is a number; unquote the expected values in the checklist",
        ),
    ],
    ids=[
        "equals-number-on-text",
        "in-numbers-on-text",
        "equals-text-on-number",
        "in-text-on-number",
    ],
)
def test_mismatch_for_equals_and_in_across_number_and_text(rule, value, detail):
    details = mismatch_details({"hours": rule}, make_case(make_call({"hours": value})))
    assert details == [f"{detail}; first seen in case case-1"]


@pytest.mark.parametrize(
    ("rule", "value"),
    [
        ({"equals": 24}, "all"),
        ({"equals": 24}, True),
        ({"in": [24, "24"]}, "24"),
        ({"in": [24, "24"]}, 24),
        ({"equals": "a"}, "b"),
        ({"equals": None}, 1),
    ],
    ids=["non-numeric-text", "boolean", "mixed-on-text", "mixed-on-number", "text", "null"],
)
def test_no_mismatch_for_equals_and_in_when_types_could_match(rule, value):
    assert mismatch_details({"hours": rule}, make_case(make_call({"hours": value}))) == []


def test_no_mismatch_from_cases_of_another_class():
    checklists = {"impossible_travel": make_checklist(make_item({"hours": {"min": 24}}))}
    cases = [make_case(make_call({"hours": "24"}), alert_class="Phishing")]
    assert find_rule_type_mismatches(cases, checklists) == []


def test_no_mismatch_from_unreadable_arguments():
    checklists = {"impossible_travel": make_checklist(make_item({"hours": {"min": 24}}))}
    assert find_rule_type_mismatches([make_case(make_call("{not json"))], checklists) == []


def test_mismatches_follow_class_then_item_order():
    rule = {"hours": {"min": 24}}
    checklists = {
        "phishing": make_checklist(make_item(rule, item_id="p"), alert_class="phishing"),
        "impossible_travel": make_checklist(
            make_item(rule, item_id="b"), make_item(rule, item_id="a")
        ),
    }
    cases = [
        make_case(make_call({"hours": "x"}), alert_class="Phishing"),
        make_case(make_call({"hours": "x"}), case_id="case-2"),
    ]
    issues = find_rule_type_mismatches(cases, checklists)
    assert [issue.subject for issue in issues] == [
        "impossible_travel/b",
        "impossible_travel/a",
        "phishing/p",
    ]


# find_missing_tool_arguments

RANGE_RULE = {"range": {"min_duration": "24h"}}


def test_item_with_rules_whose_calls_all_lack_arguments_is_reported():
    checklists = {"impossible_travel": make_checklist(make_item(RANGE_RULE))}
    cases = [make_case(make_call()), make_case(make_call(), case_id="case-2")]
    issues = find_missing_tool_arguments(cases, checklists)
    assert issues == [
        Issue(
            IssueKind.MISSING_TOOL_ARGUMENTS,
            "impossible_travel/signin_history",
            "2 calls to 'get_signin_logs', none with arguments",
        )
    ]


def test_one_call_with_arguments_is_enough_to_stay_quiet():
    checklists = {"impossible_travel": make_checklist(make_item(RANGE_RULE))}
    cases = [make_case(make_call()), make_case(make_call({"range": "1d"}), case_id="case-2")]
    assert find_missing_tool_arguments(cases, checklists) == []


def test_unreadable_arguments_still_count_as_carried():
    checklists = {"impossible_travel": make_checklist(make_item(RANGE_RULE))}
    assert find_missing_tool_arguments([make_case(make_call("{not json"))], checklists) == []


def test_item_without_rules_is_not_reported_for_calls_without_arguments():
    checklists = {"impossible_travel": make_checklist(make_item())}
    assert find_missing_tool_arguments([make_case(make_call())], checklists) == []


def test_item_whose_tool_is_never_called_is_not_reported():
    checklists = {"impossible_travel": make_checklist(make_item(RANGE_RULE))}
    cases = [make_case(make_call(tool="check_mfa_status"))]
    assert find_missing_tool_arguments(cases, checklists) == []


def test_calls_in_cases_of_another_class_do_not_count():
    checklists = {"impossible_travel": make_checklist(make_item(RANGE_RULE))}
    cases = [make_case(make_call(), alert_class="Phishing")]
    assert find_missing_tool_arguments(cases, checklists) == []


def test_arguments_in_another_class_do_not_hide_the_problem():
    checklists = {"impossible_travel": make_checklist(make_item(RANGE_RULE))}
    cases = [
        make_case(make_call()),
        make_case(make_call({"range": "1d"}), case_id="case-2", alert_class="Phishing"),
    ]
    issues = find_missing_tool_arguments(cases, checklists)
    assert [issue.subject for issue in issues] == ["impossible_travel/signin_history"]


def test_a_single_call_is_counted_in_the_singular():
    checklists = {"impossible_travel": make_checklist(make_item(RANGE_RULE))}
    issues = find_missing_tool_arguments([make_case(make_call())], checklists)
    assert [issue.detail for issue in issues] == [
        "1 call to 'get_signin_logs', none with arguments"
    ]


def test_missing_arguments_follow_class_then_item_order():
    checklists = {
        "phishing": make_checklist(make_item(RANGE_RULE, item_id="p"), alert_class="phishing"),
        "impossible_travel": make_checklist(
            make_item(RANGE_RULE, item_id="b"), make_item(RANGE_RULE, item_id="a")
        ),
    }
    cases = [
        make_case(make_call(), alert_class="Phishing"),
        make_case(make_call(), case_id="case-2"),
    ]
    issues = find_missing_tool_arguments(cases, checklists)
    assert [issue.subject for issue in issues] == [
        "impossible_travel/b",
        "impossible_travel/a",
        "phishing/p",
    ]
