import time

import pytest

from detecttrace.model import Issue, IssueKind
from detecttrace.summary import (
    SEVERITY,
    IssueExample,
    JoinCoverage,
    Severity,
    SummaryLine,
    coverage_lines,
    has_invalid_input,
    is_low_coverage,
    summarize_issues,
    to_terminal_text,
)

INVALID_INPUT_KINDS = [
    IssueKind.EMPTY_FILE,
    IssueKind.TRUNCATED_LINE,
    IssueKind.TRUNCATED_FILE,
    IssueKind.INVALID_LINE,
    IssueKind.INVALID_FILE,
    IssueKind.INVALID_SPAN,
    IssueKind.CONFLICTING_DUPLICATE_SPAN,
    IssueKind.INVALID_ATTRIBUTE,
    IssueKind.MISSING_TOOL_NAME,
    IssueKind.AGENT_WITHOUT_CASE_ID,
    IssueKind.ORPHAN_TOOL_SPAN,
    IssueKind.BROKEN_PARENT_CHAIN,
    IssueKind.VERSION_CONFLICT,
    IssueKind.INVALID_VERDICT_ROW,
    IssueKind.CONFLICTING_ANALYST_VERDICT,
    IssueKind.ALERT_CLASS_CONFLICT,
    IssueKind.UNMAPPED_ANALYST_LABEL,
    IssueKind.UNMAPPED_AGENT_LABEL,
    IssueKind.MISSING_AGENT_VERDICT,
    IssueKind.UNREADABLE_ARGUMENTS,
    IssueKind.UNREADABLE_DURATION,
    IssueKind.UNREADABLE_KQL_TIMESPAN,
    IssueKind.CONSOLE_EXPORTER_OUTPUT,
    IssueKind.UNSUPPORTED_COMPRESSION,
]
WARNING_KINDS = [
    IssueKind.DUPLICATE_SPAN,
    IssueKind.NESTED_CASE,
    IssueKind.INCOMPLETE_TRACE,
    IssueKind.DUPLICATE_ROOT,
    IssueKind.DUPLICATE_VERDICT,
    IssueKind.ROOT_WITHOUT_VERDICT,
    IssueKind.VERDICT_WITHOUT_ROOT,
    IssueKind.RULE_TYPE_MISMATCH,
    IssueKind.MISSING_TOOL_ARGUMENTS,
    IssueKind.UNKNOWN_CHECKLIST_TOOL,
    IssueKind.UNUSED_CHECKLIST,
]


def unmapped(label: str, count: int) -> list[Issue]:
    return [
        Issue(IssueKind.UNMAPPED_ANALYST_LABEL, f"DT-{label}-{index}", label)
        for index in range(count)
    ]


def only_line(issues: list[Issue]) -> SummaryLine:
    [line] = summarize_issues(issues)
    return line


# Severity table


def test_every_issue_kind_has_a_severity() -> None:
    assert set(SEVERITY) == set(IssueKind)


@pytest.mark.parametrize("kind", INVALID_INPUT_KINDS)
def test_invalid_input_kinds_have_invalid_input_severity(kind: IssueKind) -> None:
    assert SEVERITY[kind] is Severity.INVALID_INPUT


@pytest.mark.parametrize("kind", WARNING_KINDS)
def test_warning_kinds_have_warning_severity(kind: IssueKind) -> None:
    assert SEVERITY[kind] is Severity.WARNING


def test_table_lists_every_kind_once() -> None:
    assert sorted(INVALID_INPUT_KINDS + WARNING_KINDS) == sorted(IssueKind)


# Messages


@pytest.mark.parametrize("kind", list(IssueKind))
def test_every_kind_renders_without_placeholders(kind: IssueKind) -> None:
    detail = "lookup_ip"
    # Same subject and detail so every kind, whatever its grouping key, gives one plural line.
    issues = [Issue(kind, "a.jsonl", detail), Issue(kind, "a.jsonl", detail)]
    assert "{" not in only_line(issues).message


