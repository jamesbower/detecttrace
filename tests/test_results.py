import json
import math
import os
import sys
from collections.abc import Mapping, Sequence
from pathlib import Path
from typing import Any

import pytest
from builders import case_root, otlp_document, otlp_span, span_hex, tool_span, write_jsonl
from scale import SCALE_CHECKLISTS, make_scale_cases

from detecttrace.cases import build_trace_cases
from detecttrace.checklist import Checklist, ChecklistItem
from detecttrace.config import Config
from detecttrace.join import join_cases
from detecttrace.metrics import compute_metrics
from detecttrace.model import Case, IssueKind, ToolCall, Verdict
from detecttrace.otlp import load_spans
from detecttrace.results import build_results, is_results_file, write_results_json
from detecttrace.summary import JoinCoverage, Severity, SummaryLine
from detecttrace.verdicts import read_verdicts

DAY_NS = 86_400 * 1_000_000_000
# Monday 2026-09-14, the start of ISO week 2026-W38.
W38_NS = 1_789_344_000 * 1_000_000_000
TP, FP, BENIGN = Verdict.TRUE_POSITIVE, Verdict.FALSE_POSITIVE, Verdict.BENIGN
CHECKLISTS = {
    "impossible_travel": Checklist(
        alert_class="impossible_travel",
        items=(
            ChecklistItem(id="signins", tool="get_signin_logs"),
            ChecklistItem(id="mfa", tool="check_mfa_status"),
            ChecklistItem(id="user", tool="lookup_user"),
        ),
    )
}
COVERAGE = JoinCoverage(verdicts_matched=3, verdicts_total=4, traces_matched=3, traces_total=5)
SOURCE = {"traces": "traces/", "verdicts": "verdicts.csv", "checklists": "checklists/"}
NOTE = SummaryLine(
    severity=Severity.WARNING,
    kind=IssueKind.DUPLICATE_VERDICT,
    count=2,
    message="2 duplicate verdict rows. Keep one row per case.",
    terminal_message="2 duplicate verdict rows. Keep one row per case.",
    examples=("DT-1", "DT-2"),
)


def call(
    tool: str = "get_signin_logs",
    *,
    arguments: str | dict[str, object] | None = None,
    is_failed: bool = False,
    start_ns: int = 0,
    end_ns: int = 2_500_000,
) -> ToolCall:
    return ToolCall(
        span_id=span_hex(1),
        tool_name=tool,
        arguments=arguments,
        start_ns=start_ns,
        end_ns=end_ns,
        is_failed=is_failed,
    )


ALL_STEPS = (call("get_signin_logs"), call("check_mfa_status"), call("lookup_user"))


def case(
    case_id: str,
    *,
    analyst: Verdict | None = BENIGN,
    agent: Verdict | None = BENIGN,
    version: str | None = "v1",
    day: int = 0,
    calls: tuple[ToolCall, ...] = ALL_STEPS,
    alert_class: str = "impossible_travel",
) -> Case:
    return Case(
        case_id=case_id,
        alert_class=alert_class,
        prompt_version=version,
        analyst_verdict=analyst,
        agent_verdict=agent,
        start_ns=W38_NS + day * DAY_NS,
        tool_calls=calls,
        is_incomplete_trace=False,
    )


def results_for(
    cases: Sequence[Case],
    *,
    checklists: Mapping[str, Checklist] = CHECKLISTS,
    max_detail_cases: int = 2_000,
    notes: Sequence[SummaryLine] = (NOTE,),
) -> dict[str, Any]:
    report, _ = compute_metrics(cases, checklists)
    return build_results(report, cases, checklists, notes, COVERAGE, SOURCE, max_detail_cases)


def dumped(results: dict[str, Any], path: Path) -> str:
    write_results_json(results, path)
    return path.read_text(encoding="utf-8")


MIXED = [
    case("DT-1", analyst=TP, agent=FP, day=1),
    case("DT-2", version=None, day=2, calls=(call("get_signin_logs"),)),
    case("DT-3", version="v2", day=15, calls=(call("get_signin_logs", is_failed=True),)),
    case("DT-4", analyst=None, agent=TP, alert_class="oauth_consent", day=3),
]


