import csv
from pathlib import Path

import pytest

from detecttrace.model import IssueKind, VerdictRow
from detecttrace.verdicts import MAX_FIELD_CHARACTERS, VerdictFileError, read_verdicts


def write_csv(tmp_path: Path, text: str) -> Path:
    path = tmp_path / "verdicts.csv"
    path.write_text(text, encoding="utf-8")
    return path


def test_reads_rows_and_ignores_extra_columns(tmp_path: Path) -> None:
    path = write_csv(tmp_path, "case_id,alert_class,verdict,analyst\nDT-1,impossible_travel,TP,a\n")

    rows, _ = read_verdicts(path)

    assert rows == [VerdictRow("DT-1", "impossible_travel", "TP", 2)]


def test_row_with_a_closed_at_time_zone_offset_is_read(tmp_path: Path) -> None:
    path = write_csv(
        tmp_path,
        "case_id,alert_class,verdict,closed_at\nDT-1,oauth_consent,Benign,2026-03-01T09:30:00+02:00\n",
    )

    rows, _ = read_verdicts(path)

    assert rows == [VerdictRow("DT-1", "oauth_consent", "Benign", 2)]


def test_reads_excel_csv_with_byte_order_mark_and_crlf(tmp_path: Path) -> None:
    path = tmp_path / "verdicts.csv"
    path.write_bytes("\ufeffcase_id,alert_class,verdict\r\nDT-1,impossible_travel,FP\r\n".encode())

    rows, _ = read_verdicts(path)

    assert rows == [VerdictRow("DT-1", "impossible_travel", "FP", 2)]


def test_missing_required_column_raises_with_its_name(tmp_path: Path) -> None:
    path = write_csv(tmp_path, "case_id,alert_class\nDT-1,impossible_travel\n")

    with pytest.raises(VerdictFileError, match="verdict"):
        read_verdicts(path)


def test_missing_file_raises(tmp_path: Path) -> None:
    with pytest.raises(VerdictFileError, match="not found"):
        read_verdicts(tmp_path / "missing.csv")


def test_non_utf8_file_raises(tmp_path: Path) -> None:
    path = tmp_path / "verdicts.csv"
    path.write_bytes(b"case_id,alert_class,verdict\nDT-1,caf\xe9,TP\n")

    with pytest.raises(VerdictFileError, match="UTF-8"):
        read_verdicts(path)


def test_rows_sharing_a_case_id_are_all_returned(tmp_path: Path) -> None:
    path = write_csv(tmp_path, "case_id,alert_class,verdict\nDT-1,x,TP\nDT-1,x,FP\n")

    rows, _ = read_verdicts(path)

    assert [row.label for row in rows] == ["TP", "FP"]


def test_row_with_an_empty_required_value_is_reported(tmp_path: Path) -> None:
    path = write_csv(tmp_path, "case_id,alert_class,verdict\nDT-1,,TP\n")

    _, issues = read_verdicts(path)

    assert [i.kind for i in issues] == [IssueKind.INVALID_VERDICT_ROW]


def test_row_with_a_whitespace_only_required_value_is_reported(tmp_path: Path) -> None:
    path = write_csv(tmp_path, "case_id,alert_class,verdict\nDT-1,x,  \n")

    _, issues = read_verdicts(path)

    assert [i.kind for i in issues] == [IssueKind.INVALID_VERDICT_ROW]


def test_invalid_row_issue_subject_is_the_file_name_and_line(tmp_path: Path) -> None:
    path = write_csv(tmp_path, "case_id,alert_class,verdict\nDT-1,,TP\n")

    _, issues = read_verdicts(path)

    assert [i.subject for i in issues] == ["verdicts.csv:2"]


def test_row_with_a_field_over_the_csv_size_limit_is_reported(tmp_path: Path) -> None:
    oversized = "x" * (MAX_FIELD_CHARACTERS + 1)
    path = write_csv(tmp_path, f"case_id,alert_class,verdict\nDT-1,{oversized},TP\nDT-2,x,FP\n")

    _, issues = read_verdicts(path)

    assert [(i.kind, i.subject) for i in issues] == [
        (IssueKind.INVALID_VERDICT_ROW, "verdicts.csv:2")
    ]