@pytest.mark.parametrize("kind", list(IssueKind))
def test_every_kind_starts_with_the_count(kind: IssueKind) -> None:
    assert only_line([Issue(kind, "a.jsonl", "x")]).message.startswith("1 ")


@pytest.mark.parametrize("kind", list(IssueKind))
def test_every_kind_ends_a_sentence(kind: IssueKind) -> None:
    assert only_line([Issue(kind, "a.jsonl", "x")]).message.endswith(".")


@pytest.mark.parametrize("kind", list(IssueKind))
def test_every_kind_has_a_hint_sentence_after_the_count(kind: IssueKind) -> None:
    assert ". " in only_line([Issue(kind, "a.jsonl", "x")]).message


# Escaped so a search of the code for private-document references stays clean.
PRIVATE_DOCUMENT_MARKERS = ["\x50RD", "\N{SECTION SIGN}"]


@pytest.mark.parametrize("kind", list(IssueKind))
@pytest.mark.parametrize("marker", PRIVATE_DOCUMENT_MARKERS)
def test_messages_do_not_reference_private_documents(kind: IssueKind, marker: str) -> None:
    assert marker not in only_line([Issue(kind, "a.jsonl", "x")]).message


def test_unmapped_labels_give_one_line_per_label() -> None:
    lines = summarize_issues(unmapped("Escalated", 12) + unmapped("Pending", 3))
    assert len(lines) == 2


def test_unmapped_label_message_counts_what_it_is_about() -> None:
    lines = summarize_issues(unmapped("Escalated", 12) + unmapped("Pending", 3))
    assert lines[0].message == (
        "12 verdicts use the label 'Escalated', which has no mapping. "
        "Add it to label_map in detecttrace.yaml."
    )


def test_config_name_is_used_when_given() -> None:
    [line] = summarize_issues(unmapped("Escalated", 1), config_name="prod.yaml")
    assert line.message.endswith("Add it to label_map in prod.yaml.")


def test_missing_tool_name_points_at_the_mapping() -> None:
    [line] = summarize_issues([Issue(IssueKind.MISSING_TOOL_NAME, "t/1")], config_name="prod.yaml")
    assert line.message.endswith(
        "Set the tool name attribute (mapping.tool_name in prod.yaml) on every tool span."
    )


def test_singular_count_uses_singular_noun_and_verb() -> None:
    assert only_line(unmapped("Escalated", 1)).message.startswith("1 verdict uses the label")


def test_plural_count_uses_plural_noun_and_verb() -> None:
    assert only_line(unmapped("Escalated", 2)).message.startswith("2 verdicts use the label")


def test_count_has_thousands_separators() -> None:
    issues = [Issue(IssueKind.VERDICT_WITHOUT_ROOT, f"DT-{index}") for index in range(1234)]
    assert only_line(issues).message.startswith("1,234 ")


# Grouping keys


def test_unmapped_agent_labels_group_by_label() -> None:
    issues = [
        Issue(IssueKind.UNMAPPED_AGENT_LABEL, "DT-1", "escalate"),
        Issue(IssueKind.UNMAPPED_AGENT_LABEL, "DT-2", "unsure"),
        Issue(IssueKind.UNMAPPED_AGENT_LABEL, "DT-3", "escalate"),
    ]
    assert [line.count for line in summarize_issues(issues)] == [2, 1]


def test_unknown_tools_group_by_tool() -> None:
    issues = [
        Issue(IssueKind.UNKNOWN_CHECKLIST_TOOL, "travel/mfa", "check_mfa"),
        Issue(IssueKind.UNKNOWN_CHECKLIST_TOOL, "oauth/mfa", "check_mfa"),
        Issue(IssueKind.UNKNOWN_CHECKLIST_TOOL, "travel/ip", "lookup_ip"),
    ]
    assert [line.count for line in summarize_issues(issues)] == [2, 1]


