import gzip
import io
import json
import os
import sys
import tracemalloc
from collections.abc import Iterator
from pathlib import Path
from typing import BinaryIO

import pytest
import zstandard
from builders import (
    TRACE_ID,
    otlp_document,
    otlp_span,
    span_hex,
    write_gzip_jsonl,
    write_jsonl,
)

from detecttrace.model import IssueKind, Span
from detecttrace.otlp import TraceFileError, load_spans

S1 = span_hex(1)
S2 = span_hex(2)
BOM = "﻿"
MIB = 1 << 20
CONSOLE_OUTPUT = Path(__file__).parent / "fixtures" / "console_exporter" / "console.json"
needs_permissions = pytest.mark.skipif(
    sys.platform == "win32" or os.geteuid() == 0,
    reason="needs POSIX permissions that the current user cannot bypass",
)


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


def test_reads_single_pretty_printed_document(tmp_path: Path) -> None:
    path = tmp_path / "trace.json"
    path.write_text(json.dumps(otlp_document([otlp_span(S1), otlp_span(S2)]), indent=2))

    spans, _ = load_spans(path)

    assert [s.span_id for s in spans] == [S1, S2]


def test_reads_document_whose_first_line_opens_resource_spans(tmp_path: Path) -> None:
    text = json.dumps(otlp_document([otlp_span(S1)]), indent=2)
    path = tmp_path / "trace.json"
    path.write_text('{ "resourceSpans": [' + text.split('"resourceSpans": [', 1)[1])

    spans, _ = load_spans(path)

    assert [s.span_id for s in spans] == [S1]


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


@pytest.fixture
def unreadable_folder(tmp_path: Path) -> Iterator[Path]:
    folder = tmp_path / "sub"
    folder.mkdir()
    write_jsonl(folder / "t.jsonl", [otlp_document([otlp_span(S1)])])
    folder.chmod(0)
    yield folder
    folder.chmod(0o700)


@needs_permissions
def test_unreadable_subfolder_is_reported(tmp_path: Path, unreadable_folder: Path) -> None:
    write_jsonl(tmp_path / "t.jsonl", [otlp_document([otlp_span(S2)])])

    _, issues = load_spans(tmp_path)

    assert [(i.kind, i.subject, i.detail) for i in issues] == [
        (IssueKind.INVALID_FILE, "sub", "Permission denied")
    ]


@pytest.fixture
def unenterable_folder(tmp_path: Path) -> Iterator[Path]:
    folder = tmp_path / "sub"
    folder.mkdir()
    write_jsonl(folder / "t.jsonl", [otlp_document([otlp_span(S1)])])
    # Listable but not enterable: the listing works, but checking each file fails.
    folder.chmod(0o444)
    yield folder
    folder.chmod(0o700)


@needs_permissions
def test_file_in_unenterable_subfolder_is_reported(
    tmp_path: Path, unenterable_folder: Path
) -> None:
    write_jsonl(tmp_path / "t.jsonl", [otlp_document([otlp_span(S2)])])

    _, issues = load_spans(tmp_path)

    assert [(i.kind, i.subject, i.detail) for i in issues] == [
        (IssueKind.INVALID_FILE, "sub/t.jsonl", "Permission denied")
    ]


@pytest.fixture
def unreadable_root(tmp_path: Path) -> Iterator[Path]:
    folder = tmp_path / "traces"
    folder.mkdir()
    write_jsonl(folder / "t.jsonl", [otlp_document([otlp_span(S1)])])
    folder.chmod(0)
    yield folder
    folder.chmod(0o700)


@needs_permissions
def test_unreadable_root_folder_raises_trace_file_error(unreadable_root: Path) -> None:
    with pytest.raises(TraceFileError, match=r"cannot be read: Permission denied"):
        load_spans(unreadable_root)


needs_symlinks = pytest.mark.skipif(
    sys.platform == "win32", reason="creating symbolic links needs extra rights on Windows"
)


@needs_symlinks
def test_dangling_symlink_is_reported(tmp_path: Path) -> None:
    write_jsonl(tmp_path / "t.jsonl", [otlp_document([otlp_span(S1)])])
    (tmp_path / "gone.jsonl").symlink_to(tmp_path / "missing.jsonl")

    _, issues = load_spans(tmp_path)

    assert [(i.kind, i.subject, i.detail) for i in issues] == [
        (IssueKind.INVALID_FILE, "gone.jsonl", "symbolic link target is missing or loops")
    ]


