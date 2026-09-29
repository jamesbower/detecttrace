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


def test_unknown_tool_name_matches_nothing():
    assert outcomes([make_item()], make_call(tool="")) == (NOT_CALLED,)


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
    assert len(issues[0].detail) < 150


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


def test_mismatch_for_min_on_a_string_names_the_path_and_suggests_min_duration():
    checklists = {"impossible_travel": make_checklist(make_item({"hours": {"min": 24}}))}
    issues = find_rule_type_mismatches([make_case(make_call({"hours": "24"}))], checklists)
    assert issues == [
        Issue(
            IssueKind.RULE_TYPE_MISMATCH,
            "impossible_travel/signin_history",
            "'hours': a call's value is not a number, but min and max compare numbers; "
            "for text like '24h' use min_duration",
        )
    ]


def test_mismatch_for_min_duration_on_a_number_suggests_min():
    checklists = {
        "impossible_travel": make_checklist(make_item({"range": {"min_duration": "24h"}}))
    }
    issues = find_rule_type_mismatches([make_case(make_call({"range": 24}))], checklists)
    assert issues[0].detail == (
        "'range': a call's value is a number, but min_duration reads text like '24h'; "
        "for numbers use min or max"
    )


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


def test_no_mismatch_for_min_duration_with_start_and_end():
    checklists = {"impossible_travel": make_checklist(make_item(WINDOW_RULE))}
    cases = [make_case(make_call({"start": 1, "end": 2}))]
    assert find_rule_type_mismatches(cases, checklists) == []


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
