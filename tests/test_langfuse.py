import json
import shutil
from pathlib import Path
from typing import Any

import pytest
from builders import (
    TRACE_ID,
    langfuse_row,
    otlp_document,
    otlp_span,
    span_hex,
    write_jsonl,
    write_run_folder,
)
from html_tree import has_tag, parse_html
from typer.testing import CliRunner

from detecttrace import cli
from detecttrace.cases import build_trace_cases
from detecttrace.config import MappingConfig
from detecttrace.langfuse import find_missing_tool_calls
from detecttrace.model import Issue, IssueKind, Span, ToolCall, TraceCase
from detecttrace.pipeline import run_check
from detecttrace.runconfig import load_run_config
from detecttrace.summary import summarize_issues
from detecttrace.traces import load_spans

S1 = span_hex(1)
S2 = span_hex(2)
S3 = span_hex(3)
# 2026-09-30T08:00:00Z
BASE_NS = 1_790_755_200_000_000_000
SECOND = 1_000_000_000
REAL = Path(__file__).parent / "fixtures" / "langfuse_real"
API_PAGES = [f"v2_all_fields_page{number}.json" for number in (1, 2, 3, 4)]
NO_IO_PAGES = [f"v2_no_io_page{number}.json" for number in (1, 2, 3, 4)]


def load(
    tmp_path: Path, document: object, name: str = "rows.json"
) -> tuple[list[Span], list[Issue]]:
    path = tmp_path / name
    path.write_text(json.dumps(document), encoding="utf-8")
    return load_spans(path, format="langfuse")


def load_lines(tmp_path: Path, rows: list[Any]) -> tuple[list[Span], list[Issue]]:
    path = tmp_path / "rows.jsonl"
    path.write_text("".join(json.dumps(row) + "\n" for row in rows), encoding="utf-8")
    return load_spans(path, format="langfuse")


def only_span(tmp_path: Path, row: dict[str, Any]) -> Span:
    [span], _ = load(tmp_path, [row])
    return span


def kinds(issues: list[Issue]) -> list[IssueKind]:
    return [issue.kind for issue in issues]


def to_snake_case(row: dict[str, Any]) -> dict[str, Any]:
    names = {
        "traceId": "trace_id",
        "parentObservationId": "parent_observation_id",
        "startTime": "start_time",
        "endTime": "end_time",
        "promptName": "prompt_name",
        "promptVersion": "prompt_version",
        "sessionId": "session_id",
        "traceName": "trace_name",
    }
    return {names.get(key, key): value for key, value in row.items()}


def copy_files(tmp_path: Path, names: list[str]) -> Path:
    folder = tmp_path / "export"
    folder.mkdir()
    for name in names:
        shutil.copy(REAL / name, folder / name)
    return folder


def cases_of(spans: list[Span]) -> dict[str, TraceCase]:
    cases, _ = build_trace_cases(spans, MappingConfig())
    return {case.case_id: case for case in cases}


# Document shapes


def test_reads_the_rows_of_an_api_page(tmp_path: Path) -> None:
    spans, _ = load(tmp_path, {"data": [langfuse_row(S1), langfuse_row(S2)], "meta": {}})
    assert [span.span_id for span in spans] == [S1, S2]


def test_reads_a_pretty_printed_json_array_of_rows(tmp_path: Path) -> None:
    path = tmp_path / "rows.json"
    path.write_text(json.dumps([langfuse_row(S1), langfuse_row(S2)], indent=2), encoding="utf-8")
    spans, _ = load_spans(path, format="langfuse")
    assert [span.span_id for span in spans] == [S1, S2]


def test_reads_a_json_array_of_rows_on_one_line(tmp_path: Path) -> None:
    spans, _ = load(tmp_path, [langfuse_row(S1), langfuse_row(S2)])
    assert [span.span_id for span in spans] == [S1, S2]


def test_reads_one_row_per_json_line(tmp_path: Path) -> None:
    spans, _ = load_lines(tmp_path, [langfuse_row(S1), langfuse_row(S2)])
    assert [span.span_id for span in spans] == [S1, S2]


def test_an_empty_array_is_read_without_issues(tmp_path: Path) -> None:
    assert load(tmp_path, []) == ([], [])


def test_a_snake_case_row_gives_the_same_span_as_its_camel_case_form(tmp_path: Path) -> None:
    row = langfuse_row(S2, S1, promptName="triage", sessionId="s-1", traceName="t")
    assert only_span(tmp_path, to_snake_case(row)) == only_span(tmp_path, row)


def test_camel_case_and_snake_case_rows_mix_in_one_file(tmp_path: Path) -> None:
    spans, _ = load_lines(tmp_path, [langfuse_row(S1), to_snake_case(langfuse_row(S2))])
    assert [span.span_id for span in spans] == [S1, S2]


def test_a_readable_row_gives_no_issues(tmp_path: Path) -> None:
    row = langfuse_row(S1, type="TOOL", input='{"a": 1}', output="{}")
    assert load(tmp_path, [row])[1] == []


# Field mapping


def test_ids_and_parent_come_from_the_row(tmp_path: Path) -> None:
    span = only_span(tmp_path, langfuse_row(S2, S1))
    assert (span.trace_id, span.span_id, span.parent_span_id) == (TRACE_ID, S2, S1)


@pytest.mark.parametrize("parent", [None, ""])
def test_a_null_or_empty_parent_means_none(tmp_path: Path, parent: str | None) -> None:
    assert only_span(tmp_path, langfuse_row(S1, parent)).parent_span_id is None


def test_hex_ids_are_lowercased(tmp_path: Path) -> None:
    span = only_span(tmp_path, langfuse_row(S1.upper(), trace_id=TRACE_ID.upper()))
    assert (span.trace_id, span.span_id) == (TRACE_ID, S1)


