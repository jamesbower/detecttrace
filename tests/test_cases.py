import pytest
from builders import TRACE_ID, agent_span, case_root, make_span, tool_span

from detecttrace.cases import build_trace_cases
from detecttrace.config import MappingConfig, OperationConfig
from detecttrace.model import Issue, IssueKind, Span

MAPPING = MappingConfig()


def tools_by_case(
    spans: list[Span], mapping: MappingConfig = MAPPING
) -> dict[str, tuple[str, ...]]:
    cases, _ = build_trace_cases(spans, mapping)
    return {case.case_id: tuple(call.tool_name for call in case.tool_calls) for case in cases}


def issue_kinds(spans: list[Span], mapping: MappingConfig = MAPPING) -> list[IssueKind]:
    _, issues = build_trace_cases(spans, mapping)
    return [issue.kind for issue in issues]


# Operation matching


def test_tool_span_below_root_belongs_to_the_case() -> None:
    spans = [case_root("r1", "DT-1"), tool_span("t1", "r1", "check_mfa_status")]

    assert tools_by_case(spans) == {"DT-1": ("check_mfa_status",)}


def test_span_name_fallback_identifies_operations_without_the_attribute() -> None:
    spans = [
        make_span("r1", name="invoke_agent", attributes={"detecttrace.case_id": "DT-1"}),
        make_span("t1", "r1", name="execute_tool get_ip_reputation"),
    ]

    assert tools_by_case(spans) == {"DT-1": ("get_ip_reputation",)}


def test_span_name_fallback_can_be_turned_off() -> None:
    mapping = MappingConfig(operation=OperationConfig(span_name_fallback=False))
    spans = [
        make_span("r1", name="invoke_agent triage", attributes={"detecttrace.case_id": "DT-1"})
    ]

    assert tools_by_case(spans, mapping) == {}


def test_custom_operation_attribute_and_values() -> None:
    mapping = MappingConfig(
        operation=OperationConfig(
            attribute="openinference.span.kind", agent_value="AGENT", tool_value="TOOL"
        )
    )
    spans = [
        make_span(
            "r1", attributes={"openinference.span.kind": "AGENT", "detecttrace.case_id": "DT-1"}
        ),
        make_span(
            "t1", "r1", attributes={"openinference.span.kind": "TOOL", "gen_ai.tool.name": "x"}
        ),
    ]

    assert tools_by_case(spans, mapping) == {"DT-1": ("x",)}


@pytest.mark.parametrize("operation", ["", 5])
def test_empty_or_non_text_operation_falls_back_to_span_name(operation: object) -> None:
    spans = [
        make_span(
            "r1",
            name="invoke_agent x",
            attributes={"gen_ai.operation.name": operation, "detecttrace.case_id": "DT-1"},
        )
    ]

    assert tools_by_case(spans) == {"DT-1": ()}


# Case structure


def test_sub_agent_without_case_id_tool_calls_belong_to_the_root() -> None:
    spans = [
        case_root("r1", "DT-1"),
        agent_span("s1", "r1"),
        tool_span("t1", "s1", "check_mfa_status"),
    ]

    assert tools_by_case(spans) == {"DT-1": ("check_mfa_status",)}


def test_sub_agent_verdict_is_ignored() -> None:
    spans = [
        case_root("r1", "DT-1", verdict="benign"),
        agent_span("s1", "r1", attributes={"detecttrace.verdict": "true_positive"}),
    ]

    cases, _ = build_trace_cases(spans, MAPPING)

    assert cases[0].agent_label == "benign"


def test_nested_span_with_the_same_case_id_uses_the_outermost_root() -> None:
    spans = [case_root("r1", "DT-1"), case_root("r2", "DT-1", "r1")]

    cases, _ = build_trace_cases(spans, MAPPING)

    assert [c.root_span_id for c in cases] == ["r1"]


def test_several_roots_in_one_trace_are_separate_cases() -> None:
    spans = [case_root("r1", "DT-1"), case_root("r2", "DT-2"), tool_span("t1", "r2")]

    assert tools_by_case(spans) == {"DT-1": (), "DT-2": ("get_signin_logs",)}