def test_round_trip_through_the_file_gives_the_same_object(tmp_path: Path):
    results = results_for(MIXED)

    assert json.loads(dumped(results, tmp_path / "out.json")) == results


def test_schema_version_is_one():
    assert results_for(MIXED)["schema_version"] == 1


def test_top_level_keys_come_in_a_fixed_order():
    assert list(results_for(MIXED)) == [
        "schema_version",
        "generated_by",
        "source",
        "totals",
        "classes",
        "data_notes",
        "case_rows",
        "case_detail",
    ]


def test_generated_by_names_the_tool_and_its_version():
    assert results_for(MIXED)["generated_by"].startswith("detecttrace ")


def test_source_is_copied_as_given():
    assert results_for(MIXED)["source"] == SOURCE


def test_totals_count_cases_classes_period_versions_and_coverage():
    assert results_for(MIXED)["totals"] == {
        "cases": 4,
        "classes": 2,
        "period": {"first_week": "2026-W38", "last_week": "2026-W40"},
        "versions": [None, "v1", "v2"],
        "coverage": {
            "verdicts_matched": 3,
            "verdicts_total": 4,
            "traces_matched": 3,
            "traces_total": 5,
        },
    }


def test_totals_period_is_null_without_cases():
    assert results_for([])["totals"]["period"] == {"first_week": None, "last_week": None}


def test_data_notes_carry_every_summary_line_field():
    assert results_for(MIXED)["data_notes"] == [
        {
            "severity": "warning",
            "kind": "duplicate_verdict",
            "count": 2,
            "message": "2 duplicate verdict rows. Keep one row per case.",
            "examples": ["DT-1", "DT-2"],
        }
    ]


def test_a_class_keeps_its_checklist_item_ids():
    assert results_for(MIXED)["classes"][0]["checklist_item_ids"] == ["signins", "mfa", "user"]


def test_a_class_lists_per_version_records_in_first_seen_order():
    by_version = results_for(MIXED)["classes"][0]["by_version"]

    assert [record["version"] for record in by_version] == ["v1", None, "v2"]


def test_a_version_record_carries_its_first_week_metrics_and_skipped_steps():
    record = results_for(MIXED)["classes"][0]["by_version"][2]

    assert (record["first_week"], record["metrics"]["case_count"], record["skipped"][0]) == (
        "2026-W40",
        1,
        {"item_id": "signins", "skipped": 1, "n": 1, "rate": 1.0},
    )


def test_a_slice_keeps_the_dangerous_false_closes():
    assert results_for(MIXED)["classes"][0]["overall"]["dangerous_false_closes"] == ["DT-1"]


def test_a_slice_keeps_the_confusion_matrix_rows():
    assert results_for(MIXED)["classes"][0]["overall"]["confusion"] == [
        [0, 1, 0],
        [0, 0, 0],
        [0, 0, 2],
    ]


def test_a_real_version_named_null_stays_apart_from_no_version(tmp_path: Path):
    cases = [case("DT-1", version=None), case("DT-2", version="null", day=1)]

    data = json.loads(dumped(results_for(cases), tmp_path / "out.json"))

    assert [record["version"] for record in data["classes"][0]["by_version"]] == [None, "null"]


def test_case_rows_keep_a_version_named_null_apart_from_no_version(tmp_path: Path):
    cases = [case("DT-1", version=None), case("DT-2", version="null", day=1)]

    rows = json.loads(dumped(results_for(cases), tmp_path / "out.json"))["case_rows"]

    assert [
        None if index is None else rows["strings"][index] for index in rows["columns"]["version"]
    ] == [None, "null"]


def test_pooled_versions_sit_under_other_not_under_a_version_named_other():
    cases = [
        *(case(f"DT-{index}", version=f"v{index}", day=index) for index in range(1, 8)),
        case("DT-9", version="other", day=9),
    ]

    data = results_for(cases)["classes"][0]

    assert (data["other"]["metrics"]["case_count"], data["other_versions"]) == (2, ["v7", "other"])