def test_unknown_tool_message_names_the_tool() -> None:
    issue = Issue(IssueKind.UNKNOWN_CHECKLIST_TOOL, "travel/mfa", "check_mfa")
    assert "'check_mfa'" in only_line([issue]).message


def test_unknown_tool_with_quotes_in_its_name_keeps_the_whole_name() -> None:
    issue = Issue(IssueKind.UNKNOWN_CHECKLIST_TOOL, "travel/mfa", "it's 'x'")
    assert "the tool 'it's 'x'', which" in only_line([issue]).message


def test_rule_mismatches_group_by_item() -> None:
    issues = [
        Issue(IssueKind.RULE_TYPE_MISMATCH, "travel/mfa", "expected a number"),
        Issue(IssueKind.RULE_TYPE_MISMATCH, "travel/ip", "expected a string"),
    ]
    assert len(summarize_issues(issues)) == 2


def test_other_kinds_group_by_kind_alone() -> None:
    issues = [
        Issue(IssueKind.INVALID_LINE, "a.jsonl", "line 3"),
        Issue(IssueKind.INVALID_LINE, "b.jsonl", "line 9"),
    ]
    assert only_line(issues).count == 2


# Examples


def test_examples_are_the_first_three_subjects_in_input_order() -> None:
    issues = [Issue(IssueKind.VERDICT_WITHOUT_ROOT, f"DT-{index}") for index in (5, 1, 9, 2)]
    assert only_line(issues).examples == (
        IssueExample("DT-5", None),
        IssueExample("DT-1", None),
        IssueExample("DT-9", None),
    )


def test_examples_skip_repeated_subjects_keeping_the_first_detail() -> None:
    issues = [
        Issue(IssueKind.INVALID_LINE, "a.jsonl", "line 3"),
        Issue(IssueKind.INVALID_LINE, "a.jsonl", "line 4"),
        Issue(IssueKind.INVALID_LINE, "b.jsonl", "line 1"),
    ]
    assert only_line(issues).examples == (
        IssueExample("a.jsonl", "line 3"),
        IssueExample("b.jsonl", "line 1"),
    )


def test_long_example_subjects_are_kept_whole_for_the_results() -> None:
    subject = "traces/" + "x" * 100 + ".jsonl"
    assert only_line([Issue(IssueKind.EMPTY_FILE, subject)]).examples == (
        IssueExample(subject, None),
    )


def test_an_invalid_file_example_keeps_the_os_error() -> None:
    issue = Issue(IssueKind.INVALID_FILE, "a.jsonl", "Permission denied")
    assert only_line([issue]).examples == (IssueExample("a.jsonl", "Permission denied"),)


def test_a_rule_mismatch_example_keeps_its_description() -> None:
    issue = Issue(IssueKind.RULE_TYPE_MISMATCH, "travel/mfa", "x: expected a number")
    assert only_line([issue]).examples == (IssueExample("travel/mfa", "x: expected a number"),)


@pytest.mark.parametrize(
    "kind",
    [
        IssueKind.UNMAPPED_ANALYST_LABEL,
        IssueKind.UNMAPPED_AGENT_LABEL,
        IssueKind.UNKNOWN_CHECKLIST_TOOL,
    ],
)
def test_examples_leave_out_a_detail_that_is_the_key(kind: IssueKind) -> None:
    assert only_line([Issue(kind, "DT-1", "Escalated")]).examples == (IssueExample("DT-1", None),)


# Terminal text


def test_terminal_message_escapes_control_characters_in_the_key() -> None:
    line = only_line(unmapped("X\x1b[2K", 1))
    assert "'X\\x1b[2K'" in line.terminal_message


def test_message_keeps_the_raw_key_for_the_results() -> None:
    line = only_line(unmapped("X\x1b[2K", 1))
    assert "'X\x1b[2K'" in line.message


def test_terminal_message_shortens_a_long_key() -> None:
    line = only_line(unmapped("x" * 1_000_000, 1))
    assert len(line.terminal_message) < 200