def test_rows_after_a_field_over_the_csv_size_limit_are_still_read(tmp_path: Path) -> None:
    oversized = "x" * (MAX_FIELD_CHARACTERS + 1)
    path = write_csv(tmp_path, f"case_id,alert_class,verdict\nDT-1,{oversized},TP\nDT-2,x,FP\n")

    rows, _ = read_verdicts(path)

    assert rows == [VerdictRow("DT-2", "x", "FP", 3)]


def test_quoted_value_over_the_csv_size_limit_spanning_lines_raises(tmp_path: Path) -> None:
    multiline = "x\n" * (MAX_FIELD_CHARACTERS // 2 + 1)
    path = write_csv(tmp_path, f'case_id,alert_class,verdict\nDT-1,"{multiline}",TP\n')

    with pytest.raises(VerdictFileError, match="unbalanced quote"):
        read_verdicts(path)


def test_unbalanced_quote_is_reported_with_the_merged_lines(tmp_path: Path) -> None:
    path = write_csv(
        tmp_path, 'case_id,alert_class,verdict\nDT-1,"Phish,TP\nDT-2,Mal,FP\nDT-3,Mal,TP\n'
    )

    _, issues = read_verdicts(path)

    assert [(i.kind, i.subject, i.detail) for i in issues] == [
        (
            IssueKind.INVALID_VERDICT_ROW,
            "verdicts.csv:4",
            "unbalanced quote; lines 2\u20134 were read as one row",
        )
    ]


def test_row_merged_by_an_unbalanced_quote_is_not_returned(tmp_path: Path) -> None:
    path = write_csv(tmp_path, 'case_id,alert_class,verdict\nDT-1,"Phish,TP\nDT-2,Mal",FP\n')

    rows, _ = read_verdicts(path)

    assert rows == []


def test_unbalanced_quote_in_an_extra_column_is_reported_with_the_merged_lines(
    tmp_path: Path,
) -> None:
    path = write_csv(tmp_path, 'case_id,alert_class,verdict,notes\nDT-1,x,TP,"odd\nDT-2,y,FP,\n')

    _, issues = read_verdicts(path)

    assert [(i.kind, i.subject, i.detail) for i in issues] == [
        (
            IssueKind.INVALID_VERDICT_ROW,
            "verdicts.csv:3",
            "unbalanced quote; lines 2\u20133 were read as one row",
        )
    ]


def test_quoted_multiline_value_in_an_extra_column_is_read(tmp_path: Path) -> None:
    path = write_csv(
        tmp_path, 'case_id,alert_class,verdict,notes\nDT-1,x,TP,"line one\nline two"\n'
    )

    rows, _ = read_verdicts(path)

    assert [row.case_id for row in rows] == ["DT-1"]


def test_oversized_field_after_blank_lines_is_reported_at_its_own_line(tmp_path: Path) -> None:
    oversized = "x" * (MAX_FIELD_CHARACTERS + 1)
    path = write_csv(
        tmp_path,
        f"case_id,alert_class,verdict\nDT-1,x,TP\n\n\nDT-2,{oversized},FP\nDT-3,y,TP\n",
    )

    _, issues = read_verdicts(path)

    assert [(i.kind, i.subject) for i in issues] == [
        (IssueKind.INVALID_VERDICT_ROW, "verdicts.csv:5")
    ]


def test_rows_around_an_oversized_field_after_blank_lines_are_read(tmp_path: Path) -> None:
    oversized = "x" * (MAX_FIELD_CHARACTERS + 1)
    path = write_csv(
        tmp_path,
        f"case_id,alert_class,verdict\nDT-1,x,TP\n\n\nDT-2,{oversized},FP\nDT-3,y,TP\n",
    )

    rows, _ = read_verdicts(path)

    assert [row.case_id for row in rows] == ["DT-1", "DT-3"]


def test_blank_lines_between_rows_are_not_reported(tmp_path: Path) -> None:
    path = write_csv(tmp_path, "case_id,alert_class,verdict\nDT-1,x,TP\n\n\nDT-2,y,FP\n")

    _, issues = read_verdicts(path)

    assert issues == []


def test_row_with_more_fields_than_the_header_is_reported(tmp_path: Path) -> None:
    path = write_csv(tmp_path, "case_id,alert_class,verdict\nDT-1,Phishing, Malware,TP\n")

    _, issues = read_verdicts(path)

    assert [(i.kind, i.detail) for i in issues] == [
        (
            IssueKind.INVALID_VERDICT_ROW,
            "1 more fields than the header; quote values that contain commas",
        )
    ]


def test_row_with_more_fields_than_the_header_is_not_returned(tmp_path: Path) -> None:
    path = write_csv(tmp_path, "case_id,alert_class,verdict\nDT-1,Phishing, Malware,TP\n")

    rows, _ = read_verdicts(path)

    assert rows == []


def test_duplicate_required_header_column_raises_with_its_name(tmp_path: Path) -> None:
    path = write_csv(tmp_path, "case_id,alert_class,verdict, verdict\nDT-1,x,TP,FP\n")

    with pytest.raises(VerdictFileError, match="repeats the column\\(s\\) verdict"):
        read_verdicts(path)


def test_directory_path_raises_could_not_be_opened(tmp_path: Path) -> None:
    with pytest.raises(VerdictFileError, match="could not be opened"):
        read_verdicts(tmp_path)


# Very long values

LONG = "x" * 1000
SHORTENED = "x" * 199 + "…"


@pytest.mark.parametrize(
    ("line", "expected"),
    [
        (f"{LONG},impossible_travel,TP", VerdictRow(SHORTENED, "impossible_travel", "TP", 2)),
        (f"DT-1,{LONG},TP", VerdictRow("DT-1", SHORTENED, "TP", 2)),
        (f"DT-1,impossible_travel,{LONG}", VerdictRow("DT-1", "impossible_travel", SHORTENED, 2)),
    ],
    ids=["case_id", "alert_class", "verdict"],
)
def test_a_value_longer_than_200_characters_is_shortened(
    tmp_path: Path, line: str, expected: VerdictRow
) -> None:
    rows, _ = read_verdicts(write_csv(tmp_path, f"case_id,alert_class,verdict\n{line}\n"))
    assert rows == [expected]


def test_a_shortened_value_is_reported(tmp_path: Path) -> None:
    path = write_csv(tmp_path, f"case_id,alert_class,verdict\n{LONG},impossible_travel,TP\n")
    _, issues = read_verdicts(path)
    assert [(issue.kind, issue.subject, issue.detail) for issue in issues] == [
        (
            IssueKind.LONG_VERDICT_VALUE,
            "verdicts.csv:2",
            "case_id is longer than 200 characters; shortened",
        )
    ]


def test_long_values_that_start_alike_are_reported_once(tmp_path: Path) -> None:
    path = write_csv(tmp_path, f"case_id,alert_class,verdict\n{LONG}a,c,TP\n{LONG}b,c,TP\n")
    _, issues = read_verdicts(path)
    assert len(issues) == 1


def test_a_one_megabyte_value_is_read(tmp_path: Path) -> None:
    huge = "x" * (1 << 20)
    path = write_csv(tmp_path, f"case_id,alert_class,verdict\n{huge},c,TP\n")
    rows, _ = read_verdicts(path)
    assert [row.case_id for row in rows] == [SHORTENED]


def test_reading_leaves_the_csv_field_limit_as_it_was(tmp_path: Path) -> None:
    before = csv.field_size_limit()
    read_verdicts(write_csv(tmp_path, "case_id,alert_class,verdict\nDT-1,c,TP\n"))
    assert csv.field_size_limit() == before
