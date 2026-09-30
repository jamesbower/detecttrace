import json
from pathlib import Path

import pytest
from builders import (
    TRACE_ID,
    otlp_document,
    otlp_span,
    span_hex,
    write_jsonl,
)

from detecttrace.model import IssueKind, Span
from detecttrace.traces import load_spans

S1 = span_hex(1)
S2 = span_hex(2)


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


def test_non_ascii_attribute_value_loads_unchanged(tmp_path: Path) -> None:
    path = tmp_path / "t.jsonl"
    doc = otlp_document([otlp_span(S1, attributes={"city": "Zürich 東京"})])
    path.write_text(_line(S2) + json.dumps(doc, ensure_ascii=False) + "\n", encoding="utf-8")

    spans, _ = load_spans(path)

    assert spans[1].attributes == {"city": "Zürich 東京"}


def test_non_ascii_span_name_loads_unchanged(tmp_path: Path) -> None:
    path = tmp_path / "t.jsonl"
    doc = otlp_document([otlp_span(S1, name="invoke_agent Zürich 東京")])
    path.write_text(_line(S2) + json.dumps(doc, ensure_ascii=False) + "\n", encoding="utf-8")

    spans, _ = load_spans(path)

    assert spans[1].name == "invoke_agent Zürich 東京"


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
        (_attribute_line({"intValue": " 12 "}), IssueKind.INVALID_ATTRIBUTE),
        (_attribute_line({"intValue": "1_000"}), IssueKind.INVALID_ATTRIBUTE),
        (_attribute_line({"intValue": "+5"}), IssueKind.INVALID_ATTRIBUTE),
        (_attribute_line({"intValue": "\u0663"}), IssueKind.INVALID_ATTRIBUTE),
        (_attribute_line({"doubleValue": "x"}), IssueKind.INVALID_ATTRIBUTE),
        (_attribute_line({"doubleValue": 10**400}), IssueKind.INVALID_ATTRIBUTE),
        (_attribute_line({"stringValue": 1}), IssueKind.INVALID_ATTRIBUTE),
        (_attribute_line({"boolValue": "true"}), IssueKind.INVALID_ATTRIBUTE),
        (_attribute_line({"bytesValue": 1}), IssueKind.INVALID_ATTRIBUTE),
        (_attribute_line({"kvlistValue": [1]}), IssueKind.INVALID_ATTRIBUTE),
        (_attribute_line({"kvlistValue": {"values": [1]}}), IssueKind.INVALID_ATTRIBUTE),
        (_attribute_line({"arrayValue": [1]}), IssueKind.INVALID_ATTRIBUTE),
        (_attribute_line("s"), IssueKind.INVALID_ATTRIBUTE),
        (_attribute_line({"stringvalue": "DT-1"}), IssueKind.INVALID_ATTRIBUTE),
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
        "int_value_spaces",
        "int_value_underscore",
        "int_value_plus",
        "int_value_non_ascii_digit",
        "double_value",
        "double_value_huge_integer",
        "string_value_type",
        "bool_value_type",
        "bytes_value_type",
        "kvlist_value_not_object",
        "kvlist_value_item",
        "array_value",
        "value_not_object",
        "unknown_value_type",
        "status",
        "start_overflow",
    ],
)
def test_malformed_shape_is_reported(tmp_path: Path, line: str, expected: IssueKind) -> None:
    path = tmp_path / "t.jsonl"
    path.write_text(line + "\n", encoding="utf-8")

    _, issues = load_spans(path)

    assert [i.kind for i in issues] == [expected]


def test_malformed_entry_detail_names_it(tmp_path: Path) -> None:
    path = tmp_path / "t.jsonl"
    path.write_text('{"resourceSpans": [{"scopeSpans": []}, 1]}\n', encoding="utf-8")

    _, issues = load_spans(path)

    assert [i.detail for i in issues] == ["line 1: resourceSpans[1] is not an object"]