def test_terminal_message_escapes_the_config_name() -> None:
    [line] = summarize_issues(unmapped("Escalated", 1), config_name="a\x1b.yaml")
    assert line.terminal_message.endswith("Add it to label_map in a\\x1b.yaml.")


def test_terminal_text_keeps_printable_text() -> None:
    assert to_terminal_text("Café 'x' {y}") == "Café 'x' {y}"


def test_terminal_text_escapes_an_escape_character() -> None:
    assert to_terminal_text("a\x1b[2Kb") == "a\\x1b[2Kb"


def test_terminal_text_escapes_an_osc_52_sequence() -> None:
    assert to_terminal_text("\x1b]52;c;ZXZpbA==\x07") == "\\x1b]52;c;ZXZpbA==\\x07"


def test_terminal_text_escapes_line_breaks() -> None:
    assert to_terminal_text("a\r\nb") == "a\\x0d\\x0ab"


def test_terminal_text_escapes_a_bidi_override() -> None:
    assert to_terminal_text("a\u202eb") == "a\\u202eb"


def test_terminal_text_escapes_an_astral_format_character() -> None:
    assert to_terminal_text("\U000e0001") == "\\U000e0001"


def test_terminal_text_shortens_to_60_characters() -> None:
    assert len(to_terminal_text("x" * 1_000_000)) == 60


def test_terminal_text_marks_a_shortened_text_with_an_ellipsis() -> None:
    assert to_terminal_text("x" * 100).endswith("x…")


def test_terminal_text_counts_escapes_toward_the_limit() -> None:
    assert to_terminal_text("\x1b" * 30) == "\\x1b" * 14 + "…"


def test_terminal_text_never_cuts_an_escape_in_half() -> None:
    assert to_terminal_text("x" * 57 + "\x1b" + "y" * 10) == "x" * 57 + "…"


def test_terminal_text_keeps_text_at_the_limit_whole() -> None:
    assert to_terminal_text("x" * 60) == "x" * 60


def test_terminal_text_without_a_limit_keeps_everything() -> None:
    assert to_terminal_text("x" * 100, limit=None) == "x" * 100


# Ordering


def test_lines_order_invalid_input_first_then_count_then_kind() -> None:
    issues = [
        Issue(IssueKind.VERDICT_WITHOUT_ROOT, "DT-1"),
        Issue(IssueKind.VERDICT_WITHOUT_ROOT, "DT-2"),
        Issue(IssueKind.VERDICT_WITHOUT_ROOT, "DT-3"),
        Issue(IssueKind.ROOT_WITHOUT_VERDICT, "DT-4"),
        Issue(IssueKind.ROOT_WITHOUT_VERDICT, "DT-5"),
        Issue(IssueKind.DUPLICATE_VERDICT, "DT-6"),
        Issue(IssueKind.DUPLICATE_ROOT, "DT-7"),
        Issue(IssueKind.EMPTY_FILE, "a.jsonl"),
        Issue(IssueKind.INVALID_FILE, "b.jsonl"),
        Issue(IssueKind.INVALID_FILE, "c.jsonl"),
    ]
    assert [line.kind for line in summarize_issues(issues)] == [
        IssueKind.INVALID_FILE,
        IssueKind.EMPTY_FILE,
        IssueKind.VERDICT_WITHOUT_ROOT,
        IssueKind.ROOT_WITHOUT_VERDICT,
        IssueKind.DUPLICATE_ROOT,
        IssueKind.DUPLICATE_VERDICT,
    ]


def test_lines_carry_the_kind_severity() -> None:
    lines = summarize_issues([Issue(IssueKind.EMPTY_FILE, "a"), Issue(IssueKind.NESTED_CASE, "b")])
    assert [line.severity for line in lines] == [Severity.INVALID_INPUT, Severity.WARNING]


def test_same_count_same_kind_orders_by_key() -> None:
    lines = summarize_issues(unmapped("Pending", 1) + unmapped("Escalated", 1))
    assert "'Escalated'" in lines[0].message


