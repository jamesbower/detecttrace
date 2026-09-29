import gzip
import json
from pathlib import Path

import pytest
from builders import (
    TRACE_ID,
    otlp_document,
    otlp_span,
    write_gzip_jsonl,
    write_jsonl,
)

from detecttrace.model import IssueKind, Span
from detecttrace.otlp import load_spans


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


@pytest.mark.parametrize(
    ("value", "expected"),
    [("text", "text"), (3, 3), (2.5, 2.5), (True, True), (["a", 1], ["a", 1])],
)
def test_decodes_attribute_value_types(tmp_path: Path, value: object, expected: object) -> None:
    path = write_jsonl(
        tmp_path / "t.jsonl", [otlp_document([otlp_span("a1", attributes={"k": value})])]
    )

    spans, _ = load_spans(path)

    assert spans[0].attributes["k"] == expected


def test_decodes_kvlist_value_as_dict(tmp_path: Path) -> None:
    span = otlp_span("a1")
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
    path = write_jsonl(tmp_path / "t.jsonl", [otlp_document([otlp_span("a1", "")])])

    spans, _ = load_spans(path)

    assert spans[0].parent_span_id is None


@pytest.mark.parametrize("status", [{"code": 2}, {"code": "STATUS_CODE_ERROR"}])
def test_error_status_marks_span_as_error(tmp_path: Path, status: dict[str, object]) -> None:
    path = write_jsonl(tmp_path / "t.jsonl", [otlp_document([otlp_span("a1", status=status)])])

    spans, _ = load_spans(path)

    assert spans[0].is_error is True


def test_reads_single_pretty_printed_document(tmp_path: Path) -> None:
    path = tmp_path / "trace.json"
    path.write_text(json.dumps(otlp_document([otlp_span("a1"), otlp_span("a2")]), indent=2))

    spans, _ = load_spans(path)

    assert [s.span_id for s in spans] == ["a1", "a2"]


def test_reads_gzip_file(tmp_path: Path) -> None:
    path = write_gzip_jsonl(tmp_path / "traces.jsonl.gz", [otlp_document([otlp_span("a1")])])

    spans, _ = load_spans(path)

    assert [s.span_id for s in spans] == ["a1"]


def test_reads_one_trace_split_across_rotated_files(tmp_path: Path) -> None:
    write_jsonl(tmp_path / "traces-1.jsonl", [otlp_document([otlp_span("a1")])])
    write_jsonl(tmp_path / "traces-2.jsonl", [otlp_document([otlp_span("a2", "a1")])])

    spans, _ = load_spans(tmp_path)

    assert sorted(s.span_id for s in spans) == ["a1", "a2"]


def test_first_copy_of_duplicate_span_wins_in_sorted_file_order(tmp_path: Path) -> None:
    write_jsonl(tmp_path / "b.jsonl", [otlp_document([otlp_span("a1", name="second")])])
    write_jsonl(tmp_path / "a.jsonl", [otlp_document([otlp_span("a1", name="first")])])

    spans, _ = load_spans(tmp_path)

    assert [s.name for s in spans] == ["first"]


def test_files_are_read_in_posix_relative_path_order(tmp_path: Path) -> None:
    # "/" sorts before "0" in POSIX form; a Windows "\\" would sort after it.
    (tmp_path / "a").mkdir()
    write_jsonl(tmp_path / "a" / "x.jsonl", [otlp_document([otlp_span("a1", name="first")])])
    write_jsonl(tmp_path / "a0.jsonl", [otlp_document([otlp_span("a1", name="second")])])

    spans, _ = load_spans(tmp_path)

    assert [s.name for s in spans] == ["first"]


def test_identical_duplicate_span_is_reported_as_duplicate(tmp_path: Path) -> None:
    write_jsonl(tmp_path / "a.jsonl", [otlp_document([otlp_span("a1")])])
    write_jsonl(tmp_path / "b.jsonl", [otlp_document([otlp_span("a1")])])

    _, issues = load_spans(tmp_path)

    assert [i.kind for i in issues] == [IssueKind.DUPLICATE_SPAN]


def test_differing_duplicate_span_is_reported_as_conflicting(tmp_path: Path) -> None:
    write_jsonl(tmp_path / "a.jsonl", [otlp_document([otlp_span("a1", name="first")])])
    write_jsonl(tmp_path / "b.jsonl", [otlp_document([otlp_span("a1", name="second")])])

    _, issues = load_spans(tmp_path)

    assert [i.kind for i in issues] == [IssueKind.CONFLICTING_DUPLICATE_SPAN]


def test_empty_file_is_reported(tmp_path: Path) -> None:
    path = tmp_path / "empty.jsonl"
    path.write_text("")

    _, issues = load_spans(path)

    assert [i.kind for i in issues] == [IssueKind.EMPTY_FILE]


def test_truncated_last_line_keeps_earlier_lines(tmp_path: Path) -> None:
    path = write_jsonl(tmp_path / "t.jsonl", [otlp_document([otlp_span("a1")])])
    with path.open("a") as handle:
        handle.write('{"resourceSpans": [{"scopeSp')

    spans, _ = load_spans(path)

    assert [s.span_id for s in spans] == ["a1"]


def test_truncated_last_line_is_reported(tmp_path: Path) -> None:
    path = write_jsonl(tmp_path / "t.jsonl", [otlp_document([otlp_span("a1")])])
    with path.open("a") as handle:
        handle.write('{"resourceSpans": [{"scopeSp')

    _, issues = load_spans(path)

    assert [i.kind for i in issues] == [IssueKind.TRUNCATED_LINE]


def test_invalid_middle_line_is_reported(tmp_path: Path) -> None:
    doc = json.dumps(otlp_document([otlp_span("a1")]))
    path = tmp_path / "t.jsonl"
    path.write_text(f"{doc}\nnot json\n{doc}\n")

    _, issues = load_spans(path)

    assert [i.kind for i in issues] == [IssueKind.INVALID_LINE, IssueKind.DUPLICATE_SPAN]


def test_truncated_gzip_file_is_reported(tmp_path: Path) -> None:
    data = gzip.compress((json.dumps(otlp_document([otlp_span("a1")])) + "\n").encode())
    path = tmp_path / "t.jsonl.gz"
    path.write_bytes(data[:-12])

    _, issues = load_spans(path)

    assert IssueKind.TRUNCATED_FILE in [i.kind for i in issues]


def test_document_without_resource_spans_is_reported(tmp_path: Path) -> None:
    path = write_jsonl(tmp_path / "t.jsonl", [{"name": "not otlp"}])

    _, issues = load_spans(path)

    assert [i.kind for i in issues] == [IssueKind.INVALID_FILE]


def test_span_without_ids_is_reported(tmp_path: Path) -> None:
    path = write_jsonl(tmp_path / "t.jsonl", [otlp_document([otlp_span("")])])

    _, issues = load_spans(path)

    assert [i.kind for i in issues] == [IssueKind.INVALID_SPAN]


def test_missing_path_raises(tmp_path: Path) -> None:
    with pytest.raises(FileNotFoundError, match=r"traces\.path"):
        load_spans(tmp_path / "missing")
