from dataclasses import replace
from pathlib import Path
from typing import Any

from builders import case_root, otlp_document, otlp_span, span_hex, tool_span, write_jsonl

from detecttrace.cases import build_trace_cases
from detecttrace.config import Config
from detecttrace.join import join_cases
from detecttrace.model import Case, IssueKind, ToolCall, TraceCase, Verdict, VerdictRow
from detecttrace.otlp import load_spans
from detecttrace.verdicts import read_verdicts

CONFIG = Config(
    label_map={"TP": Verdict.TRUE_POSITIVE, "FP": Verdict.FALSE_POSITIVE, "Benign": Verdict.BENIGN}
)


BASE_TRACE_CASE = TraceCase(
    case_id="DT-1",
    trace_id="t",
    root_span_id="r",
    start_ns=5,
    end_ns=9,
    alert_class="impossible_travel",
    agent_label="benign",
    prompt_version="v1",
    tool_calls=(),
    is_incomplete_trace=False,
)


def trace_case(case_id: str = "DT-1", **overrides: Any) -> TraceCase:
    return replace(BASE_TRACE_CASE, case_id=case_id, **overrides)


def row(
    case_id: str = "DT-1", alert_class: str = "impossible_travel", label: str = "TP", line: int = 2
) -> VerdictRow:
    return VerdictRow(case_id, alert_class, label, line)


def test_joins_trace_and_verdict_into_a_case() -> None:
    cases, _ = join_cases([trace_case()], [row()], CONFIG)

    assert cases == [
        Case(
            case_id="DT-1",
            alert_class="impossible_travel",
            prompt_version="v1",
            analyst_verdict=Verdict.TRUE_POSITIVE,
            agent_verdict=Verdict.BENIGN,
            start_ns=5,
            tool_calls=(),
            is_incomplete_trace=False,
        )
    ]


def test_root_without_verdict_is_not_scored() -> None:
    cases, _ = join_cases([trace_case()], [], CONFIG)

    assert cases == []


def test_root_without_verdict_is_reported() -> None:
    _, issues = join_cases([trace_case()], [], CONFIG)

    assert [i.kind for i in issues] == [IssueKind.ROOT_WITHOUT_VERDICT]


def test_verdict_without_root_is_reported() -> None:
    _, issues = join_cases([], [row()], CONFIG)

    assert [i.kind for i in issues] == [IssueKind.VERDICT_WITHOUT_ROOT]


def test_alert_class_conflict_uses_the_csv_value() -> None:
    cases, _ = join_cases([trace_case(alert_class="oauth_consent")], [row()], CONFIG)

    assert cases[0].alert_class == "impossible_travel"


def test_alert_class_conflict_is_reported() -> None:
    _, issues = join_cases([trace_case(alert_class="oauth_consent")], [row()], CONFIG)

    assert [i.kind for i in issues] == [IssueKind.ALERT_CLASS_CONFLICT]


def test_alert_class_differing_only_in_case_and_spaces_is_not_a_conflict() -> None:
    _, issues = join_cases(
        [trace_case(alert_class=" Impossible_Travel ")],
        [row(alert_class="impossible_travel")],
        CONFIG,
    )

    assert issues == []


def test_missing_prompt_version_goes_to_unknown() -> None:
    cases, _ = join_cases([trace_case(prompt_version=None)], [row()], CONFIG)

    assert cases[0].prompt_version == "unknown"


def test_unmapped_analyst_label_is_left_out_of_verdicts() -> None:
    cases, _ = join_cases([trace_case()], [row(label="Escalated")], CONFIG)

    assert cases[0].analyst_verdict is None


def test_unmapped_analyst_label_is_reported_with_the_label() -> None:
    _, issues = join_cases([trace_case()], [row(label="Escalated")], CONFIG)

    assert [(i.kind, i.detail) for i in issues] == [(IssueKind.UNMAPPED_ANALYST_LABEL, "Escalated")]


def test_unmapped_agent_label_is_reported() -> None:
    _, issues = join_cases([trace_case(agent_label="maybe")], [row()], CONFIG)

    assert [i.kind for i in issues] == [IssueKind.UNMAPPED_AGENT_LABEL]


def test_missing_agent_verdict_is_reported() -> None:
    _, issues = join_cases([trace_case(agent_label=None)], [row()], CONFIG)

    assert [i.kind for i in issues] == [IssueKind.MISSING_AGENT_VERDICT]


def test_duplicate_rows_with_the_same_mapped_verdict_keep_it() -> None:
    config = Config(label_map={"TP": Verdict.TRUE_POSITIVE, "Malicious": Verdict.TRUE_POSITIVE})

    cases, _ = join_cases([trace_case()], [row(label="TP"), row(label="Malicious", line=3)], config)

    assert cases[0].analyst_verdict == Verdict.TRUE_POSITIVE


def test_duplicate_rows_with_the_same_verdict_are_reported() -> None:
    _, issues = join_cases([trace_case()], [row(label="TP"), row(label=" tp", line=3)], CONFIG)

    assert [i.kind for i in issues] == [IssueKind.DUPLICATE_VERDICT]