@needs_symlinks
def test_symlink_loop_is_reported(tmp_path: Path) -> None:
    write_jsonl(tmp_path / "t.jsonl", [otlp_document([otlp_span(S1)])])
    (tmp_path / "loop.jsonl").symlink_to(tmp_path / "loop.jsonl")

    _, issues = load_spans(tmp_path)

    assert [(i.kind, i.subject, i.detail) for i in issues] == [
        (IssueKind.INVALID_FILE, "loop.jsonl", "symbolic link target is missing or loops")
    ]


@needs_permissions
def test_unreadable_file_detail_has_no_path(tmp_path: Path) -> None:
    path = write_jsonl(tmp_path / "t.jsonl", [otlp_document([otlp_span(S1)])])
    path.chmod(0)

    _, issues = load_spans(path)

    assert [(i.kind, i.detail) for i in issues] == [(IssueKind.INVALID_FILE, "Permission denied")]


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


def test_identical_duplicate_span_with_nan_attribute_is_reported_as_duplicate(
    tmp_path: Path,
) -> None:
    # NaN never equals itself, so plain equality would call these copies conflicting.
    line = _attribute_line({"doubleValue": "NaN"}) + "\n"
    (tmp_path / "a.jsonl").write_text(line)
    (tmp_path / "b.jsonl").write_text(line)

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


def test_invalid_first_line_opening_an_object_keeps_later_lines(tmp_path: Path) -> None:
    path = tmp_path / "t.jsonl"
    path.write_text('{"resourceSpans": [\n' + _line(S1) + _line(S2))

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
        (IssueKind.TRUNCATED_FILE, "compressed file ends early")
    ]


def test_truncated_gzip_after_first_line_says_earlier_lines_were_read(tmp_path: Path) -> None:
    # Two gzip members: the first line is intact, the second member is cut short.
    path = tmp_path / "t.jsonl.gz"
    path.write_bytes(gzip.compress(_line(S1).encode()) + gzip.compress(_line(S2).encode())[:-12])

    _, issues = load_spans(path)

    assert [(i.kind, i.detail) for i in issues] == [
        (IssueKind.TRUNCATED_FILE, "compressed file ends early; earlier lines were read")
    ]


def test_truncated_gzip_document_is_reported(tmp_path: Path) -> None:
    text = json.dumps(otlp_document([otlp_span(S1), otlp_span(S2)]), indent=2)
    path = tmp_path / "t.json.gz"
    path.write_bytes(gzip.compress(text.encode())[:-12])

    _, issues = load_spans(path)

    assert [(i.kind, i.detail) for i in issues] == [
        (IssueKind.TRUNCATED_FILE, "compressed file ends early")
    ]


def test_corrupt_gzip_header_is_reported(tmp_path: Path) -> None:
    path = tmp_path / "t.jsonl.gz"
    path.write_bytes(b"\x1f\x8b\x09" + b"\x00" * 20)

    _, issues = load_spans(path)

    assert [(i.kind, i.detail) for i in issues] == [
        (IssueKind.INVALID_FILE, "corrupt compressed data")
    ]


def test_gzip_with_trailing_garbage_keeps_earlier_lines(tmp_path: Path) -> None:
    path = tmp_path / "t.jsonl.gz"
    path.write_bytes(gzip.compress((_line(S1) + _line(S2)).encode()) + b"garbage")

    spans, _ = load_spans(path)

    assert [s.span_id for s in spans] == [S1, S2]


def test_gzip_with_trailing_garbage_is_reported(tmp_path: Path) -> None:
    path = tmp_path / "t.jsonl.gz"
    path.write_bytes(gzip.compress((_line(S1) + _line(S2)).encode()) + b"garbage")

    _, issues = load_spans(path)

    assert [(i.kind, i.detail) for i in issues] == [
        (IssueKind.INVALID_FILE, "corrupt compressed data; earlier lines were read")
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
        (IssueKind.INVALID_FILE, "corrupt compressed data; earlier lines were read")
    ]


def test_corrupt_gzip_data_keeps_earlier_spans(tmp_path: Path) -> None:
    path = tmp_path / "t.jsonl.gz"
    path.write_bytes(_corrupt_gzip())

    spans, _ = load_spans(path)

    assert [s.span_id for s in spans] == [S1]


def _zstd(text: str) -> bytes:
    return zstandard.ZstdCompressor().compress(text.encode())