def test_ids_that_are_not_hex_are_kept_as_given(tmp_path: Path) -> None:
    span = only_span(tmp_path, langfuse_row("Obs-1", "Obs-0", trace_id="Trace-A"))
    assert (span.trace_id, span.span_id, span.parent_span_id) == ("Trace-A", "Obs-1", "Obs-0")


def test_the_name_comes_from_the_row(tmp_path: Path) -> None:
    assert only_span(tmp_path, langfuse_row(S1, name="invoke_agent triage")).name == (
        "invoke_agent triage"
    )


def test_level_error_marks_the_span_as_an_error(tmp_path: Path) -> None:
    assert only_span(tmp_path, langfuse_row(S1, level="ERROR")).is_error is True


@pytest.mark.parametrize("level", ["DEFAULT", "WARNING", "DEBUG"])
def test_other_levels_are_not_errors(tmp_path: Path, level: str) -> None:
    assert only_span(tmp_path, langfuse_row(S1, level=level)).is_error is False


def test_prefixed_metadata_keys_become_span_attributes(tmp_path: Path) -> None:
    row = langfuse_row(S1, attributes={"detecttrace.case_id": "DT-1", "detecttrace.score": 0.5})
    assert only_span(tmp_path, row).attributes == {
        "detecttrace.case_id": "DT-1",
        "detecttrace.score": 0.5,
    }


def test_resource_metadata_keys_become_resource_attributes(tmp_path: Path) -> None:
    row = langfuse_row(S1, resource={"service.name": "soc-agent", "soc.team": "t1"})
    assert only_span(tmp_path, row).resource_attributes == {
        "service.name": "soc-agent",
        "soc.team": "t1",
    }


def test_scope_metadata_keys_are_ignored(tmp_path: Path) -> None:
    row = langfuse_row(S1, metadata={"scope.name": "lib", "scope.version": "1.0", "scope": "x"})
    assert only_span(tmp_path, row).attributes == {}


def test_other_metadata_keys_are_named_under_langfuse_metadata(tmp_path: Path) -> None:
    row = langfuse_row(S1, metadata={"case_id": "DT-1"})
    assert only_span(tmp_path, row).attributes == {"langfuse.metadata.case_id": "DT-1"}


@pytest.mark.parametrize(
    ("field", "value", "attribute"),
    [
        ("promptName", "triage", "langfuse.prompt_name"),
        ("promptVersion", 3, "langfuse.prompt_version"),
        ("version", "0.9.0", "langfuse.version"),
        ("sessionId", "s-1", "langfuse.session_id"),
        ("traceName", "invoke_agent triage", "langfuse.trace_name"),
    ],
)
def test_langfuse_fields_become_attributes(
    tmp_path: Path, field: str, value: object, attribute: str
) -> None:
    assert only_span(tmp_path, langfuse_row(S1) | {field: value}).attributes == {attribute: value}


@pytest.mark.parametrize("value", [None, ""])
def test_empty_langfuse_fields_are_left_out(tmp_path: Path, value: object) -> None:
    row = langfuse_row(S1, promptName=value, version=value, sessionId=value)
    assert only_span(tmp_path, row).attributes == {}


@pytest.mark.parametrize(
    ("row_type", "operation"), [("AGENT", "invoke_agent"), ("TOOL", "execute_tool")]
)
def test_the_type_gives_the_operation_when_it_is_missing(
    tmp_path: Path, row_type: str, operation: str
) -> None:
    span = only_span(tmp_path, langfuse_row(S1, type=row_type))
    assert span.attributes["gen_ai.operation.name"] == operation


def test_an_operation_attribute_wins_over_the_type(tmp_path: Path) -> None:
    row = langfuse_row(S1, type="TOOL", attributes={"gen_ai.operation.name": "chat"})
    assert only_span(tmp_path, row).attributes["gen_ai.operation.name"] == "chat"


@pytest.mark.parametrize("row_type", ["SPAN", "GENERATION", "EVENT", "RETRIEVER", "NEW_TYPE"])
def test_other_types_give_no_operation(tmp_path: Path, row_type: str) -> None:
    assert (
        "gen_ai.operation.name"
        not in only_span(tmp_path, langfuse_row(S1, type=row_type)).attributes
    )


def test_a_tool_row_is_named_by_its_name(tmp_path: Path) -> None:
    span = only_span(tmp_path, langfuse_row(S1, type="TOOL", name="get_signin_logs"))
    assert span.attributes["gen_ai.tool.name"] == "get_signin_logs"


def test_a_tool_name_attribute_wins_over_the_row_name(tmp_path: Path) -> None:
    row = langfuse_row(S1, type="TOOL", name="x", attributes={"gen_ai.tool.name": "lookup"})
    assert only_span(tmp_path, row).attributes["gen_ai.tool.name"] == "lookup"


@pytest.mark.parametrize("name", ["execute_tool", "execute_tool lookup"])
def test_a_tool_row_named_by_its_operation_is_not_given_a_tool_name(
    tmp_path: Path, name: str
) -> None:
    span = only_span(tmp_path, langfuse_row(S1, type="TOOL", name=name))
    assert "gen_ai.tool.name" not in span.attributes


def test_a_tool_row_named_execute_tool_and_a_tool_calls_that_tool(tmp_path: Path) -> None:
    rows = [
        langfuse_row(S1, type="AGENT", attributes={"detecttrace.case_id": "DT-1"}),
        langfuse_row(S2, S1, type="TOOL", name="execute_tool lookup"),
    ]
    assert cases_of(load(tmp_path, rows)[0])["DT-1"].tool_calls[0].tool_name == "lookup"