@pytest.mark.parametrize(
    ("line", "expected"),
    [
        (
            '{"resourceSpans": [{"scopeSpans": [1]}]}',
            "line 1: resourceSpans[0].scopeSpans[0] is not an object",
        ),
        (
            '{"resourceSpans": [{"scopeSpans": [{"spans": {"a": 1}}]}]}',
            "line 1: resourceSpans[0].scopeSpans[0].spans is not a list",
        ),
    ],
    ids=["scope_spans_item", "spans_object"],
)
def test_malformed_scope_spans_detail_names_problem(
    tmp_path: Path, line: str, expected: str
) -> None:
    path = tmp_path / "t.jsonl"
    path.write_text(line + "\n", encoding="utf-8")

    _, issues = load_spans(path)

    assert [i.detail for i in issues] == [expected]


def test_unknown_value_type_detail_names_keys(tmp_path: Path) -> None:
    path = tmp_path / "t.jsonl"
    path.write_text(_attribute_line({"stringvalue": "DT-1"}) + "\n", encoding="utf-8")

    _, issues = load_spans(path)

    assert issues[0].detail.endswith("attributes[0]: unknown value type: stringvalue")


def test_negative_int_value_text_is_decoded(tmp_path: Path) -> None:
    path = tmp_path / "t.jsonl"
    path.write_text(_attribute_line({"intValue": "-12"}) + "\n", encoding="utf-8")

    spans, _ = load_spans(path)

    assert spans[0].attributes["k"] == -12


def test_non_string_name_becomes_empty(tmp_path: Path) -> None:
    path = tmp_path / "t.jsonl"
    path.write_text(_span_line(name=5) + "\n", encoding="utf-8")

    spans, _ = load_spans(path)

    assert [s.name for s in spans] == [""]


def test_non_string_name_is_reported(tmp_path: Path) -> None:
    path = tmp_path / "t.jsonl"
    path.write_text(_span_line(name=5) + "\n", encoding="utf-8")

    _, issues = load_spans(path)

    assert [(i.kind, i.detail) for i in issues] == [
        (
            IssueKind.INVALID_ATTRIBUTE,
            "line 1: resourceSpans[0].scopeSpans[0].spans[0]: name is not a string",
        )
    ]


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
    path.write_text(_span_line(traceId=123) + "\n", encoding="utf-8")

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
    path.write_text(_line(S1) + _span_line(spanId="a1") + "\n", encoding="utf-8")

    _, issues = load_spans(path)

    assert issues[0].detail.startswith("line 2: ")


@pytest.mark.parametrize("start", [True, 1.5, 1000.0, "1.5", "abc", -1, None, 2**64, str(2**64)])
def test_invalid_start_time_is_reported(tmp_path: Path, start: object) -> None:
    path = tmp_path / "t.jsonl"
    path.write_text(
        _span_line(startTimeUnixNano=start, endTimeUnixNano=start) + "\n", encoding="utf-8"
    )

    _, issues = load_spans(path)

    assert [i.kind for i in issues] == [IssueKind.INVALID_SPAN]


def test_largest_unsigned_64_bit_time_is_accepted(tmp_path: Path) -> None:
    largest = str(2**64 - 1)
    path = tmp_path / "t.jsonl"
    path.write_text(
        _span_line(startTimeUnixNano=largest, endTimeUnixNano=largest) + "\n", encoding="utf-8"
    )

    spans, _ = load_spans(path)

    assert [s.end_ns for s in spans] == [2**64 - 1]


def test_end_before_start_is_reported(tmp_path: Path) -> None:
    path = write_jsonl(
        tmp_path / "t.jsonl", [otlp_document([otlp_span(S1, start_ns=2_000, end_ns=1_000)])]
    )

    _, issues = load_spans(path)

    assert [i.kind for i in issues] == [IssueKind.INVALID_SPAN]