def test_other_is_null_when_nothing_is_pooled():
    assert results_for(MIXED)["classes"][0]["other"] is None


def test_trend_points_carry_their_scope():
    trend = results_for(MIXED)["classes"][0]["trend"]

    assert [(point["scope"], point["version"]) for point in trend[:3]] == [
        ("all", None),
        ("version", "v1"),
        ("version", None),
    ]


def test_strings_table_has_no_duplicates():
    strings = results_for(MIXED)["case_rows"]["strings"]

    assert len(strings) == len(set(strings))


def test_case_rows_list_case_ids_in_order():
    assert results_for(MIXED)["case_rows"]["columns"]["case_id"] == [
        "DT-1",
        "DT-2",
        "DT-3",
        "DT-4",
    ]


def test_case_row_class_indexes_resolve_to_the_class_names():
    rows = results_for(MIXED)["case_rows"]

    assert [rows["strings"][index] for index in rows["columns"]["class"]] == [
        "impossible_travel",
        "impossible_travel",
        "impossible_travel",
        "oauth_consent",
    ]


def test_case_row_week_indexes_resolve_to_iso_weeks():
    rows = results_for(MIXED)["case_rows"]

    assert [rows["strings"][index] for index in rows["columns"]["week"]] == [
        "2026-W38",
        "2026-W38",
        "2026-W40",
        "2026-W38",
    ]


def test_verdict_codes_are_documented():
    assert results_for(MIXED)["case_rows"]["verdict_codes"] == [
        "true_positive",
        "false_positive",
        "benign",
    ]


def test_analyst_codes_use_minus_one_for_an_unknown_verdict():
    assert results_for(MIXED)["case_rows"]["columns"]["analyst"] == [0, 2, 2, -1]


def test_agent_codes_index_the_verdict_codes():
    assert results_for(MIXED)["case_rows"]["columns"]["agent"] == [1, 2, 2, 0]


def test_satisfied_counts_are_null_without_a_checklist():
    assert results_for(MIXED)["case_rows"]["columns"]["satisfied"] == [3, 1, 0, None]


def test_missed_items_are_checklist_item_indexes():
    assert results_for(MIXED)["case_rows"]["columns"]["missed_items"] == [[], [1, 2], [1, 2], []]


def test_failed_items_are_checklist_item_indexes():
    assert results_for(MIXED)["case_rows"]["columns"]["failed_items"] == [[], [], [0], []]


def test_case_rows_name_the_item_ids_of_each_class_checklist():
    rows = results_for(MIXED)["case_rows"]

    assert [
        (rows["strings"][entry["class"]], [rows["strings"][index] for index in entry["items"]])
        for entry in rows["checklists"]
    ] == [("impossible_travel", ["signins", "mfa", "user"])]


def test_detail_takes_dangerous_closes_then_the_newest_disagreement_up_to_the_cap():
    cases = [
        case("DT-01", analyst=TP, agent=BENIGN, day=1),
        case("DT-02", analyst=TP, agent=FP, day=2),
        *(case(f"DT-1{day}", analyst=FP, agent=BENIGN, day=day) for day in range(5)),
    ]

    assert list(results_for(cases, max_detail_cases=3)["case_detail"]) == [
        "DT-02",
        "DT-01",
        "DT-14",
    ]


def test_detail_puts_failed_calls_before_missed_steps():
    cases = [
        case("DT-1", calls=(call("get_signin_logs"),), day=5),
        case("DT-2", calls=(*ALL_STEPS, call("lookup_user", is_failed=True))),
    ]

    assert list(results_for(cases)["case_detail"]) == ["DT-2", "DT-1"]


def test_detail_breaks_start_time_ties_by_case_id():
    cases = [case("DT-2", analyst=TP, agent=FP), case("DT-1", analyst=TP, agent=FP)]

    assert list(results_for(cases)["case_detail"]) == ["DT-1", "DT-2"]