def test_nested_case_with_a_different_id_owns_only_its_subtree() -> None:
    spans = [
        case_root("r1", "DT-1"),
        tool_span("t1", "r1", "get_user_profile"),
        case_root("r2", "DT-2", "r1"),
        tool_span("t2", "r2", "check_mfa_status"),
    ]

    assert tools_by_case(spans) == {"DT-1": ("get_user_profile",), "DT-2": ("check_mfa_status",)}


def test_nested_case_with_a_different_id_is_reported() -> None:
    spans = [case_root("r1", "DT-1"), case_root("r2", "DT-2", "r1")]

    assert issue_kinds(spans) == [IssueKind.NESTED_CASE]


def reappearing_case_spans() -> list[Span]:
    return [
        case_root("r1", "DT-1"),
        tool_span("t0", "r1", "get_user_profile"),
        case_root("r2", "DT-2", "r1"),
        case_root("r3", "DT-1", "r2"),
        tool_span("t3", "r3", "check_mfa_status"),
    ]


def test_case_id_reappearing_inside_a_nested_case_is_a_sub_agent_of_the_nested_case() -> None:
    assert tools_by_case(reappearing_case_spans()) == {
        "DT-1": ("get_user_profile",),
        "DT-2": ("check_mfa_status",),
    }


def test_case_id_reappearing_inside_a_nested_case_opens_no_second_case() -> None:
    cases, _ = build_trace_cases(reappearing_case_spans(), MAPPING)

    assert sorted((case.case_id, case.root_span_id) for case in cases) == [
        ("DT-1", "r1"),
        ("DT-2", "r2"),
    ]


def test_case_id_reappearing_inside_a_nested_case_is_reported() -> None:
    _, issues = build_trace_cases(reappearing_case_spans(), MAPPING)

    assert issues == [
        Issue(IssueKind.NESTED_CASE, "DT-2", "inside case DT-1"),
        Issue(
            IssueKind.NESTED_CASE,
            "DT-1",
            "case ID DT-1 reappears inside case DT-2; treated as a sub-agent of DT-2",
        ),
    ]


def test_tool_call_of_a_sub_agent_in_a_nested_case_belongs_to_the_nested_case() -> None:
    spans = [
        case_root("r1", "DT-1"),
        case_root("r2", "DT-2", "r1"),
        agent_span("s1", "r2"),
        tool_span("t1", "s1", "check_mfa_status"),
    ]

    assert tools_by_case(spans) == {"DT-1": (), "DT-2": ("check_mfa_status",)}


def test_nested_agent_with_an_invalid_case_id_is_a_sub_agent() -> None:
    spans = [
        case_root("r1", "DT-1"),
        agent_span("a1", "r1", attributes={"detecttrace.case_id": ["DT-2"]}),
        tool_span("t1", "a1", "check_mfa_status"),
    ]

    assert tools_by_case(spans) == {"DT-1": ("check_mfa_status",)}


def test_root_that_is_its_own_parent_keeps_its_tool_call() -> None:
    spans = [case_root("r1", "DT-1", "r1"), tool_span("t1", "r1", "check_mfa_status")]

    assert tools_by_case(spans) == {"DT-1": ("check_mfa_status",)}


def test_root_in_a_two_span_parent_cycle_keeps_its_tool_call() -> None:
    spans = [
        case_root("r1", "DT-1", "r2"),
        agent_span("r2", "r1"),
        tool_span("t1", "r1", "check_mfa_status"),
    ]

    assert tools_by_case(spans) == {"DT-1": ("check_mfa_status",)}


def test_tool_that_sorts_before_a_cycle_member_still_belongs_to_the_case() -> None:
    spans = [
        case_root("r1", "DT-1", "r2"),
        agent_span("r2", "r1"),
        tool_span("a1", "r1", "check_mfa_status"),
    ]

    assert tools_by_case(spans) == {"DT-1": ("check_mfa_status",)}


def test_parent_cycle_is_reported_once_as_a_broken_parent_chain() -> None:
    spans = [case_root("r1", "DT-1", "r2"), agent_span("r2", "r1")]

    _, issues = build_trace_cases(spans, MAPPING)

    assert issues == [Issue(IssueKind.BROKEN_PARENT_CHAIN, f"{TRACE_ID}/r1", "parent cycle")]


