import gzip
import json
from pathlib import Path

import pytest
from builders import (
    TRACE_ID,
    otlp_document,
    otlp_span,
    span_hex,
    write_gzip_jsonl,
    write_jsonl,
)

from detecttrace.model import IssueKind, Span
from detecttrace.otlp import load_spans

S1 = span_hex(1)
S2 = span_hex(2)
BOM = "﻿"


def _line(span_id: str) -> str:
    return json.dumps(otlp_document([otlp_span(span_id)])) + "\n"


def test_reads_span_from_json_lines(tmp_path: Path) -> None:
    doc = otlp_document(
        [
            otlp_span(
                "B7AD6B7169203331",
                "00F067AA0BA902B7",
                name="execute_tool x",
                start_ns=1_758_000_000_123_456_789,
                end_ns=1_758_000_001_000_000_000,
                attributes={"gen_ai.tool.name": "x"},
            )
        ],
        resource={"service.name": "agent"},
    )
    path = write_jsonl(tmp_path / "traces.jsonl", [doc])

    spans, _ = load_spans(path)

    assert spans == [
        Span(
            trace_id=TRACE_ID,
            span_id="b7ad6b7169203331",
            parent_span_id="00f067aa0ba902b7",
            name="execute_tool x",
            start_ns=1_758_000_000_123_456_789,
            end_ns=1_758_000_001_000_000_000,
            is_error=False,
            attributes={"gen_ai.tool.name": "x"},
            resource_attributes={"service.name": "agent"},
        )
    ]


def test_uppercase_trace_id_is_lowercased(tmp_path: Path) -> None:
    path = write_jsonl(
        tmp_path / "t.jsonl", [otlp_document([otlp_span(S1, trace_id=TRACE_ID.upper())])]
    )

    spans, _ = load_spans(path)

    assert spans[0].trace_id == TRACE_ID


@pytest.mark.parametrize(
    ("value", "expected"),
    [("text", "text"), (3, 3), (2.5, 2.5), (True, True), (["a", 1], ["a", 1])],
)
def test_decodes_attribute_value_types(tmp_path: Path, value: object, expected: object) -> None:
    path = write_jsonl(
        tmp_path / "t.jsonl", [otlp_document([otlp_span(S1, attributes={"k": value})])]
    )

    spans, _ = load_spans(path)

    assert spans[0].attributes["k"] == expected


def test_decodes_kvlist_value_as_dict(tmp_path: Path) -> None:
    span = otlp_span(S1)
    span["attributes"] = [
        {
            "key": "k",
            "value": {"kvlistValue": {"values": [{"key": "x", "value": {"stringValue": "y"}}]}},
        }
    ]
    path = write_jsonl(tmp_path / "t.jsonl", [otlp_document([span])])

    spans, _ = load_spans(path)

    assert spans[0].attributes["k"] == {"x": "y"}


def test_empty_parent_span_id_means_no_parent(tmp_path: Path) -> None:
    path = write_jsonl(tmp_path / "t.jsonl", [otlp_document([otlp_span(S1, "")])])

    spans, _ = load_spans(path)

    assert spans[0].parent_span_id is None


@pytest.mark.parametrize("status", [{"code": 2}, {"code": "STATUS_CODE_ERROR"}])
def test_error_status_marks_span_as_error(tmp_path: Path, status: dict[str, object]) -> None:
    path = write_jsonl(tmp_path / "t.jsonl", [otlp_document([otlp_span(S1, status=status)])])

    spans, _ = load_spans(path)

    assert spans[0].is_error is True


def test_missing_end_time_means_end_equals_start(tmp_path: Path) -> None:
    span = otlp_span(S1, start_ns=5_000)
    del span["endTimeUnixNano"]
    path = write_jsonl(tmp_path / "t.jsonl", [otlp_document([span])])

    spans, _ = load_spans(path)

    assert spans[0].end_ns == 5_000


def test_resource_attributes_stay_with_their_own_resource(tmp_path: Path) -> None:
    first = otlp_document([otlp_span(S1)], resource={"service.name": "a"})
    second = otlp_document([otlp_span(S2)], resource={"service.name": "b"})
    doc = {"resourceSpans": first["resourceSpans"] + second["resourceSpans"]}
    path = write_jsonl(tmp_path / "t.jsonl", [doc])

    spans, _ = load_spans(path)

    assert [s.resource_attributes["service.name"] for s in spans] == ["a", "b"]