def test_detail_leaves_out_unremarkable_cases():
    assert results_for([case("DT-1")])["case_detail"] == {}


def test_detail_is_empty_when_the_cap_is_zero():
    assert results_for(MIXED, max_detail_cases=0)["case_detail"] == {}


def test_detail_lists_each_call_with_status_duration_and_arguments():
    cases = [
        case(
            "DT-1",
            calls=(
                call("get_signin_logs", arguments='{"user":"a@example.com"}'),
                call("lookup_user", is_failed=True),
            ),
        )
    ]

    assert results_for(cases)["case_detail"]["DT-1"] == [
        {
            "tool": "get_signin_logs",
            "status": "success",
            "duration_ms": 2.5,
            "arguments": '{"user":"a@example.com"}',
        },
        {"tool": "lookup_user", "status": "failed", "duration_ms": 2.5, "arguments": None},
    ]


def test_long_arguments_are_cut_to_200_characters_with_an_ellipsis():
    cases = [case("DT-1", analyst=TP, agent=FP, calls=(call(arguments="x" * 10_000),))]

    arguments = results_for(cases)["case_detail"]["DT-1"][0]["arguments"]

    assert (len(arguments), arguments[-1]) == (200, "…")


def test_map_arguments_are_serialized_in_their_original_key_order():
    cases = [case("DT-1", analyst=TP, agent=FP, calls=(call(arguments={"z": 1, "a": [2]}),))]

    assert results_for(cases)["case_detail"]["DT-1"][0]["arguments"] == '{"z": 1, "a": [2]}'


PLANTED_RESULT = "RESULT-9f2c-do-not-embed"


def load_planted_result_json(tmp_path: Path) -> str:
    """Run a trace whose tool span carries a result through the loader, join and results."""
    root = case_root(
        span_hex(1), "DT-7", attributes={"detecttrace.alert_class": "impossible_travel"}
    )
    tool = tool_span(
        span_hex(2),
        span_hex(1),
        attributes={
            "gen_ai.tool.call.arguments": '{"user":"a@example.com"}',
            "gen_ai.tool.call.result": PLANTED_RESULT,
        },
    )
    document = otlp_document(
        [
            otlp_span(root.span_id, name=root.name, attributes=root.attributes),
            otlp_span(tool.span_id, span_hex(1), name=tool.name, attributes=tool.attributes),
        ]
    )
    write_jsonl(tmp_path / "traces.jsonl", [document])
    verdict_path = tmp_path / "verdicts.csv"
    verdict_path.write_text("case_id,alert_class,verdict\nDT-7,impossible_travel,TP\n")
    config = Config(label_map={"TP": TP, "benign": BENIGN})
    spans, _ = load_spans(tmp_path / "traces.jsonl")
    trace_cases, _ = build_trace_cases(spans, config.mapping)
    rows, _ = read_verdicts(verdict_path)
    cases, _ = join_cases(trace_cases, rows, config)
    return dumped(results_for(cases), tmp_path / "out.json")


def test_a_tool_result_never_reaches_the_json(tmp_path: Path):
    assert PLANTED_RESULT not in load_planted_result_json(tmp_path)


def test_the_call_with_a_planted_result_is_in_the_detail(tmp_path: Path):
    # Guards the test above: the call must reach the detail, or that check proves nothing.
    detail = json.loads(load_planted_result_json(tmp_path))["case_detail"]

    assert detail["DT-7"][0]["arguments"] == '{"user":"a@example.com"}'


def test_two_runs_write_byte_identical_files(tmp_path: Path):
    first = dumped(results_for(MIXED), tmp_path / "first.json")

    assert dumped(results_for(MIXED), tmp_path / "second.json") == first


def test_writing_replaces_an_existing_file(tmp_path: Path):
    path = tmp_path / "out.json"
    path.write_text("old", encoding="utf-8")

    assert json.loads(dumped(results_for(MIXED), path))["schema_version"] == 1


