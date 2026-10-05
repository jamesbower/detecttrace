import csv
import io
import json
import threading
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import pytest

from detecttrace.config import Config
from detecttrace.model import MAX_LABEL_LENGTH, IssueKind, Verdict, VerdictRow
from detecttrace.serve.verdict_api import (
    MAX_ROWS,
    InvalidBody,
    RejectedRow,
    TooManyRows,
    UnsupportedMediaType,
    parse_verdicts_body,
)
from detecttrace.verdicts import (
    MAX_FIELD_CHARACTERS,
    VerdictFileError,
    VerdictRowLimitError,
    read_verdict_rows,
    read_verdicts,
)

CONFIG = Config(label_map={"TP": Verdict.TRUE_POSITIVE, "FP": Verdict.FALSE_POSITIVE})
JSON = "application/json"
CSV = "text/csv"
CSV_HEADER = "case_id,alert_class,verdict\n"
# The csv module's own default; asserting against it, not a value read mid-session, means a
# limit leaked by an earlier test cannot hide a leak here.
CSV_DEFAULT_FIELD_LIMIT = 128 * 1024


def json_body(*items: object) -> bytes:
    return json.dumps({"verdicts": list(items)}).encode("utf-8")


def item(case_id: str = "DT-1", verdict: str = "TP") -> dict[str, str]:
    return {"case_id": case_id, "alert_class": "impossible_travel", "verdict": verdict}


@pytest.mark.parametrize("body", [b"[]", b'"text"', b"not json", b"{}", b'{"verdicts": {}}'])
def test_json_that_is_not_an_object_holding_a_verdicts_list_is_invalid(body: bytes) -> None:
    with pytest.raises(InvalidBody):
        parse_verdicts_body(body, JSON, CONFIG)


def test_empty_verdicts_list_gives_no_rows_and_no_rejections() -> None:
    assert parse_verdicts_body(json_body(), JSON, CONFIG) == ([], [], [])


def test_item_missing_a_field_is_rejected_with_its_position() -> None:
    _, rejected, _ = parse_verdicts_body(json_body(item(), {"case_id": "DT-2"}), JSON, CONFIG)

    assert rejected == [RejectedRow("verdicts[1]", "alert_class is required")]


def test_number_as_case_id_is_rejected() -> None:
    bad = {**item(), "case_id": 7}

    _, rejected, _ = parse_verdicts_body(json_body(bad), JSON, CONFIG)

    assert rejected == [RejectedRow("verdicts[0]", "case_id must be a string")]


def test_item_that_is_not_an_object_is_rejected() -> None:
    _, rejected, _ = parse_verdicts_body(json_body("DT-1"), JSON, CONFIG)

    assert rejected == [RejectedRow("verdicts[0]", "must be an object")]


def test_blank_value_is_rejected_after_stripping() -> None:
    _, rejected, _ = parse_verdicts_body(json_body(item(case_id="  ")), JSON, CONFIG)

    assert rejected == [RejectedRow("verdicts[0]", "case_id must have a value")]


@pytest.mark.parametrize("line_break", ["\n", "\r"])
def test_line_break_in_a_value_is_rejected(line_break: str) -> None:
    case_id = f"DT{line_break}1"
    _, rejected, _ = parse_verdicts_body(json_body(item(case_id=case_id)), JSON, CONFIG)

    assert rejected == [RejectedRow("verdicts[0]", "case_id must not contain a line break")]


def test_extra_keys_are_ignored() -> None:
    rows, _, _ = parse_verdicts_body(json_body({**item(), "analyst": "a"}), JSON, CONFIG)

    assert rows == [VerdictRow("DT-1", "impossible_travel", "TP", 0)]


def test_values_are_stripped() -> None:
    body = json_body(item(case_id=" DT-1 ", verdict=" TP "))

    rows, _, _ = parse_verdicts_body(body, JSON, CONFIG)

    assert rows == [VerdictRow("DT-1", "impossible_travel", "TP", 0)]