def test_reads_single_pretty_printed_document(tmp_path: Path) -> None:
    path = tmp_path / "trace.json"
    path.write_text(json.dumps(otlp_document([otlp_span(S1), otlp_span(S2)]), indent=2))

    spans, _ = load_spans(path)

    assert [s.span_id for s in spans] == [S1, S2]


@pytest.mark.parametrize("indent", [None, 2])
def test_reads_file_starting_with_bom(tmp_path: Path, indent: int | None) -> None:
    path = tmp_path / "t.json"
    path.write_text(BOM + json.dumps(otlp_document([otlp_span(S1)]), indent=indent) + "\n")

    spans, _ = load_spans(path)

    assert [s.span_id for s in spans] == [S1]


def test_document_that_is_not_json_is_reported(tmp_path: Path) -> None:
    path = tmp_path / "t.json"
    path.write_text("{\n garbage\n")

    _, issues = load_spans(path)

    assert [i.kind for i in issues] == [IssueKind.INVALID_FILE]


def test_reads_gzip_file(tmp_path: Path) -> None:
    path = write_gzip_jsonl(tmp_path / "traces.jsonl.gz", [otlp_document([otlp_span(S1)])])

    spans, _ = load_spans(path)

    assert [s.span_id for s in spans] == [S1]


def test_detects_gzip_without_gz_name(tmp_path: Path) -> None:
    path = write_gzip_jsonl(tmp_path / "traces.jsonl", [otlp_document([otlp_span(S1)])])

    spans, _ = load_spans(path)

    assert [s.span_id for s in spans] == [S1]


def test_reads_one_trace_split_across_rotated_files(tmp_path: Path) -> None:
    write_jsonl(tmp_path / "traces-1.jsonl", [otlp_document([otlp_span(S1)])])
    write_jsonl(tmp_path / "traces-2.jsonl", [otlp_document([otlp_span(S2, S1)])])

    spans, _ = load_spans(tmp_path)

    assert sorted(s.span_id for s in spans) == [S1, S2]


def test_first_copy_of_duplicate_span_wins_in_sorted_file_order(tmp_path: Path) -> None:
    write_jsonl(tmp_path / "b.jsonl", [otlp_document([otlp_span(S1, name="second")])])
    write_jsonl(tmp_path / "a.jsonl", [otlp_document([otlp_span(S1, name="first")])])

    spans, _ = load_spans(tmp_path)

    assert [s.name for s in spans] == ["first"]


def test_files_are_read_in_posix_relative_path_order(tmp_path: Path) -> None:
    # "/" sorts before "0" in POSIX form; a Windows "\\" would sort after it.
    (tmp_path / "a").mkdir()
    write_jsonl(tmp_path / "a" / "x.jsonl", [otlp_document([otlp_span(S1, name="first")])])
    write_jsonl(tmp_path / "a0.jsonl", [otlp_document([otlp_span(S1, name="second")])])

    spans, _ = load_spans(tmp_path)

    assert [s.name for s in spans] == ["first"]


def test_files_in_hidden_folders_are_skipped(tmp_path: Path) -> None:
    (tmp_path / ".cache").mkdir()
    write_jsonl(tmp_path / ".cache" / "old.jsonl", [otlp_document([otlp_span(S1)])])
    write_jsonl(tmp_path / "t.jsonl", [otlp_document([otlp_span(S2)])])

    spans, _ = load_spans(tmp_path)

    assert [s.span_id for s in spans] == [S2]


def test_issue_subject_is_posix_path_relative_to_folder(tmp_path: Path) -> None:
    (tmp_path / "a").mkdir()
    (tmp_path / "a" / "empty.jsonl").write_text("")

    _, issues = load_spans(tmp_path)

    assert [i.subject for i in issues] == ["a/empty.jsonl"]


def test_issue_subject_for_single_file_is_its_name(tmp_path: Path) -> None:
    path = tmp_path / "empty.jsonl"
    path.write_text("")

    _, issues = load_spans(path)

    assert [i.subject for i in issues] == ["empty.jsonl"]


def test_identical_duplicate_span_is_reported_as_duplicate(tmp_path: Path) -> None:
    write_jsonl(tmp_path / "a.jsonl", [otlp_document([otlp_span(S1)])])
    write_jsonl(tmp_path / "b.jsonl", [otlp_document([otlp_span(S1)])])

    _, issues = load_spans(tmp_path)

    assert [i.kind for i in issues] == [IssueKind.DUPLICATE_SPAN]