def test_a_tool_row_named_only_execute_tool_is_missing_its_tool_name(tmp_path: Path) -> None:
    rows = [
        langfuse_row(S1, type="AGENT", attributes={"detecttrace.case_id": "DT-1"}),
        langfuse_row(S2, S1, type="TOOL", name="execute_tool"),
    ]
    _, issues = build_trace_cases(load(tmp_path, rows)[0], MappingConfig())
    assert issues == [Issue(IssueKind.MISSING_TOOL_NAME, f"{TRACE_ID}/{S2}")]


def test_a_span_row_is_not_given_a_tool_name(tmp_path: Path) -> None:
    assert "gen_ai.tool.name" not in only_span(tmp_path, langfuse_row(S1, name="x")).attributes


def test_a_tool_row_input_becomes_the_tool_arguments(tmp_path: Path) -> None:
    span = only_span(tmp_path, langfuse_row(S1, type="TOOL", input='{"user": "a@example.com"}'))
    assert span.attributes["gen_ai.tool.call.arguments"] == '{"user": "a@example.com"}'


def test_a_tool_row_object_input_is_kept_as_an_argument_map(tmp_path: Path) -> None:
    span = only_span(tmp_path, langfuse_row(S1, type="TOOL", input={"range": "7d"}))
    assert span.attributes["gen_ai.tool.call.arguments"] == {"range": "7d"}


def test_a_tool_arguments_attribute_wins_over_the_input(tmp_path: Path) -> None:
    row = langfuse_row(
        S1, type="TOOL", input='{"a": 1}', attributes={"gen_ai.tool.call.arguments": '{"b": 2}'}
    )
    assert only_span(tmp_path, row).attributes["gen_ai.tool.call.arguments"] == '{"b": 2}'


@pytest.mark.parametrize("value", [None, ""])
def test_an_empty_tool_input_gives_no_arguments(tmp_path: Path, value: object) -> None:
    span = only_span(tmp_path, langfuse_row(S1, type="TOOL", input=value))
    assert "gen_ai.tool.call.arguments" not in span.attributes


def test_the_input_of_other_rows_is_not_read(tmp_path: Path) -> None:
    span = only_span(tmp_path, langfuse_row(S1, type="AGENT", input='{"prompt": "hi"}'))
    assert "gen_ai.tool.call.arguments" not in span.attributes


def test_the_output_is_never_read(tmp_path: Path) -> None:
    span = only_span(tmp_path, langfuse_row(S1, type="TOOL", output='{"secret": "result-text"}'))
    assert "result-text" not in repr(span)


def test_a_tool_input_that_is_a_list_is_reported_as_the_tool_arguments_are(
    tmp_path: Path,
) -> None:
    rows = [
        langfuse_row(S1, type="AGENT", attributes={"detecttrace.case_id": "DT-1"}),
        langfuse_row(S2, S1, type="TOOL", name="lookup", input=[1, 2]),
    ]
    spans, _ = load(tmp_path, rows)
    _, issues = build_trace_cases(spans, MappingConfig())
    assert issues == [
        Issue(
            IssueKind.INVALID_ATTRIBUTE,
            f"{TRACE_ID}/{S2}",
            "gen_ai.tool.call.arguments is a list",
        )
    ]


# Times


@pytest.mark.parametrize(
    ("text", "nanos"),
    [
        ("2026-09-30T08:00:00.000Z", BASE_NS),
        ("2026-09-30 08:00:00.000000", BASE_NS),
        ("2026-09-30T08:00:00Z", BASE_NS),
        ("2026-09-30T08:00:00", BASE_NS),
        ("2026-09-30T10:00:00+02:00", BASE_NS),
        ("2026-09-30T07:30:00-00:30", BASE_NS),
        ("2026-09-30T08:00:00.5Z", BASE_NS + 500_000_000),
        ("2026-09-30T08:00:00.123456789Z", BASE_NS + 123_456_789),
        ("2026-09-30 08:00:00.1234567891", BASE_NS + 123_456_789),
    ],
)
def test_start_times_are_read_as_utc_nanoseconds(tmp_path: Path, text: str, nanos: int) -> None:
    row = langfuse_row(S1, start=text, end="2026-09-30T09:00:00Z")
    assert only_span(tmp_path, row).start_ns == nanos


def test_end_time_is_read(tmp_path: Path) -> None:
    span = only_span(tmp_path, langfuse_row(S1, end="2026-09-30 08:00:02.000000"))
    assert span.end_ns == BASE_NS + 2 * SECOND


# Hostile rows


@pytest.mark.parametrize("row", [5, "row", None, [1], True])
def test_a_row_that_is_not_an_object_is_reported(tmp_path: Path, row: object) -> None:
    _, issues = load(tmp_path, {"data": [row]})
    assert kinds(issues) == [IssueKind.INVALID_LANGFUSE_ROW]


def test_a_bad_row_does_not_stop_the_rows_after_it(tmp_path: Path) -> None:
    spans, _ = load(tmp_path, [5, langfuse_row(S1)])
    assert [span.span_id for span in spans] == [S1]


def test_a_bad_row_on_a_json_line_names_its_line(tmp_path: Path) -> None:
    _, issues = load_lines(tmp_path, [langfuse_row(S1), {"traceId": TRACE_ID}])
    assert issues == [
        Issue(IssueKind.INVALID_LANGFUSE_ROW, "rows.jsonl", "line 2: row: id is missing")
    ]


def test_a_bad_row_in_a_page_names_its_index(tmp_path: Path) -> None:
    _, issues = load(tmp_path, {"data": [langfuse_row(S1), "row"]})
    assert issues == [
        Issue(IssueKind.INVALID_LANGFUSE_ROW, "rows.json", "line 1: data[1] is not an object")
    ]


@pytest.mark.parametrize("data", [{"id": S1}, "rows", 5, None])
def test_a_page_whose_data_is_not_a_list_is_reported(tmp_path: Path, data: object) -> None:
    _, issues = load(tmp_path, {"data": data})
    assert kinds(issues) == [IssueKind.INVALID_LANGFUSE_DOCUMENT]