def test_no_issues_give_no_lines() -> None:
    assert summarize_issues([]) == []


@pytest.mark.benchmark
def test_100000_issues_summarize_quickly() -> None:
    kinds = list(IssueKind)
    issues = [
        Issue(kinds[index % len(kinds)], f"DT-{index}", f"label-{index % 50}")
        for index in range(100_000)
    ]
    started = time.perf_counter()
    summarize_issues(issues)
    elapsed = time.perf_counter() - started
    print(f"summarized 100,000 issues in {elapsed:.3f} s")
    assert elapsed < 5


# has_invalid_input


def test_has_invalid_input_is_false_with_warnings_only() -> None:
    issues = [Issue(IssueKind.DUPLICATE_SPAN, "a"), Issue(IssueKind.UNUSED_CHECKLIST, "b")]
    assert has_invalid_input(issues) is False


def test_has_invalid_input_is_true_with_one_invalid_issue() -> None:
    issues = [Issue(IssueKind.DUPLICATE_SPAN, "a"), Issue(IssueKind.INVALID_SPAN, "b")]
    assert has_invalid_input(issues) is True


def test_has_invalid_input_is_false_without_issues() -> None:
    assert has_invalid_input([]) is False


# Join coverage


def test_low_verdict_coverage_shows_a_warning() -> None:
    [verdicts, _] = coverage_lines(JoinCoverage(412, 1030, 412, 412))
    assert verdicts.message == (
        "WARNING: 412 of 1,030 verdicts matched a trace (40%). Less than half matched, so the "
        "results may be misleading; check mapping.case_id in detecttrace.yaml."
    )


def test_low_trace_coverage_shows_a_warning() -> None:
    [_, traces] = coverage_lines(JoinCoverage(10, 10, 10, 30))
    assert traces.message.startswith("WARNING: 10 of 30 traces matched a verdict (33%).")


def test_coverage_at_exactly_half_has_no_warning() -> None:
    [verdicts, _] = coverage_lines(JoinCoverage(515, 1030, 515, 515))
    assert verdicts.message == "515 of 1,030 verdicts matched a trace (50%)."


def test_full_coverage_line() -> None:
    [_, traces] = coverage_lines(JoinCoverage(3, 3, 1, 1))
    assert traces.message == "1 of 1 trace matched a verdict (100%)."


def test_coverage_just_below_half_is_not_rounded_up() -> None:
    [verdicts, _] = coverage_lines(JoinCoverage(999, 2000, 1, 1))
    assert verdicts.message.startswith("WARNING: 999 of 2,000 verdicts matched a trace (49%).")


def test_zero_verdicts_give_a_clear_line() -> None:
    [verdicts, _] = coverage_lines(JoinCoverage(0, 0, 0, 5))
    assert verdicts.message == "0 of 0 verdicts matched a trace: no verdicts were read."


def test_zero_traces_give_a_clear_line() -> None:
    [_, traces] = coverage_lines(JoinCoverage(0, 5, 0, 0))
    assert traces.message == "0 of 0 traces matched a verdict: no traces were read."


def test_coverage_lines_flag_a_low_side() -> None:
    lines = coverage_lines(JoinCoverage(1, 3, 1, 1))
    assert [line.is_low for line in lines] == [True, False]


def test_coverage_below_half_is_low() -> None:
    assert is_low_coverage(999, 2000) is True


def test_coverage_at_exactly_half_is_not_low() -> None:
    assert is_low_coverage(1, 2) is False


def test_coverage_with_nothing_read_is_not_low() -> None:
    assert is_low_coverage(0, 0) is False


def test_coverage_hint_uses_the_config_name() -> None:
    [verdicts, _] = coverage_lines(JoinCoverage(1, 3, 1, 1), config_name="prod.yaml")
    assert verdicts.message.endswith("check mapping.case_id in prod.yaml.")