def test_csv_with_an_unbalanced_quote_mid_file_is_invalid_with_the_file_readers_message(
    tmp_path: Path,
) -> None:
    # A quoted value past the field size limit is the unrecoverable mid-file case.
    runaway = "x\n" * (MAX_FIELD_CHARACTERS // 2 + 1)
    text = f'{CSV_HEADER}DT-1,"{runaway}",TP\n'
    path = tmp_path / "verdicts.csv"
    # newline="" keeps the bytes the API receives; Windows would otherwise write CRLF.
    path.write_text(text, encoding="utf-8", newline="")
    with pytest.raises(VerdictFileError) as from_file:
        read_verdicts(path)

    with pytest.raises(InvalidBody) as from_api:
        parse_verdicts_body(text.encode("utf-8"), CSV, CONFIG)

    assert str(from_api.value) == str(from_file.value).replace(str(path), "verdict API")


def test_csv_header_without_verdict_is_invalid() -> None:
    with pytest.raises(InvalidBody, match="missing the column"):
        parse_verdicts_body(b"case_id,alert_class\nDT-1,a\n", CSV, CONFIG)


def test_csv_row_without_a_value_is_rejected_at_its_line() -> None:
    body = (CSV_HEADER + "DT-1,a,TP\nDT-2,a,\n").encode()

    _, rejected, _ = parse_verdicts_body(body, CSV, CONFIG)

    assert rejected == [
        RejectedRow("line 3", "case_id, alert_class, and verdict must all have a value")
    ]


def test_more_than_the_row_limit_is_too_many_rows() -> None:
    body = json_body(*[item(case_id=f"DT-{n}") for n in range(MAX_ROWS + 1)])

    with pytest.raises(TooManyRows):
        parse_verdicts_body(body, JSON, CONFIG)


def test_csv_rows_over_the_limit_are_too_many_rows() -> None:
    lines = "".join(f"DT-{n},a,TP\n" for n in range(MAX_ROWS + 1))

    with pytest.raises(TooManyRows):
        parse_verdicts_body((CSV_HEADER + lines).encode(), CSV, CONFIG)


def test_rows_at_the_limit_are_accepted() -> None:
    body = json_body(*[item(case_id=f"DT-{n}") for n in range(MAX_ROWS)])

    rows, _, _ = parse_verdicts_body(body, JSON, CONFIG)

    assert len(rows) == MAX_ROWS


def test_over_long_case_id_is_accepted_shortened() -> None:
    rows, _, _ = parse_verdicts_body(json_body(item(case_id="x" * 500)), JSON, CONFIG)

    assert len(rows[0].case_id) == MAX_LABEL_LENGTH


def test_shortening_is_returned_as_an_issue() -> None:
    _, _, issues = parse_verdicts_body(json_body(item(case_id="x" * 500)), JSON, CONFIG)

    assert [(issue.kind, issue.subject) for issue in issues] == [
        (IssueKind.LONG_VERDICT_VALUE, "verdict API:verdicts[0]")
    ]


def test_unmappable_label_is_rejected_with_the_reason() -> None:
    _, rejected, _ = parse_verdicts_body(json_body(item(verdict="maybe")), JSON, CONFIG)

    assert rejected == [RejectedRow("verdicts[0]", "verdict label 'maybe' is not in label_map")]


def test_unmappable_over_long_label_is_named_shortened() -> None:
    _, rejected, _ = parse_verdicts_body(json_body(item(verdict="m" * 500)), JSON, CONFIG)

    assert len(rejected[0].reason) < MAX_LABEL_LENGTH + 60


def test_mixed_rows_split_into_accepted_and_rejected() -> None:
    body = json_body(item("DT-1", "TP"), item("DT-2", "maybe"), item("DT-3", "fp"))

    rows, rejected, _ = parse_verdicts_body(body, JSON, CONFIG)

    assert ([row.case_id for row in rows], [r.where for r in rejected]) == (
        ["DT-1", "DT-3"],
        ["verdicts[1]"],
    )


def test_later_row_with_a_repeated_case_id_is_rejected() -> None:
    body = json_body(item("DT-1", "TP"), item("DT-1", "FP"))

    rows, rejected, _ = parse_verdicts_body(body, JSON, CONFIG)

    assert (rows, rejected) == (
        [VerdictRow("DT-1", "impossible_travel", "TP", 0)],
        [RejectedRow("verdicts[1]", "duplicate case_id in this request")],
    )


def test_repeated_case_id_in_csv_is_rejected_at_its_line() -> None:
    body = (CSV_HEADER + "DT-1,a,TP\nDT-1,a,FP\n").encode()

    _, rejected, _ = parse_verdicts_body(body, CSV, CONFIG)

    assert rejected == [RejectedRow("line 3", "duplicate case_id in this request")]


@pytest.mark.parametrize("content_type", ["text/csv; charset=utf-8", "TEXT/CSV;charset=UTF-8"])
def test_csv_content_type_with_utf8_charset_is_accepted(content_type: str) -> None:
    body = (CSV_HEADER + "DT-1,a,TP\n").encode()

    rows, _, _ = parse_verdicts_body(body, content_type, CONFIG)

    assert len(rows) == 1


def test_csv_byte_order_mark_is_accepted() -> None:
    body = ("﻿" + CSV_HEADER + "DT-1,a,TP\n").encode()

    rows, _, _ = parse_verdicts_body(body, CSV, CONFIG)

    assert len(rows) == 1


@pytest.mark.parametrize(
    "content_type", ["application/xml", None, "", "text/csv; charset=latin-1", "text/plain"]
)
def test_other_content_types_are_unsupported(content_type: str | None) -> None:
    with pytest.raises(UnsupportedMediaType):
        parse_verdicts_body(b"", content_type, CONFIG)


@pytest.mark.parametrize("content_type", [JSON, CSV])
def test_bytes_that_are_not_utf8_are_invalid(content_type: str) -> None:
    with pytest.raises(InvalidBody):
        parse_verdicts_body(b'{"verdicts": ["DT-\xe9"]}', content_type, CONFIG)


def test_json_and_csv_forms_of_the_same_rows_give_equal_rows() -> None:
    rows = [("DT-1", "a", "TP"), ("DT-2", "b", "fp"), ("DT-3", "c", "TP")]
    from_json = json_body(*[{"case_id": c, "alert_class": a, "verdict": v} for c, a, v in rows])
    from_csv = (CSV_HEADER + "".join(f"{c},{a},{v}\n" for c, a, v in rows)).encode()

    json_rows, _, _ = parse_verdicts_body(from_json, JSON, CONFIG)
    csv_rows, _, _ = parse_verdicts_body(from_csv, CSV, CONFIG)

    assert json_rows == csv_rows


def test_csv_shortening_note_names_the_line_like_a_rejection_does() -> None:
    body = (CSV_HEADER + f"DT-1,a,TP\n{'x' * 500},a,TP\n").encode()

    _, _, issues = parse_verdicts_body(body, CSV, CONFIG)

    assert [(issue.kind, issue.subject) for issue in issues] == [
        (IssueKind.LONG_VERDICT_VALUE, "verdict API:line 3")
    ]


def test_json_over_long_alert_class_is_shortened() -> None:
    bad = {**item(), "alert_class": "c" * 500}

    rows, _, _ = parse_verdicts_body(json_body(bad), JSON, CONFIG)

    assert len(rows[0].alert_class) == MAX_LABEL_LENGTH


def test_json_with_a_byte_order_mark_is_invalid() -> None:
    with pytest.raises(InvalidBody):
        parse_verdicts_body(b"\xef\xbb\xbf" + json_body(item()), JSON, CONFIG)


def test_more_than_the_row_limit_is_too_many_rows_even_when_labels_are_bad() -> None:
    body = json_body(*[item(case_id=f"DT-{n}", verdict="maybe") for n in range(MAX_ROWS + 1)])

    with pytest.raises(TooManyRows):
        parse_verdicts_body(body, JSON, CONFIG)


def test_csv_invalid_rows_count_toward_the_row_limit() -> None:
    lines = "".join(f"DT-{n},a,\n" for n in range(MAX_ROWS + 1))

    with pytest.raises(TooManyRows):
        parse_verdicts_body((CSV_HEADER + lines).encode(), CSV, CONFIG)


def test_rejections_come_back_in_body_order() -> None:
    body = json_body(item("DT-1", "maybe"), {"case_id": "DT-2"})

    _, rejected, _ = parse_verdicts_body(body, JSON, CONFIG)

    assert [r.where for r in rejected] == ["verdicts[0]", "verdicts[1]"]


def test_label_with_terminal_escapes_and_bidi_override_is_echoed_printable() -> None:
    body = json_body(item(verdict="\x1b]52;c;aGk=\x07\u202eevil"))

    _, rejected, _ = parse_verdicts_body(body, JSON, CONFIG)

    assert [c for c in rejected[0].reason if ord(c) < 0x20 or c == "\u202e"] == []


class CountingLines(io.StringIO):
    def __init__(self, text: str) -> None:
        super().__init__(text)
        self.read_count = 0

    def __next__(self) -> str:
        self.read_count += 1
        return super().__next__()


def test_reading_stops_soon_after_the_row_limit_is_passed() -> None:
    lines = CountingLines(CSV_HEADER + "DT-1,a,TP\n" * 700_000)

    with pytest.raises(VerdictRowLimitError):
        read_verdict_rows(lines, "verdict API", max_rows=10)

    assert lines.read_count <= 13


def test_csv_field_limit_is_back_to_the_default_after_a_fatal_error() -> None:
    with pytest.raises(InvalidBody):
        parse_verdicts_body(b"case_id,alert_class\nDT-1,a\n", CSV, CONFIG)

    assert csv.field_size_limit() == CSV_DEFAULT_FIELD_LIMIT


def test_parsing_a_large_field_in_many_threads_neither_rejects_it_nor_leaks_the_limit() -> None:
    body = (CSV_HEADER + f'DT-1,"{"x" * 300_000}",TP\n').encode()
    start = threading.Barrier(8)

    def parse_repeatedly() -> int:
        start.wait()
        return sum(len(parse_verdicts_body(body, CSV, CONFIG)[0]) for _ in range(40))

    with ThreadPoolExecutor(max_workers=8) as pool:
        accepted = sum(pool.map(lambda _: parse_repeatedly(), range(8)))

    assert (accepted, csv.field_size_limit()) == (8 * 40, CSV_DEFAULT_FIELD_LIMIT)


def test_csv_rows_at_the_limit_with_a_shortened_value_are_all_accepted() -> None:
    long_id = f"{'x' * 500},a,TP\n"
    lines = long_id + "".join(f"DT-{n},a,TP\n" for n in range(MAX_ROWS - 1))

    rows, _, _ = parse_verdicts_body((CSV_HEADER + lines).encode(), CSV, CONFIG)

    assert len(rows) == MAX_ROWS