# A JSON null line never reaches the parser: the reader reports it as an invalid line.
@pytest.mark.parametrize("document", [5, "text", True])
def test_a_document_that_is_not_an_object_or_list_is_reported(
    tmp_path: Path, document: object
) -> None:
    _, issues = load_lines(tmp_path, [document])
    assert kinds(issues) == [IssueKind.INVALID_LANGFUSE_DOCUMENT]


def test_an_otlp_document_read_as_langfuse_says_so(tmp_path: Path) -> None:
    _, issues = load(tmp_path, {"resourceSpans": []})
    assert "otlp" in issues[0].detail


@pytest.mark.parametrize(
    "metadata",
    [
        "not json",
        '{"attributes.detecttrace.case_id": "DT-1", "attributes.note": "cut at two hund',
        "[" * 100_000,
        '{"attributes.a": ' + "[" * 100_000 + "]" * 100_000 + "}",
        '"{\\"attributes.a\\": 1}"',
        '["attributes.a"]',
        "5",
        5,
        ["attributes.a"],
    ],
    # Short ids: pytest puts the test id in an environment variable, which Windows caps at
    # 32,767 characters, and the deep-nesting cases are 100,000 characters long.
    ids=[
        "not_json",
        "cut_off_json",
        "deep_array",
        "deep_array_in_object",
        "json_string_of_object",
        "json_array_text",
        "json_number_text",
        "number",
        "list",
    ],
)
def test_metadata_that_is_not_an_object_is_reported(tmp_path: Path, metadata: object) -> None:
    _, issues = load(tmp_path, [langfuse_row(S1, metadata=metadata)])
    assert kinds(issues) == [IssueKind.LANGFUSE_METADATA_NOT_OBJECT]


def test_metadata_given_as_a_json_string_is_read(tmp_path: Path) -> None:
    metadata = json.dumps({"attributes.detecttrace.case_id": "DT-1", "case_id": "c"})
    span = only_span(tmp_path, langfuse_row(S1, metadata=metadata))
    assert span.attributes == {"detecttrace.case_id": "DT-1", "langfuse.metadata.case_id": "c"}


def test_metadata_string_values_are_not_decoded_again(tmp_path: Path) -> None:
    metadata = json.dumps({"attributes.a": '{"b": 1}'})
    assert only_span(tmp_path, langfuse_row(S1, metadata=metadata)).attributes == {"a": '{"b": 1}'}


def test_a_lone_surrogate_in_a_metadata_string_is_replaced(tmp_path: Path) -> None:
    metadata = '{"attributes.a": "\\ud800"}'
    assert only_span(tmp_path, langfuse_row(S1, metadata=metadata)).attributes == {"a": "\ufffd"}


def test_a_row_whose_metadata_is_not_an_object_is_still_read(tmp_path: Path) -> None:
    span = only_span(tmp_path, langfuse_row(S1, type="AGENT", metadata="text"))
    assert span.attributes == {"gen_ai.operation.name": "invoke_agent"}


@pytest.mark.parametrize(
    "key", ["attributes.", "attributes", "resourceAttributes.", "resourceAttributes"]
)
def test_a_metadata_key_without_an_attribute_name_is_reported(tmp_path: Path, key: str) -> None:
    _, issues = load(tmp_path, [langfuse_row(S1, metadata={key: {"detecttrace.case_id": "x"}})])
    assert kinds(issues) == [IssueKind.INVALID_ATTRIBUTE]


def test_a_metadata_key_without_an_attribute_name_is_left_out(tmp_path: Path) -> None:
    span = only_span(
        tmp_path, langfuse_row(S1, metadata={"attributes": {"a": 1}, "attributes.": 2})
    )
    assert span.attributes == {}


def test_a_span_attribute_wins_over_a_metadata_key_with_the_same_name(tmp_path: Path) -> None:
    metadata = {"case_id": "B", "attributes.langfuse.metadata.case_id": "A"}
    span = only_span(tmp_path, langfuse_row(S1, metadata=metadata))
    assert span.attributes == {"langfuse.metadata.case_id": "A"}


def test_a_name_given_twice_with_different_values_is_reported(tmp_path: Path) -> None:
    metadata = {"case_id": "B", "attributes.langfuse.metadata.case_id": "A"}
    _, issues = load(tmp_path, [langfuse_row(S1, metadata=metadata)])
    assert issues == [
        Issue(
            IssueKind.INVALID_ATTRIBUTE,
            "rows.json",
            "line 1: [0]: langfuse.metadata.case_id is given twice with different values; "
            "the span attribute was kept",
        )
    ]


def test_a_long_name_given_twice_is_shortened_in_the_report(tmp_path: Path) -> None:
    key = "k" * 100_000
    metadata = {key: "B", f"attributes.langfuse.metadata.{key}": "A"}
    [issue] = load(tmp_path, [langfuse_row(S1, metadata=metadata)])[1]
    assert len(issue.detail) < 300


def test_a_name_given_twice_with_the_same_value_is_not_reported(tmp_path: Path) -> None:
    row = langfuse_row(S1, version="1.0", attributes={"langfuse.version": "1.0"})
    assert load(tmp_path, [row])[1] == []


@pytest.mark.parametrize("field", ["startTime", "endTime", "traceId", "id"])
def test_a_row_without_a_required_field_is_reported(tmp_path: Path, field: str) -> None:
    row = langfuse_row(S1)
    del row[field]
    _, issues = load(tmp_path, [row])
    assert kinds(issues) == [IssueKind.INVALID_LANGFUSE_ROW]