def test_conflicting_analyst_verdicts_leave_the_case_out_of_verdict_metrics() -> None:
    cases, _ = join_cases([trace_case()], [row(label="TP"), row(label="FP", line=3)], CONFIG)

    assert cases[0].analyst_verdict is None


def test_conflicting_analyst_verdicts_are_reported() -> None:
    _, issues = join_cases([trace_case()], [row(label="TP"), row(label="FP", line=3)], CONFIG)

    assert [i.kind for i in issues] == [IssueKind.CONFLICTING_ANALYST_VERDICT]


def test_duplicate_verdict_detail_names_each_row_alert_class_and_label() -> None:
    _, issues = join_cases(
        [trace_case(alert_class=None)],
        [row(alert_class="Phishing"), row(alert_class="phishing ", line=3)],
        CONFIG,
    )

    assert [i.detail for i in issues] == ["line 2: Phishing/TP, line 3: phishing /TP"]


def test_conflicting_verdict_detail_names_each_row_alert_class_and_label() -> None:
    _, issues = join_cases(
        [trace_case(alert_class=None)],
        [row(alert_class="Phishing"), row(alert_class="Phishing", label="FP", line=3)],
        CONFIG,
    )

    assert [i.detail for i in issues] == ["line 2: Phishing/TP, line 3: Phishing/FP"]


def test_duplicate_rows_with_different_alert_classes_are_reported() -> None:
    _, issues = join_cases(
        [trace_case(alert_class=None)],
        [row(alert_class="Phishing"), row(alert_class="Malware", line=3)],
        CONFIG,
    )

    assert [(i.kind, i.detail) for i in issues] == [
        (
            IssueKind.ALERT_CLASS_CONFLICT,
            "CSV rows give 'Phishing', 'Malware'; using the first row's 'Phishing'",
        ),
        (IssueKind.DUPLICATE_VERDICT, "line 2: Phishing/TP, line 3: Malware/TP"),
    ]


def test_duplicate_rows_with_different_alert_classes_use_the_first_class() -> None:
    cases, _ = join_cases(
        [trace_case(alert_class=None)],
        [row(alert_class="Phishing"), row(alert_class="Malware", line=3)],
        CONFIG,
    )

    assert cases[0].alert_class == "Phishing"


def test_mapped_label_mixed_with_an_unmapped_one_reports_both_problems() -> None:
    _, issues = join_cases(
        [trace_case()], [row(label="TP"), row(label="Malicious", line=3)], CONFIG
    )

    assert [(i.kind, i.detail) for i in issues] == [
        (IssueKind.UNMAPPED_ANALYST_LABEL, "Malicious"),
        (
            IssueKind.CONFLICTING_ANALYST_VERDICT,
            "line 2: impossible_travel/TP, line 3: impossible_travel/Malicious",
        ),
    ]


def test_repeated_unmapped_label_is_reported_once() -> None:
    _, issues = join_cases(
        [trace_case()], [row(label="Escalated"), row(label=" escalated", line=3)], CONFIG
    )

    assert [i.kind for i in issues] == [
        IssueKind.UNMAPPED_ANALYST_LABEL,
        IssueKind.DUPLICATE_VERDICT,
    ]


def test_cases_are_sorted_by_case_id() -> None:
    cases, _ = join_cases(
        [trace_case("DT-2"), trace_case("DT-1")], [row("DT-1"), row("DT-2")], CONFIG
    )

    assert [c.case_id for c in cases] == ["DT-1", "DT-2"]


def test_end_to_end_from_files(tmp_path: Path) -> None:
    root = case_root(
        span_hex(1), "DT-7", attributes={"detecttrace.alert_class": "impossible_travel"}
    )
    tool = tool_span(span_hex(2), span_hex(1), "check_mfa_status", start_ns=10, end_ns=20)
    doc = otlp_document(
        [
            otlp_span(root.span_id, name=root.name, attributes=root.attributes),
            otlp_span(
                tool.span_id,
                span_hex(1),
                name=tool.name,
                start_ns=10,
                end_ns=20,
                attributes=tool.attributes,
            ),
        ]
    )
    write_jsonl(tmp_path / "traces.jsonl", [doc])
    verdict_path = tmp_path / "verdicts.csv"
    verdict_path.write_text("case_id,alert_class,verdict\nDT-7,impossible_travel,TP\n")

    spans, _ = load_spans(tmp_path / "traces.jsonl")
    trace_cases, _ = build_trace_cases(spans, CONFIG.mapping)
    rows, _ = read_verdicts(verdict_path)
    cases, _ = join_cases(trace_cases, rows, CONFIG)

    assert cases == [
        Case(
            case_id="DT-7",
            alert_class="impossible_travel",
            prompt_version="unknown",
            analyst_verdict=Verdict.TRUE_POSITIVE,
            agent_verdict=Verdict.BENIGN,
            start_ns=1_000,
            tool_calls=(
                ToolCall(
                    span_id=span_hex(2),
                    tool_name="check_mfa_status",
                    arguments=None,
                    start_ns=10,
                    end_ns=20,
                    is_failed=False,
                ),
            ),
            is_incomplete_trace=False,
        )
    ]