def test_tool_span_outside_any_root_is_an_orphan() -> None:
    assert issue_kinds([tool_span("t1", None)]) == [IssueKind.ORPHAN_TOOL_SPAN]


def test_tool_span_with_a_missing_parent_is_a_broken_parent_chain() -> None:
    assert issue_kinds([tool_span("t1", "gone")]) == [IssueKind.BROKEN_PARENT_CHAIN]


def test_root_with_a_missing_parent_is_still_a_case() -> None:
    assert tools_by_case([case_root("r1", "DT-1", "remote-parent")]) == {"DT-1": ()}


def test_case_in_a_trace_with_orphans_is_flagged_incomplete() -> None:
    spans = [case_root("r1", "DT-1"), tool_span("t1", "gone")]

    cases, _ = build_trace_cases(spans, MAPPING)

    assert cases[0].is_incomplete_trace is True


def test_incomplete_trace_detail_counts_the_orphans() -> None:
    spans = [case_root("r1", "DT-1"), tool_span("t1", "gone")]

    _, issues = build_trace_cases(spans, MAPPING)

    assert issues[-1] == Issue(
        IssueKind.INCOMPLETE_TRACE, "DT-1", f"{TRACE_ID}: 1 orphan tool span(s)"
    )


def test_agent_span_without_case_id_is_reported() -> None:
    assert issue_kinds([agent_span("a1")]) == [IssueKind.AGENT_WITHOUT_CASE_ID]


def test_top_agent_with_an_invalid_case_id_reports_only_the_invalid_attribute() -> None:
    spans = [agent_span("a1", attributes={"detecttrace.case_id": ["DT-1"]})]

    assert issue_kinds(spans) == [IssueKind.INVALID_ATTRIBUTE]


def test_tool_calls_are_ordered_by_start_time_then_span_id() -> None:
    spans = [
        case_root("r1", "DT-1"),
        tool_span("t3", "r1", "third", start_ns=20),
        tool_span("t2", "r1", "second", start_ns=10),
        tool_span("t1", "r1", "first", start_ns=10),
    ]

    assert tools_by_case(spans) == {"DT-1": ("first", "second", "third")}


@pytest.mark.parametrize(
    ("is_error", "attributes"),
    [(True, {}), (False, {"error.type": "timeout"})],
)
def test_failed_tool_call(is_error: bool, attributes: dict[str, object]) -> None:
    spans = [
        case_root("r1", "DT-1"),
        tool_span("t1", "r1", is_error=is_error, attributes=attributes),
    ]

    cases, _ = build_trace_cases(spans, MAPPING)

    assert cases[0].tool_calls[0].is_failed is True


def test_tool_name_falls_back_to_span_name() -> None:
    spans = [
        case_root("r1", "DT-1"),
        make_span(
            "t1",
            "r1",
            name="execute_tool get_audit_logs",
            attributes={"gen_ai.operation.name": "execute_tool"},
        ),
    ]

    assert tools_by_case(spans) == {"DT-1": ("get_audit_logs",)}


def test_span_named_only_execute_tool_is_a_tool_call_with_unknown_name() -> None:
    spans = [case_root("r1", "DT-1"), make_span("t1", "r1", name="execute_tool")]

    assert tools_by_case(spans) == {"DT-1": ("",)}


def test_unknown_tool_name_is_reported() -> None:
    spans = [case_root("r1", "DT-1"), make_span("t1", "r1", name="execute_tool")]

    assert issue_kinds(spans) == [IssueKind.MISSING_TOOL_NAME]


def test_non_text_tool_name_is_reported() -> None:
    spans = [
        case_root("r1", "DT-1"),
        tool_span("t1", "r1", attributes={"gen_ai.tool.name": 5}),
    ]

    assert issue_kinds(spans) == [IssueKind.INVALID_ATTRIBUTE]


def test_span_name_without_the_operation_prefix_is_not_a_tool_name() -> None:
    mapping = MappingConfig(
        operation=OperationConfig(
            attribute="openinference.span.kind", agent_value="AGENT", tool_value="TOOL"
        )
    )
    spans = [
        make_span(
            "r1", attributes={"openinference.span.kind": "AGENT", "detecttrace.case_id": "DT-1"}
        ),
        make_span("t1", "r1", name="lookup user", attributes={"openinference.span.kind": "TOOL"}),
    ]

    assert tools_by_case(spans, mapping) == {"DT-1": ("",)}