def test_differing_duplicate_span_is_reported_as_conflicting(tmp_path: Path) -> None:
    write_jsonl(tmp_path / "a.jsonl", [otlp_document([otlp_span(S1, name="first")])])
    write_jsonl(tmp_path / "b.jsonl", [otlp_document([otlp_span(S1, name="second")])])

    _, issues = load_spans(tmp_path)

    assert [i.kind for i in issues] == [IssueKind.CONFLICTING_DUPLICATE_SPAN]


def test_empty_file_is_reported(tmp_path: Path) -> None:
    path = tmp_path / "empty.jsonl"
    path.write_text("")

    _, issues = load_spans(path)

    assert [i.kind for i in issues] == [IssueKind.EMPTY_FILE]


def test_truncated_last_line_keeps_earlier_lines(tmp_path: Path) -> None:
    path = write_jsonl(tmp_path / "t.jsonl", [otlp_document([otlp_span(S1)])])
    with path.open("a") as handle:
        handle.write('{"resourceSpans": [{"scopeSp')

    spans, _ = load_spans(path)

    assert [s.span_id for s in spans] == [S1]


def test_truncated_last_line_is_reported(tmp_path: Path) -> None:
    path = write_jsonl(tmp_path / "t.jsonl", [otlp_document([otlp_span(S1)])])
    with path.open("a") as handle:
        handle.write('{"resourceSpans": [{"scopeSp')

    _, issues = load_spans(path)

    assert [i.kind for i in issues] == [IssueKind.TRUNCATED_LINE]


def test_single_unfinished_line_is_reported_as_truncated(tmp_path: Path) -> None:
    path = tmp_path / "t.jsonl"
    path.write_text('{"resourceSpans": [{"scopeSp')

    _, issues = load_spans(path)

    assert [i.kind for i in issues] == [IssueKind.TRUNCATED_LINE]


def test_invalid_middle_line_is_reported(tmp_path: Path) -> None:
    path = tmp_path / "t.jsonl"
    path.write_text(f"{_line(S1)}not json\n{_line(S2)}")

    _, issues = load_spans(path)

    assert [i.kind for i in issues] == [IssueKind.INVALID_LINE]


def test_invalid_line_detail_names_line_number(tmp_path: Path) -> None:
    path = tmp_path / "t.jsonl"
    path.write_text(f"{_line(S1)}not json\n{_line(S2)}")

    _, issues = load_spans(path)

    assert [i.detail for i in issues] == ["line 2"]


def test_invalid_first_line_is_reported_as_invalid_line(tmp_path: Path) -> None:
    path = tmp_path / "t.jsonl"
    path.write_text(f"garbage\n{_line(S1)}")

    _, issues = load_spans(path)

    assert [i.kind for i in issues] == [IssueKind.INVALID_LINE]


def test_invalid_first_line_keeps_later_lines(tmp_path: Path) -> None:
    path = tmp_path / "t.jsonl"
    path.write_text(f"garbage\n{_line(S1)}{_line(S2)}")

    spans, _ = load_spans(path)

    assert [s.span_id for s in spans] == [S1, S2]


def test_line_that_is_not_utf8_is_reported(tmp_path: Path) -> None:
    path = tmp_path / "t.jsonl"
    path.write_bytes(_line(S1).encode() + b"\xe9\n" + _line(S2).encode())

    _, issues = load_spans(path)

    assert [i.kind for i in issues] == [IssueKind.INVALID_LINE]


def test_line_that_is_not_utf8_keeps_other_lines(tmp_path: Path) -> None:
    path = tmp_path / "t.jsonl"
    path.write_bytes(_line(S1).encode() + b"\xe9\n" + _line(S2).encode())

    spans, _ = load_spans(path)

    assert [s.span_id for s in spans] == [S1, S2]


def test_deeply_nested_line_is_reported(tmp_path: Path) -> None:
    path = tmp_path / "t.jsonl"
    path.write_text(_line(S1) + "[" * 100_000 + "]" * 100_000 + "\n")

    _, issues = load_spans(path)

    assert [i.kind for i in issues] == [IssueKind.INVALID_LINE]


def test_deeply_nested_document_is_reported(tmp_path: Path) -> None:
    path = tmp_path / "t.json"
    path.write_text("[\n" + "[" * 100_000 + "]" * 100_000 + "\n]\n")

    _, issues = load_spans(path)

    assert [i.kind for i in issues] == [IssueKind.INVALID_FILE]