def test_a_nan_raises_value_error(tmp_path: Path):
    results = results_for(MIXED)
    results["totals"]["cases"] = math.nan

    with pytest.raises(ValueError):
        write_results_json(results, tmp_path / "out.json")


def test_a_nan_leaves_no_file_behind(tmp_path: Path):
    results = results_for(MIXED)
    results["totals"]["cases"] = math.nan

    with pytest.raises(ValueError):
        write_results_json(results, tmp_path / "out.json")

    assert list(tmp_path.iterdir()) == []


def test_case_rows_stay_within_60_bytes_per_case():
    cases = make_scale_cases(5_000)
    report, _ = compute_metrics(cases, SCALE_CHECKLISTS)
    results = build_results(report, cases, SCALE_CHECKLISTS, [], COVERAGE, SOURCE, 0)

    size = len(json.dumps(results["case_rows"], separators=(",", ":"), ensure_ascii=False))

    assert size / 5_000 <= 60


# Recognizing a results file before replacing it


def test_a_written_results_file_is_recognized(tmp_path: Path):
    path = tmp_path / "out.json"
    write_results_json(results_for(MIXED), path)

    assert is_results_file(path) is True


def test_a_results_file_larger_than_the_read_limit_is_recognized(tmp_path: Path):
    path = tmp_path / "out.json"
    results = results_for(MIXED)
    results["case_detail"] = [{"case_id": "x" * 200_000, "calls": []}]
    write_results_json(results, path)

    assert is_results_file(path) is True


def test_generated_by_after_other_keys_is_recognized(tmp_path: Path):
    path = tmp_path / "out.json"
    path.write_text('{ "a" : [1, {"b": 2}] ,\n "generated_by": "detecttrace 9"}', encoding="utf-8")

    assert is_results_file(path) is True


def test_a_file_from_another_tool_is_not_recognized(tmp_path: Path):
    path = tmp_path / "out.json"
    path.write_text('{"generated_by": "othertool 1.0"}', encoding="utf-8")

    assert is_results_file(path) is False


def test_generated_by_in_a_nested_object_is_not_recognized(tmp_path: Path):
    path = tmp_path / "out.json"
    path.write_text('{"x": {"generated_by": "detecttrace 1"}}', encoding="utf-8")

    assert is_results_file(path) is False


def test_generated_by_that_is_not_a_string_is_not_recognized(tmp_path: Path):
    path = tmp_path / "out.json"
    path.write_text('{"generated_by": ["detecttrace"]}', encoding="utf-8")

    assert is_results_file(path) is False


def test_generated_by_beyond_the_read_limit_is_not_recognized(tmp_path: Path):
    path = tmp_path / "out.json"
    path.write_text(
        json.dumps({"a": "x" * 100_000, "generated_by": "detecttrace 1"}), encoding="utf-8"
    )

    assert is_results_file(path) is False


def test_a_text_file_is_not_recognized(tmp_path: Path):
    path = tmp_path / "notes.txt"
    path.write_text("my notes about detecttrace", encoding="utf-8")

    assert is_results_file(path) is False


def test_a_binary_file_is_not_recognized(tmp_path: Path):
    path = tmp_path / "out.json"
    path.write_bytes(b"\xff\xfe\x00{")

    assert is_results_file(path) is False


def test_deeply_nested_json_is_not_recognized(tmp_path: Path):
    path = tmp_path / "out.json"
    path.write_text('{"a": ' + "[" * 50_000, encoding="utf-8")

    assert is_results_file(path) is False


def test_a_folder_is_not_recognized(tmp_path: Path):
    assert is_results_file(tmp_path) is False


def test_a_missing_file_is_not_recognized(tmp_path: Path):
    assert is_results_file(tmp_path / "missing.json") is False


@pytest.mark.skipif(sys.platform == "win32", reason="needs a POSIX named pipe")
def test_a_named_pipe_is_not_recognized_and_not_opened(tmp_path: Path):
    path = tmp_path / "pipe.json"
    os.mkfifo(path)

    assert is_results_file(path) is False