def test_map_arguments_stay_a_dictionary_in_original_key_order() -> None:
    arguments = {"window": "24h", "account": "user@example.com"}
    spans = [
        case_root("r1", "DT-1"),
        tool_span("t1", "r1", attributes={"gen_ai.tool.call.arguments": arguments}),
    ]

    cases, _ = build_trace_cases(spans, MAPPING)

    assert (
        repr(cases[0].tool_calls[0].arguments) == "{'window': '24h', 'account': 'user@example.com'}"
    )


# Attribute lookup


def test_alert_class_falls_back_to_resource_attributes() -> None:
    spans = [case_root("r1", "DT-1", resource={"detecttrace.alert_class": "impossible_travel"})]

    cases, _ = build_trace_cases(spans, MAPPING)

    assert cases[0].alert_class == "impossible_travel"


def test_prompt_version_falls_back_to_resource_attributes() -> None:
    spans = [case_root("r1", "DT-1", resource={"detecttrace.prompt_version": "v2"})]

    cases, _ = build_trace_cases(spans, MAPPING)

    assert cases[0].prompt_version == "v2"


def test_prompt_version_on_a_child_is_ignored_by_default() -> None:
    spans = [
        case_root("r1", "DT-1"),
        make_span("c1", "r1", attributes={"detecttrace.prompt_version": "v2"}),
    ]

    cases, _ = build_trace_cases(spans, MAPPING)

    assert cases[0].prompt_version is None


def test_prompt_version_from_a_descendant_when_enabled() -> None:
    mapping = MappingConfig(prompt_version_lookup="descendant")
    spans = [
        case_root("r1", "DT-1"),
        make_span("c1", "r1", attributes={"detecttrace.prompt_version": "v2"}),
    ]

    cases, _ = build_trace_cases(spans, mapping)

    assert cases[0].prompt_version == "v2"


def test_conflicting_descendant_versions_give_no_version() -> None:
    mapping = MappingConfig(prompt_version_lookup="descendant")
    spans = [
        case_root("r1", "DT-1"),
        make_span("c1", "r1", attributes={"detecttrace.prompt_version": "v1"}),
        make_span("c2", "r1", attributes={"detecttrace.prompt_version": "v2"}),
    ]

    assert issue_kinds(spans, mapping) == [IssueKind.VERSION_CONFLICT]


def test_conflicting_descendant_versions_leave_the_version_empty() -> None:
    mapping = MappingConfig(prompt_version_lookup="descendant")
    spans = [
        case_root("r1", "DT-1"),
        make_span("c1", "r1", attributes={"detecttrace.prompt_version": "v1"}),
        make_span("c2", "r1", attributes={"detecttrace.prompt_version": "v2"}),
    ]

    cases, _ = build_trace_cases(spans, mapping)

    assert cases[0].prompt_version is None


def test_descendant_lookup_skips_an_invalid_earlier_value() -> None:
    mapping = MappingConfig(prompt_version_lookup="descendant")
    spans = [
        case_root("r1", "DT-1"),
        make_span("c2", "r1", start_ns=10, attributes={"detecttrace.prompt_version": "v2"}),
        make_span("c1", "r1", start_ns=5, attributes={"detecttrace.prompt_version": ["v1"]}),
    ]

    cases, _ = build_trace_cases(spans, mapping)

    assert cases[0].prompt_version == "v2"


def test_descendant_lookup_reports_the_span_with_the_invalid_value() -> None:
    mapping = MappingConfig(prompt_version_lookup="descendant")
    spans = [
        case_root("r1", "DT-1"),
        make_span("c1", "r1", attributes={"detecttrace.prompt_version": ["v1"]}),
    ]

    _, issues = build_trace_cases(spans, mapping)

    assert issues == [
        Issue(
            IssueKind.INVALID_ATTRIBUTE,
            "DT-1",
            "detecttrace.prompt_version on span c1 is a list, not a single value",
        )
    ]


def test_descendant_lookup_ignores_spans_of_a_nested_case() -> None:
    mapping = MappingConfig(prompt_version_lookup="descendant")
    spans = [
        case_root("r1", "DT-1"),
        case_root("r2", "DT-2", "r1"),
        make_span("c2", "r2", attributes={"detecttrace.prompt_version": "v9"}),
    ]

    cases, _ = build_trace_cases(spans, mapping)

    assert {case.case_id: case.prompt_version for case in cases} == {"DT-1": None, "DT-2": "v9"}