def test_truncated_gzip_file_is_reported(tmp_path: Path) -> None:
    data = gzip.compress(_line(S1).encode())
    path = tmp_path / "t.jsonl.gz"
    path.write_bytes(data[:-12])

    _, issues = load_spans(path)

    assert [(i.kind, i.detail) for i in issues] == [
        (IssueKind.TRUNCATED_FILE, "compressed file ends early; earlier data was read")
    ]


def _corrupt_gzip() -> bytes:
    # Two gzip members: the first is intact, the second has a valid header but broken data.
    broken = bytearray(gzip.compress(_line(S2).encode() * 50))
    broken[20:30] = b"\xff" * 10
    return gzip.compress(_line(S1).encode()) + bytes(broken)


def test_corrupt_gzip_data_is_reported(tmp_path: Path) -> None:
    path = tmp_path / "t.jsonl.gz"
    path.write_bytes(_corrupt_gzip())

    _, issues = load_spans(path)

    assert [(i.kind, i.detail) for i in issues] == [
        (IssueKind.INVALID_FILE, "corrupt compressed data; earlier data was read")
    ]


def test_corrupt_gzip_data_keeps_earlier_spans(tmp_path: Path) -> None:
    path = tmp_path / "t.jsonl.gz"
    path.write_bytes(_corrupt_gzip())

    spans, _ = load_spans(path)

    assert [s.span_id for s in spans] == [S1]


def test_document_without_resource_spans_is_reported(tmp_path: Path) -> None:
    path = write_jsonl(tmp_path / "t.jsonl", [{"name": "not otlp"}])

    _, issues = load_spans(path)

    assert [i.kind for i in issues] == [IssueKind.INVALID_FILE]


def _span_line(**overrides: object) -> str:
    return json.dumps(otlp_document([otlp_span(S1) | overrides]))


def _attribute_line(value: object) -> str:
    return _span_line(attributes=[{"key": "k", "value": value}])


@pytest.mark.parametrize(
    ("line", "expected"),
    [
        ('{"resourceSpans": [1]}', IssueKind.INVALID_FILE),
        ('{"resourceSpans": [{"resource": [1], "scopeSpans": []}]}', IssueKind.INVALID_FILE),
        ('{"resourceSpans": [{"scopeSpans": {"a": 1}}]}', IssueKind.INVALID_FILE),
        ('{"resourceSpans": [{"scopeSpans": [1]}]}', IssueKind.INVALID_FILE),
        ('{"resourceSpans": [{"scopeSpans": [{"spans": {"a": 1}}]}]}', IssueKind.INVALID_FILE),
        ('{"resourceSpans": [{"scopeSpans": [{"spans": [1]}]}]}', IssueKind.INVALID_SPAN),
        (
            '{"resourceSpans": [{"resource": {"attributes": [1]}, "scopeSpans": []}]}',
            IssueKind.INVALID_ATTRIBUTE,
        ),
        (_span_line(attributes=[1]), IssueKind.INVALID_ATTRIBUTE),
        (_span_line(attributes={"k": 1}), IssueKind.INVALID_ATTRIBUTE),
        (_span_line(attributes=[{"key": [1], "value": {}}]), IssueKind.INVALID_ATTRIBUTE),
        (_attribute_line({"intValue": "x"}), IssueKind.INVALID_ATTRIBUTE),
        (_attribute_line({"doubleValue": "x"}), IssueKind.INVALID_ATTRIBUTE),
        (_attribute_line({"arrayValue": [1]}), IssueKind.INVALID_ATTRIBUTE),
        (_attribute_line("s"), IssueKind.INVALID_ATTRIBUTE),
        (_span_line(status="ERR"), IssueKind.INVALID_SPAN),
        (_span_line(startTimeUnixNano="1000").replace('"1000"', "1e400"), IssueKind.INVALID_SPAN),
    ],
    ids=[
        "resource_spans_item",
        "resource",
        "scope_spans_object",
        "scope_spans_item",
        "spans_object",
        "span_item",
        "resource_attribute_item",
        "span_attribute_item",
        "attributes_object",
        "attribute_key",
        "int_value",
        "double_value",
        "array_value",
        "value_not_object",
        "status",
        "start_overflow",
    ],
)
def test_malformed_shape_is_reported(tmp_path: Path, line: str, expected: IssueKind) -> None:
    path = tmp_path / "t.jsonl"
    path.write_text(line + "\n")

    _, issues = load_spans(path)

    assert [i.kind for i in issues] == [expected]