@pytest.mark.parametrize(
    "text",
    [
        "yesterday",
        "2026-09-30",
        "2026-13-01T00:00:00Z",
        "2026-02-30T00:00:00Z",
        "2026-09-30T24:00:00Z",
        "2026-09-30T08:00:60Z",
        "2026-09-30T08:00:00+24:00",
        "2026-09-30T08:00:00+02",
        "2026-09-30T08:00:00.Z",
        "2026-09-30T08:00:00 Z",
        "\uff12\uff10\uff12\uff16-09-30T08:00:00Z",  # fullwidth digits
        "",
        1790755200,
        None,
    ],
)
def test_an_unreadable_start_time_is_reported(tmp_path: Path, text: object) -> None:
    _, issues = load(tmp_path, [langfuse_row(S1) | {"startTime": text}])
    assert kinds(issues) == [IssueKind.INVALID_LANGFUSE_ROW]


@pytest.mark.parametrize(
    "text", ["1969-12-31T23:59:59Z", "0001-01-01T00:00:00Z", "2600-01-01T00:00:00Z"]
)
def test_a_start_time_out_of_range_is_reported(tmp_path: Path, text: str) -> None:
    _, issues = load(tmp_path, [langfuse_row(S1, start=text, end="2026-09-30T09:00:00Z")])
    assert issues[0].detail == "line 1: [0]: start time is out of range"


def test_an_end_time_before_the_start_time_is_reported(tmp_path: Path) -> None:
    row = langfuse_row(S1, start="2026-09-30T08:00:01Z", end="2026-09-30T08:00:00Z")
    _, issues = load(tmp_path, [row])
    assert issues == [
        Issue(
            IssueKind.INVALID_LANGFUSE_ROW,
            "rows.json",
            "line 1: [0]: end time is before start time",
        )
    ]


@pytest.mark.parametrize("value", ["", "a" * 201, 123, None, ["x"]])
@pytest.mark.parametrize("field", ["id", "traceId"])
def test_an_unusable_id_is_reported(tmp_path: Path, field: str, value: object) -> None:
    _, issues = load(tmp_path, [langfuse_row(S1) | {field: value}])
    assert kinds(issues) == [IssueKind.INVALID_LANGFUSE_ROW]


@pytest.mark.parametrize("value", ["a" * 201, 123, ["x"]])
def test_an_unusable_parent_id_is_reported(tmp_path: Path, value: object) -> None:
    _, issues = load(tmp_path, [langfuse_row(S2) | {"parentObservationId": value}])
    assert kinds(issues) == [IssueKind.INVALID_LANGFUSE_ROW]


def test_an_id_of_200_characters_is_kept(tmp_path: Path) -> None:
    assert only_span(tmp_path, langfuse_row("a" * 200)).span_id == "a" * 200


@pytest.mark.parametrize("field", ["type", "level", "name"])
def test_a_field_that_is_not_a_string_is_reported(tmp_path: Path, field: str) -> None:
    _, issues = load(tmp_path, [langfuse_row(S1) | {field: 5}])
    assert kinds(issues) == [IssueKind.INVALID_ATTRIBUTE]


def test_a_level_that_is_not_a_string_is_not_an_error(tmp_path: Path) -> None:
    [span], _ = load(tmp_path, [langfuse_row(S1) | {"level": ["ERROR"]}])
    assert span.is_error is False


def test_a_legacy_trace_object_is_reported(tmp_path: Path) -> None:
    trace = {"id": TRACE_ID, "name": "t", "observations": [langfuse_row(S1)]}
    _, issues = load(tmp_path, {"data": [trace], "meta": {}})
    assert kinds(issues) == [IssueKind.LEGACY_LANGFUSE_TRACE]


def test_a_legacy_trace_object_gives_no_spans(tmp_path: Path) -> None:
    trace = {"id": TRACE_ID, "observations": [langfuse_row(S1)]}
    assert load_lines(tmp_path, [trace])[0] == []


def test_identical_rows_are_merged(tmp_path: Path) -> None:
    spans, _ = load_lines(tmp_path, [langfuse_row(S1), langfuse_row(S1)])
    assert len(spans) == 1


def test_identical_rows_are_counted_as_duplicates(tmp_path: Path) -> None:
    _, issues = load_lines(tmp_path, [langfuse_row(S1), langfuse_row(S1)])
    assert kinds(issues) == [IssueKind.DUPLICATE_SPAN]


def test_rows_that_differ_only_in_fields_not_read_are_duplicates(tmp_path: Path) -> None:
    rows = [langfuse_row(S1, updatedAt="2026-09-30T09:00:00Z"), langfuse_row(S1, updatedAt="x")]
    _, issues = load_lines(tmp_path, rows)
    assert kinds(issues) == [IssueKind.DUPLICATE_SPAN]


def test_rows_with_the_same_id_and_different_content_conflict(tmp_path: Path) -> None:
    _, issues = load_lines(tmp_path, [langfuse_row(S1), langfuse_row(S1, level="ERROR")])
    assert kinds(issues) == [IssueKind.CONFLICTING_DUPLICATE_SPAN]


def test_a_one_megabyte_case_id_is_shortened(tmp_path: Path) -> None:
    row = langfuse_row(S1, type="AGENT", attributes={"detecttrace.case_id": "x" * (1 << 20)})
    [case] = cases_of(load(tmp_path, [row])[0]).values()
    assert len(case.case_id) == 200


def test_a_one_megabyte_tool_name_is_shortened(tmp_path: Path) -> None:
    rows = [
        langfuse_row(S1, type="AGENT", attributes={"detecttrace.case_id": "DT-1"}),
        langfuse_row(S2, S1, type="TOOL", name="t" * (1 << 20)),
    ]
    assert len(cases_of(load(tmp_path, rows)[0])["DT-1"].tool_calls[0].tool_name) == 200