@pytest.mark.parametrize(
    ("value", "expected"),
    [(3, "3"), (2.5, "2.5"), (3.0, "3.0"), (1e16, "1e+16"), (True, "true"), ("  v1 ", "v1")],
)
def test_attribute_values_are_converted_to_text(value: object, expected: str) -> None:
    spans = [case_root("r1", "DT-1", attributes={"detecttrace.prompt_version": value})]

    cases, _ = build_trace_cases(spans, MAPPING)

    assert cases[0].prompt_version == expected


def test_integer_case_id_becomes_decimal_text() -> None:
    assert tools_by_case([case_root("r1", 418)]) == {"418": ()}


def test_empty_string_counts_as_missing() -> None:
    spans = [
        case_root(
            "r1",
            "DT-1",
            attributes={"detecttrace.alert_class": ""},
            resource={"detecttrace.alert_class": "oauth_consent"},
        )
    ]

    cases, _ = build_trace_cases(spans, MAPPING)

    assert cases[0].alert_class == "oauth_consent"


def test_array_value_is_rejected_and_reported() -> None:
    spans = [case_root("r1", "DT-1", attributes={"detecttrace.prompt_version": ["v1", "v2"]})]

    assert issue_kinds(spans) == [IssueKind.INVALID_ATTRIBUTE]


def test_non_finite_number_is_rejected_and_reported() -> None:
    spans = [case_root("r1", "DT-1", attributes={"detecttrace.prompt_version": float("nan")})]

    assert issue_kinds(spans) == [IssueKind.INVALID_ATTRIBUTE]


def test_invalid_resource_value_is_reported_once_for_all_cases() -> None:
    resource = {"detecttrace.prompt_version": ["v1", "v2"]}
    spans = [
        case_root("r1", "DT-1", resource=resource),
        case_root("r2", "DT-2", resource=resource),
    ]

    _, issues = build_trace_cases(spans, MAPPING)

    assert issues == [
        Issue(
            IssueKind.INVALID_ATTRIBUTE,
            "resource",
            "detecttrace.prompt_version is a list, not a single value",
        )
    ]


# Duplicate roots

TRACE_A = "0000000000000000000000000000000a"
TRACE_B = "0000000000000000000000000000000b"


def kept_root(spans: list[Span]) -> list[tuple[str, str]]:
    cases, _ = build_trace_cases(spans, MAPPING)
    return [(c.trace_id, c.root_span_id) for c in cases]


def test_duplicate_roots_keep_the_latest_start() -> None:
    spans = [
        case_root("r1", "DT-1", trace_id=TRACE_A, start_ns=200),
        case_root("r2", "DT-1", trace_id=TRACE_B, start_ns=100),
    ]

    assert kept_root(spans) == [(TRACE_A, "r1")]


def test_duplicate_roots_tied_on_start_keep_the_latest_end() -> None:
    spans = [
        case_root("r1", "DT-1", trace_id=TRACE_A, start_ns=100, end_ns=500),
        case_root("r2", "DT-1", trace_id=TRACE_B, start_ns=100, end_ns=300),
    ]

    assert kept_root(spans) == [(TRACE_A, "r1")]


def test_duplicate_roots_tied_on_times_keep_the_highest_trace_id() -> None:
    spans = [
        case_root("r1", "DT-1", trace_id=TRACE_A),
        case_root("r2", "DT-1", trace_id=TRACE_B),
    ]

    assert kept_root(spans) == [(TRACE_B, "r2")]


def test_duplicate_roots_in_one_trace_keep_the_highest_span_id() -> None:
    spans = [case_root("r1", "DT-1"), case_root("r2", "DT-1")]

    assert kept_root(spans) == [(spans[1].trace_id, "r2")]


def test_duplicate_roots_are_reported() -> None:
    spans = [case_root("r1", "DT-1", trace_id=TRACE_A), case_root("r2", "DT-1", trace_id=TRACE_B)]

    assert issue_kinds(spans) == [IssueKind.DUPLICATE_ROOT]