def test_malformed_entry_detail_names_it(tmp_path: Path) -> None:
    path = tmp_path / "t.jsonl"
    path.write_text('{"resourceSpans": [{"scopeSpans": []}, 1]}\n')

    _, issues = load_spans(path)

    assert [i.detail for i in issues] == ["line 1: resourceSpans[1] is not an object"]


def test_malformed_attribute_is_skipped_and_span_kept(tmp_path: Path) -> None:
    attributes = [{"key": "k", "value": {"intValue": "x"}}, {"key": "ok", "value": {}}]
    path = write_jsonl(tmp_path / "t.jsonl", [json.loads(_span_line(attributes=attributes))])

    spans, _ = load_spans(path)

    assert [s.attributes for s in spans] == [{"ok": None}]


@pytest.mark.parametrize(
    ("trace_id", "span_id", "parent"),
    [
        ("", S1, ""),
        ("ab", S1, ""),
        ("zz" * 16, S1, ""),
        (TRACE_ID, "", ""),
        (TRACE_ID, "a1", ""),
        (TRACE_ID, S1, "a1"),
    ],
)
def test_invalid_id_is_reported(tmp_path: Path, trace_id: str, span_id: str, parent: str) -> None:
    path = write_jsonl(
        tmp_path / "t.jsonl", [otlp_document([otlp_span(span_id, parent, trace_id=trace_id)])]
    )

    _, issues = load_spans(path)

    assert [i.kind for i in issues] == [IssueKind.INVALID_SPAN]


def test_non_string_id_is_reported(tmp_path: Path) -> None:
    path = tmp_path / "t.jsonl"
    path.write_text(_span_line(traceId=123) + "\n")

    _, issues = load_spans(path)

    assert [i.kind for i in issues] == [IssueKind.INVALID_SPAN]


def test_base64_id_detail_gives_hint(tmp_path: Path) -> None:
    span = otlp_span("t61rcWkgMzE=", trace_id="CvdlGRbNQ92ESOshHIAxnA==")
    path = write_jsonl(tmp_path / "t.jsonl", [otlp_document([span])])

    _, issues = load_spans(path)

    assert "IDs look base64; OTLP JSON uses hex" in issues[0].detail


def test_short_hex_id_detail_has_no_base64_hint(tmp_path: Path) -> None:
    path = write_jsonl(tmp_path / "t.jsonl", [otlp_document([otlp_span("a1")])])

    _, issues = load_spans(path)

    assert "base64" not in issues[0].detail


def test_invalid_span_detail_names_line_number(tmp_path: Path) -> None:
    path = tmp_path / "t.jsonl"
    path.write_text(_line(S1) + _span_line(spanId="a1") + "\n")

    _, issues = load_spans(path)

    assert issues[0].detail.startswith("line 2: ")


@pytest.mark.parametrize("start", [True, 1.5, "1.5", "abc", -1, None])
def test_invalid_start_time_is_reported(tmp_path: Path, start: object) -> None:
    path = tmp_path / "t.jsonl"
    path.write_text(_span_line(startTimeUnixNano=start) + "\n")

    _, issues = load_spans(path)

    assert [i.kind for i in issues] == [IssueKind.INVALID_SPAN]


def test_end_before_start_is_reported(tmp_path: Path) -> None:
    path = write_jsonl(
        tmp_path / "t.jsonl", [otlp_document([otlp_span(S1, start_ns=2_000, end_ns=1_000)])]
    )

    _, issues = load_spans(path)

    assert [i.kind for i in issues] == [IssueKind.INVALID_SPAN]


def test_missing_path_raises(tmp_path: Path) -> None:
    with pytest.raises(FileNotFoundError, match=r"traces\.path"):
        load_spans(tmp_path / "missing")


def test_empty_folder_raises(tmp_path: Path) -> None:
    with pytest.raises(FileNotFoundError, match=r"No trace files found"):
        load_spans(tmp_path)


def test_folder_with_only_hidden_files_raises(tmp_path: Path) -> None:
    (tmp_path / ".DS_Store").write_text("x")

    with pytest.raises(FileNotFoundError, match=r"No trace files found"):
        load_spans(tmp_path)