def test_json_nested_too_deep_to_parse_is_reported(tmp_path: Path) -> None:
    path = tmp_path / "rows.jsonl"
    path.write_text("[" * 100_000 + "]" * 100_000 + "\n", encoding="utf-8")
    _, issues = load_spans(path, format="langfuse")
    assert kinds(issues) == [IssueKind.INVALID_LINE]


def test_deeply_nested_metadata_values_in_duplicate_rows_are_compared(tmp_path: Path) -> None:
    deep = json.loads("[" * 400 + "]" * 400)
    rows = [
        langfuse_row(S1, attributes={"deep": deep}),
        langfuse_row(S1, attributes={"deep": deep}),
    ]
    _, issues = load_lines(tmp_path, rows)
    assert kinds(issues) == [IssueKind.DUPLICATE_SPAN]


def test_rows_without_input_or_output_fields_are_reported(tmp_path: Path) -> None:
    row = langfuse_row(S1)
    del row["input"], row["output"]
    _, issues = load(tmp_path, [row])
    assert kinds(issues) == [IssueKind.LANGFUSE_WITHOUT_IO]


@pytest.mark.parametrize("field", ["input", "output"])
def test_a_row_with_one_of_input_or_output_is_not_reported(tmp_path: Path, field: str) -> None:
    row = langfuse_row(S1)
    del row[field]
    assert load(tmp_path, [row])[1] == []


def test_duplicate_rows_without_input_or_output_are_reported_once(tmp_path: Path) -> None:
    row = langfuse_row(S1)
    del row["input"], row["output"]
    _, issues = load_lines(tmp_path, [row, row])
    assert kinds(issues) == [IssueKind.DUPLICATE_SPAN, IssueKind.LANGFUSE_WITHOUT_IO]


def test_a_row_without_input_or_output_among_rows_with_them_is_not_reported(
    tmp_path: Path,
) -> None:
    row = langfuse_row(S2)
    del row["input"], row["output"]
    assert load(tmp_path, [langfuse_row(S1), row])[1] == []


def test_a_folder_notes_only_the_file_whose_rows_lack_input_and_output(tmp_path: Path) -> None:
    without = langfuse_row(S2)
    del without["input"], without["output"]
    (tmp_path / "week1").mkdir()
    (tmp_path / "week2").mkdir()
    write_jsonl(tmp_path / "week1" / "rows.jsonl", [langfuse_row(S1)])
    write_jsonl(tmp_path / "week2" / "rows.jsonl", [without])
    assert load_spans(tmp_path, format="langfuse")[1] == [
        Issue(IssueKind.LANGFUSE_WITHOUT_IO, "week2/rows.jsonl", "no row has input or output")
    ]


def test_a_file_whose_rows_all_lack_input_and_output_gets_one_note(tmp_path: Path) -> None:
    rows = without_io([langfuse_row(S1), langfuse_row(S2, S1)])
    assert load_lines(tmp_path, rows)[1] == [
        Issue(IssueKind.LANGFUSE_WITHOUT_IO, "rows.jsonl", "no row has input or output")
    ]


def test_rows_with_null_input_and_output_are_not_reported(tmp_path: Path) -> None:
    assert load(tmp_path, [langfuse_row(S1, input=None, output=None)])[1] == []


# The real export (Langfuse v4.48.0)


@pytest.fixture
def api_spans(tmp_path: Path) -> list[Span]:
    spans, _ = load_spans(copy_files(tmp_path, API_PAGES), format="langfuse")
    return spans


def test_real_api_pages_give_every_row(api_spans: list[Span]) -> None:
    assert len(api_spans) == 16


def test_real_api_pages_are_read_without_issues(tmp_path: Path) -> None:
    assert load_spans(copy_files(tmp_path, API_PAGES), format="langfuse")[1] == []


def test_real_api_pages_give_the_four_cases(api_spans: list[Span]) -> None:
    assert sorted(cases_of(api_spans)) == ["CASE-9001", "CASE-9002", "CASE-9003", "CASE-9004"]


def test_real_api_pages_build_cases_without_issues(api_spans: list[Span]) -> None:
    assert build_trace_cases(api_spans, MappingConfig())[1] == []


def test_real_parent_links_attach_a_sub_agent_tool_to_its_case(api_spans: list[Span]) -> None:
    calls = cases_of(api_spans)["CASE-9003"].tool_calls
    assert [call.tool_name for call in calls] == ["get_signin_logs", "lookup_ip_reputation"]


def test_real_failed_tool_call_is_marked_failed(api_spans: list[Span]) -> None:
    calls = cases_of(api_spans)["CASE-9002"].tool_calls
    assert [(call.tool_name, call.is_failed) for call in calls] == [
        ("get_oauth_grants", True),
        ("get_user_profile", False),
    ]


def test_real_tool_arguments_come_from_the_input(api_spans: list[Span]) -> None:
    calls = cases_of(api_spans)["CASE-9002"].tool_calls
    assert calls[1].arguments == '{"user": "user2@example.com"}'


def test_real_case_fields_come_from_detecttrace_attributes(api_spans: list[Span]) -> None:
    case = cases_of(api_spans)["CASE-9002"]
    assert (case.alert_class, case.agent_label, case.prompt_version) == (
        "oauth_consent",
        "BenignPositive",
        "v2",
    )


def test_real_case_without_a_prompt_version_has_none(api_spans: list[Span]) -> None:
    assert cases_of(api_spans)["CASE-9003"].prompt_version is None


def test_real_typed_attributes_keep_their_types(api_spans: list[Span]) -> None:
    [span] = [span for span in api_spans if "detecttrace.risk_score" in span.attributes]
    assert (
        span.attributes["detecttrace.risk_score"],
        span.attributes["detecttrace.mfa_satisfied"],
        span.attributes["detecttrace.source_ips"],
    ) == (0.87, False, ["203.0.113.45", "198.51.100.23"])