def test_reads_zstd_json_lines(tmp_path: Path) -> None:
    path = tmp_path / "t.jsonl.zst"
    path.write_bytes(_zstd(_line(S1) + _line(S2)))

    spans, _ = load_spans(path)

    assert [s.span_id for s in spans] == [S1, S2]


def test_reads_zstd_pretty_printed_document(tmp_path: Path) -> None:
    path = tmp_path / "t.json.zst"
    path.write_bytes(_zstd(json.dumps(otlp_document([otlp_span(S1), otlp_span(S2)]), indent=2)))

    spans, _ = load_spans(path)

    assert [s.span_id for s in spans] == [S1, S2]


@pytest.fixture
def zstd_and_plain_files(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    # None in sys.modules makes `import zstandard` raise ImportError, as without the extra.
    monkeypatch.setitem(sys.modules, "zstandard", None)
    (tmp_path / "a.jsonl.zst").write_bytes(_zstd(_line(S1)))
    (tmp_path / "b.jsonl").write_text(_line(S2))
    return tmp_path


def test_zstd_without_extra_is_reported_once(zstd_and_plain_files: Path) -> None:
    _, issues = load_spans(zstd_and_plain_files)

    assert [(i.kind, i.subject) for i in issues] == [
        (IssueKind.UNSUPPORTED_COMPRESSION, "a.jsonl.zst")
    ]


def test_zstd_without_extra_detail_names_the_extra(zstd_and_plain_files: Path) -> None:
    _, issues = load_spans(zstd_and_plain_files)

    assert "detecttrace[zstd]" in issues[0].detail


def test_zstd_without_extra_still_loads_other_files(zstd_and_plain_files: Path) -> None:
    spans, _ = load_spans(zstd_and_plain_files)

    assert [s.span_id for s in spans] == [S2]


def _truncated_zstd() -> bytes:
    # Two zstd frames: the first line is intact, the second frame is cut short.
    return _zstd(_line(S1)) + _zstd(_line(S2))[:-6]


def test_truncated_zstd_is_reported(tmp_path: Path) -> None:
    path = tmp_path / "t.jsonl.zst"
    path.write_bytes(_truncated_zstd())

    _, issues = load_spans(path)

    assert [(i.kind, i.detail) for i in issues] == [
        (IssueKind.TRUNCATED_FILE, "compressed file ends early; earlier lines were read")
    ]


def test_truncated_zstd_keeps_earlier_spans(tmp_path: Path) -> None:
    path = tmp_path / "t.jsonl.zst"
    path.write_bytes(_truncated_zstd())

    spans, _ = load_spans(path)

    assert [s.span_id for s in spans] == [S1]


def test_zstd_cut_inside_first_frame_is_reported(tmp_path: Path) -> None:
    path = tmp_path / "t.jsonl.zst"
    path.write_bytes(_zstd(_line(S1))[:-6])

    _, issues = load_spans(path)

    assert [(i.kind, i.detail) for i in issues] == [
        (IssueKind.TRUNCATED_FILE, "compressed file ends early")
    ]


def test_corrupt_zstd_frame_header_is_reported(tmp_path: Path) -> None:
    path = tmp_path / "t.jsonl.zst"
    path.write_bytes(b"\x28\xb5\x2f\xfd" + b"\xff" * 20)

    _, issues = load_spans(path)

    assert [(i.kind, i.detail) for i in issues] == [
        (IssueKind.INVALID_FILE, "corrupt compressed data")
    ]


def test_zstd_with_trailing_garbage_is_reported(tmp_path: Path) -> None:
    path = tmp_path / "t.jsonl.zst"
    path.write_bytes(_zstd(_line(S1) + _line(S2)) + b"garbage")

    _, issues = load_spans(path)

    assert [(i.kind, i.detail) for i in issues] == [
        (IssueKind.INVALID_FILE, "corrupt compressed data; earlier lines were read")
    ]


def test_zstd_with_trailing_garbage_keeps_earlier_lines(tmp_path: Path) -> None:
    path = tmp_path / "t.jsonl.zst"
    path.write_bytes(_zstd(_line(S1) + _line(S2)) + b"garbage")

    spans, _ = load_spans(path)

    assert [s.span_id for s in spans] == [S1, S2]


BOMB_BYTES = 200 * MIB
MEMORY_BUDGET = 64 * MIB


def _write_bomb(path: Path, writer: BinaryIO | io.BufferedIOBase) -> Path:
    # Written a mebibyte at a time so the test itself never holds the whole bomb.
    zeros = bytes(MIB)
    for _ in range(BOMB_BYTES // MIB):
        writer.write(zeros)
    writer.close()
    return path


@pytest.fixture(scope="module")
def zstd_bomb(tmp_path_factory: pytest.TempPathFactory) -> Path:
    path = tmp_path_factory.mktemp("bomb") / "bomb.jsonl.zst"
    handle = path.open("wb")
    return _write_bomb(path, zstandard.ZstdCompressor(level=19).stream_writer(handle))


@pytest.fixture(scope="module")
def gzip_bomb(tmp_path_factory: pytest.TempPathFactory) -> Path:
    path = tmp_path_factory.mktemp("bomb") / "bomb.jsonl.gz"
    return _write_bomb(path, gzip.open(path, "wb", compresslevel=1))


def _peak_bytes_while_loading(path: Path) -> int:
    tracemalloc.start()
    try:
        load_spans(path)
        return tracemalloc.get_traced_memory()[1]
    finally:
        tracemalloc.stop()


def test_zstd_bomb_loads_within_memory_budget(zstd_bomb: Path) -> None:
    assert _peak_bytes_while_loading(zstd_bomb) < MEMORY_BUDGET


def test_zstd_bomb_is_reported_as_too_long_line(zstd_bomb: Path) -> None:
    _, issues = load_spans(zstd_bomb)

    assert [(i.kind, i.detail) for i in issues] == [
        (IssueKind.INVALID_LINE, "line 1 is over 32 MiB")
    ]


def test_gzip_bomb_loads_within_memory_budget(gzip_bomb: Path) -> None:
    assert _peak_bytes_while_loading(gzip_bomb) < MEMORY_BUDGET


def test_gzip_bomb_is_reported_as_too_long_line(gzip_bomb: Path) -> None:
    _, issues = load_spans(gzip_bomb)

    assert [(i.kind, i.detail) for i in issues] == [
        (IssueKind.INVALID_LINE, "line 1 is over 32 MiB")
    ]


@pytest.fixture
def long_line_file(tmp_path: Path) -> Path:
    # Opens like a JSON object, so the one-document reader sees it before the line reader.
    path = tmp_path / "t.jsonl"
    path.write_text('{"k": "' + "x" * (33 * MIB) + '"}\n' + _line(S2))
    return path


def test_too_long_plain_line_is_reported(long_line_file: Path) -> None:
    _, issues = load_spans(long_line_file)

    assert [(i.kind, i.detail) for i in issues] == [
        (IssueKind.INVALID_LINE, "line 1 is over 32 MiB")
    ]


def test_too_long_plain_line_keeps_later_lines(long_line_file: Path) -> None:
    spans, _ = load_spans(long_line_file)

    assert [s.span_id for s in spans] == [S2]


def test_too_large_document_is_reported(tmp_path: Path) -> None:
    path = tmp_path / "t.json"
    path.write_text("{\n" + " " * (33 * MIB) + "}\n")

    _, issues = load_spans(path)

    assert [(i.kind, i.detail) for i in issues] == [
        (IssueKind.INVALID_FILE, "document is over 32 MiB")
    ]


def test_console_exporter_output_is_reported_once(tmp_path: Path) -> None:
    path = tmp_path / "console.json"
    path.write_bytes(CONSOLE_OUTPUT.read_bytes())

    _, issues = load_spans(path)

    assert [(i.kind, i.subject) for i in issues] == [
        (IssueKind.CONSOLE_EXPORTER_OUTPUT, "console.json")
    ]


def test_console_exporter_detail_points_to_file_exporters(tmp_path: Path) -> None:
    path = tmp_path / "console.json"
    path.write_bytes(CONSOLE_OUTPUT.read_bytes())

    _, issues = load_spans(path)

    assert issues[0].detail == (
        "this is OpenTelemetry console exporter output, not OTLP JSON; write traces with "
        "the Collector file exporter, or with FileSpanExporter (pip install detecttrace[otel])"
    )


def test_gzip_console_exporter_output_is_reported_once(tmp_path: Path) -> None:
    path = tmp_path / "console.json.gz"
    path.write_bytes(gzip.compress(CONSOLE_OUTPUT.read_bytes()))

    _, issues = load_spans(path)

    assert [i.kind for i in issues] == [IssueKind.CONSOLE_EXPORTER_OUTPUT]


def test_console_exporter_output_one_object_per_line_is_reported_once(tmp_path: Path) -> None:
    # Pretty-printed objects re-joined one per line would otherwise give one issue per line.
    text = CONSOLE_OUTPUT.read_text().replace("\n", "").replace("}{", "}\n{") + "\n"
    path = tmp_path / "console.jsonl"
    path.write_text(text)

    _, issues = load_spans(path)

    assert [i.kind for i in issues] == [IssueKind.CONSOLE_EXPORTER_OUTPUT]


def test_otlp_mentioning_console_trace_id_loads_without_issues(tmp_path: Path) -> None:
    value = '"context": {"trace_id": "0x5b8aa5a2d2c872e8321cf37308d69df2"}'
    path = write_jsonl(
        tmp_path / "t.jsonl", [otlp_document([otlp_span(S1, attributes={"k": value})])]
    )

    _, issues = load_spans(path)

    assert issues == []


def test_console_sniff_reads_only_the_first_64_kib(tmp_path: Path) -> None:
    # 10 MB with the console marker only at the end: a sniff past 64 KiB would find it.
    path = tmp_path / "big.json"
    path.write_text('{"context": ' * 850_000 + '{"trace_id": "0x1"}')

    _, issues = load_spans(path)

    assert [i.kind for i in issues] == [IssueKind.TRUNCATED_LINE]


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
    path.write_text(line + "\n")

    _, issues = load_spans(path)

    assert [i.kind for i in issues] == [expected]


def test_malformed_entry_detail_names_it(tmp_path: Path) -> None:
    path = tmp_path / "t.jsonl"
    path.write_text('{"resourceSpans": [{"scopeSpans": []}, 1]}\n')

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
    path.write_text(line + "\n")

    _, issues = load_spans(path)

    assert [i.detail for i in issues] == [expected]


def test_unknown_value_type_detail_names_keys(tmp_path: Path) -> None:
    path = tmp_path / "t.jsonl"
    path.write_text(_attribute_line({"stringvalue": "DT-1"}) + "\n")

    _, issues = load_spans(path)

    assert issues[0].detail.endswith("attributes[0]: unknown value type: stringvalue")


def test_negative_int_value_text_is_decoded(tmp_path: Path) -> None:
    path = tmp_path / "t.jsonl"
    path.write_text(_attribute_line({"intValue": "-12"}) + "\n")

    spans, _ = load_spans(path)

    assert spans[0].attributes["k"] == -12


def test_non_string_name_becomes_empty(tmp_path: Path) -> None:
    path = tmp_path / "t.jsonl"
    path.write_text(_span_line(name=5) + "\n")

    spans, _ = load_spans(path)

    assert [s.name for s in spans] == [""]


def test_non_string_name_is_reported(tmp_path: Path) -> None:
    path = tmp_path / "t.jsonl"
    path.write_text(_span_line(name=5) + "\n")

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


@pytest.mark.parametrize("start", [True, 1.5, 1000.0, "1.5", "abc", -1, None, 2**64, str(2**64)])
def test_invalid_start_time_is_reported(tmp_path: Path, start: object) -> None:
    path = tmp_path / "t.jsonl"
    path.write_text(_span_line(startTimeUnixNano=start, endTimeUnixNano=start) + "\n")

    _, issues = load_spans(path)

    assert [i.kind for i in issues] == [IssueKind.INVALID_SPAN]


def test_largest_unsigned_64_bit_time_is_accepted(tmp_path: Path) -> None:
    largest = str(2**64 - 1)
    path = tmp_path / "t.jsonl"
    path.write_text(_span_line(startTimeUnixNano=largest, endTimeUnixNano=largest) + "\n")

    spans, _ = load_spans(path)

    assert [s.end_ns for s in spans] == [2**64 - 1]


def test_end_before_start_is_reported(tmp_path: Path) -> None:
    path = write_jsonl(
        tmp_path / "t.jsonl", [otlp_document([otlp_span(S1, start_ns=2_000, end_ns=1_000)])]
    )

    _, issues = load_spans(path)

    assert [i.kind for i in issues] == [IssueKind.INVALID_SPAN]


def test_missing_path_raises(tmp_path: Path) -> None:
    with pytest.raises(TraceFileError, match=r"traces\.path"):
        load_spans(tmp_path / "missing")


def test_empty_folder_raises(tmp_path: Path) -> None:
    with pytest.raises(TraceFileError, match=r"No trace files found"):
        load_spans(tmp_path)


def test_folder_with_only_hidden_files_raises(tmp_path: Path) -> None:
    (tmp_path / ".DS_Store").write_text("x")

    with pytest.raises(TraceFileError, match=r"No trace files found"):
        load_spans(tmp_path)