def test_real_resource_attributes_are_read(api_spans: list[Span]) -> None:
    assert {span.resource_attributes["service.name"] for span in api_spans} == {"soc-agent"}


def test_real_trace_metadata_is_named_under_langfuse_metadata(api_spans: list[Span]) -> None:
    [root] = [span for span in api_spans if "langfuse.metadata.case_id" in span.attributes]
    assert root.attributes["langfuse.metadata.case_id"] == "CASE-9004"


def test_real_service_version_is_read_as_the_langfuse_version(api_spans: list[Span]) -> None:
    assert {span.attributes["langfuse.version"] for span in api_spans} == {"0.9.0"}


def detecttrace_fields(span: Span) -> tuple[object, ...]:
    """The span fields the default mapping reads, with attribute values as text.

    The blob export writes every metadata value as a string, so typed API values are
    compared by their text; case and tool fields are strings in both exports.
    """
    keys = (
        "gen_ai.operation.name",
        "detecttrace.case_id",
        "detecttrace.alert_class",
        "detecttrace.verdict",
        "detecttrace.prompt_version",
        "gen_ai.tool.name",
        "gen_ai.tool.call.arguments",
        "error.type",
    )
    return (
        span.trace_id,
        span.span_id,
        span.parent_span_id,
        span.name,
        span.start_ns,
        span.end_ns,
        span.is_error,
        tuple(str(span.attributes.get(key)) for key in keys),
    )


@pytest.mark.parametrize("name", ["blob_observations_v2.json", "blob_observations_v2.jsonl"])
def test_real_blob_export_matches_the_api_pages_in_the_fields_detecttrace_reads(
    api_spans: list[Span], name: str
) -> None:
    spans, _ = load_spans(REAL / name, format="langfuse")
    assert sorted(map(detecttrace_fields, spans)) == sorted(map(detecttrace_fields, api_spans))


def test_real_blob_json_and_jsonl_give_identical_spans() -> None:
    from_json, _ = load_spans(REAL / "blob_observations_v2.json", format="langfuse")
    from_jsonl, _ = load_spans(REAL / "blob_observations_v2.jsonl", format="langfuse")
    assert from_json == from_jsonl


def test_real_blob_export_keeps_metadata_values_as_strings() -> None:
    spans, _ = load_spans(REAL / "blob_observations_v2.json", format="langfuse")
    [span] = [span for span in spans if "detecttrace.risk_score" in span.attributes]
    assert span.attributes["detecttrace.risk_score"] == "0.87"


@pytest.mark.parametrize("name", ["blob_observations_v2.json", "blob_observations_v2.jsonl"])
def test_real_blob_export_is_read_without_issues(name: str) -> None:
    assert load_spans(REAL / name, format="langfuse")[1] == []


def test_real_export_without_the_io_group_is_reported_once_per_page(tmp_path: Path) -> None:
    _, issues = load_spans(copy_files(tmp_path, NO_IO_PAGES), format="langfuse")
    assert [(issue.kind, issue.subject) for issue in issues] == [
        (IssueKind.LANGFUSE_WITHOUT_IO, name) for name in NO_IO_PAGES
    ]


def test_real_export_with_one_page_without_the_io_group_notes_that_page(tmp_path: Path) -> None:
    folder = copy_files(tmp_path, [*API_PAGES[:3], NO_IO_PAGES[3]])
    assert load_spans(folder, format="langfuse")[1] == [
        Issue(IssueKind.LANGFUSE_WITHOUT_IO, NO_IO_PAGES[3], "no row has input or output")
    ]


def test_real_export_without_the_io_group_has_no_tool_arguments(tmp_path: Path) -> None:
    spans, _ = load_spans(copy_files(tmp_path, NO_IO_PAGES), format="langfuse")
    calls = [call for case in cases_of(spans).values() for call in case.tool_calls]
    assert [call.arguments for call in calls] == [None] * 7


def test_a_saved_error_response_is_reported_as_a_bad_row() -> None:
    _, issues = load_spans(REAL / "legacy_traces_404.json", format="langfuse")
    assert kinds(issues) == [IssueKind.INVALID_LANGFUSE_ROW]


REAL_VERDICTS = """\
case_id,alert_class,verdict
CASE-9001,impossible_travel,TruePositive
CASE-9002,oauth_consent,BenignPositive
CASE-9003,impossible_travel,FalsePositive
CASE-9004,oauth_consent,Closed - Benign
"""
REAL_CONFIG = """\
traces: {path: traces, format: langfuse}
verdicts: {path: verdicts.csv}
checklists: checklists
output: dashboard.html
label_map:
  TruePositive: true_positive
  FalsePositive: false_positive
  BenignPositive: benign
  Closed - Benign: benign
"""
REAL_CHECKLIST = """\
alert_class: impossible_travel
items:
  - {id: signins, tool: get_signin_logs, args: {range: {min_duration: 24h}}}
  - {id: profile, tool: get_user_profile}
"""


@pytest.fixture
def real_run(tmp_path: Path) -> Any:
    shutil.copytree(copy_files(tmp_path, API_PAGES), tmp_path / "traces")
    (tmp_path / "verdicts.csv").write_text(REAL_VERDICTS, encoding="utf-8")
    (tmp_path / "checklists").mkdir()
    (tmp_path / "checklists" / "impossible_travel.yaml").write_text(
        REAL_CHECKLIST, encoding="utf-8"
    )
    config_path = tmp_path / "detecttrace.yaml"
    config_path.write_text(REAL_CONFIG, encoding="utf-8")
    return run_check(load_run_config(config_path), config_path)


def test_real_export_run_scores_the_four_cases(real_run: Any) -> None:
    assert real_run.case_count == 4


def test_real_export_run_shows_the_failed_call_with_its_arguments(real_run: Any) -> None:
    [detail] = [row for row in real_run.results["case_detail"] if row["case_id"] == "CASE-9002"]
    assert detail["calls"][0] == {
        "tool": "get_oauth_grants",
        "status": "failed",
        "duration_ms": 2000.0,
        "arguments": '{"user": "user2@example.com", "range": "7d"}',
    }


def test_real_export_run_checks_arguments_read_from_the_input(real_run: Any) -> None:
    [detail] = [row for row in real_run.results["case_detail"] if row["case_id"] == "CASE-9003"]
    assert [outcome["status"] for outcome in detail["outcomes"]] == ["satisfied", "missed"]


# Each Langfuse reason code reaches the terminal summary and the dashboard's data notes


def run_rows() -> list[dict[str, Any]]:
    return [
        langfuse_row(
            S1,
            type="AGENT",
            name="invoke_agent triage",
            attributes={
                "detecttrace.case_id": "DT-1",
                "detecttrace.alert_class": "impossible_travel",
                "detecttrace.verdict": "TP",
            },
        ),
        langfuse_row(S2, S1, type="TOOL", name="get_signin_logs", input='{"range": "24h"}'),
    ]


def without_io(rows: list[dict[str, Any]]) -> list[Any]:
    return [
        {key: value for key, value in row.items() if key not in ("input", "output")} for row in rows
    ]


PROBLEM_ROWS = {
    IssueKind.INVALID_LANGFUSE_ROW: lambda rows: [*rows, {"traceId": TRACE_ID}],
    IssueKind.INVALID_LANGFUSE_DOCUMENT: lambda rows: [*rows, {"data": 5}],
    IssueKind.LANGFUSE_METADATA_NOT_OBJECT: lambda rows: [*rows, langfuse_row(S3, metadata="x")],
    IssueKind.LEGACY_LANGFUSE_TRACE: lambda rows: [*rows, {"id": "t", "observations": []}],
    IssueKind.LANGFUSE_WITHOUT_IO: without_io,
    IssueKind.LANGFUSE_NO_TOOL_CALLS: lambda rows: rows[:1],
}


def write_langfuse_run(folder: Path, rows: list[Any]) -> Path:
    (folder / "traces").mkdir()
    write_jsonl(folder / "traces" / "rows.jsonl", rows)
    (folder / "verdicts.csv").write_text(
        "case_id,alert_class,verdict\nDT-1,impossible_travel,TP\n", encoding="utf-8"
    )
    config_path = folder / "detecttrace.yaml"
    config_path.write_text(
        "traces: {path: traces, format: langfuse}\nverdicts: {path: verdicts.csv}\n"
        "output: dashboard.html\nlabel_map: {TP: true_positive}\n",
        encoding="utf-8",
    )
    return config_path


def hint_of(kind: IssueKind) -> str:
    [line] = summarize_issues([Issue(kind, "x")])
    return line.hint


def test_a_readable_langfuse_run_gives_no_notes(tmp_path: Path) -> None:
    config_path = write_langfuse_run(tmp_path, run_rows())
    assert run_check(load_run_config(config_path), config_path).issues == []


@pytest.mark.parametrize("kind", list(PROBLEM_ROWS))
def test_each_langfuse_problem_is_in_the_terminal_summary(tmp_path: Path, kind: IssueKind) -> None:
    config_path = write_langfuse_run(tmp_path, PROBLEM_ROWS[kind](run_rows()))
    result = CliRunner().invoke(cli.app, ["check", "--config", str(config_path)])
    assert hint_of(kind) in result.output


@pytest.mark.parametrize("kind", list(PROBLEM_ROWS))
def test_each_langfuse_problem_is_in_the_dashboard_data_notes(
    tmp_path: Path, kind: IssueKind
) -> None:
    config_path = write_langfuse_run(tmp_path, PROBLEM_ROWS[kind](run_rows()))
    CliRunner().invoke(cli.app, ["check", "--config", str(config_path)])
    page = parse_html((tmp_path / "dashboard.html").read_text(encoding="utf-8"))
    notes = page.find(has_tag("ol", **{"class": "notes"}))
    assert hint_of(kind) in notes.text()


def test_an_otlp_input_without_tool_calls_gets_no_langfuse_note(tmp_path: Path) -> None:
    config_path = write_run_folder(tmp_path)
    write_jsonl(
        tmp_path / "traces" / "batch.jsonl",
        [
            otlp_document(
                [
                    otlp_span(
                        S1,
                        attributes={
                            "gen_ai.operation.name": "invoke_agent",
                            "detecttrace.case_id": "DT-1",
                        },
                    )
                ]
            )
        ],
    )
    run = run_check(load_run_config(config_path), config_path)
    assert IssueKind.LANGFUSE_NO_TOOL_CALLS not in kinds(run.issues)


# Agent runs without tool calls


def trace_case(tool_calls: tuple[ToolCall, ...] = ()) -> TraceCase:
    return TraceCase("DT-1", TRACE_ID, S1, 0, 1, None, None, None, tool_calls, False)


def test_langfuse_cases_without_tool_calls_give_one_issue() -> None:
    assert find_missing_tool_calls("langfuse", [trace_case(), trace_case()], "traces") == Issue(
        IssueKind.LANGFUSE_NO_TOOL_CALLS, "traces", "2 agent run(s), no tool calls"
    )


def test_langfuse_cases_with_a_tool_call_give_no_issue() -> None:
    call = ToolCall(S2, "scan", None, 0, 1, False)
    assert find_missing_tool_calls("langfuse", [trace_case(), trace_case((call,))], "x") is None


def test_langfuse_input_without_cases_gives_no_issue() -> None:
    assert find_missing_tool_calls("langfuse", [], "traces") is None


def test_otlp_cases_without_tool_calls_give_no_issue() -> None:
    assert find_missing_tool_calls("otlp_jsonl", [trace_case()], "traces") is None
