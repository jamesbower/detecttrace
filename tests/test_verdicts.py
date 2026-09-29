import csv
from pathlib import Path

import pytest

from detecttrace.model import IssueKind, VerdictRow
from detecttrace.verdicts import VerdictFileError, read_verdicts


def write_csv(tmp_path: Path, text: str) -> Path:
    path = tmp_path / "verdicts.csv"
    path.write_text(text, encoding="utf-8")
    return path


def test_reads_rows_and_ignores_extra_columns(tmp_path: Path) -> None:
    path = write_csv(tmp_path, "case_id,alert_class,verdict,analyst\nDT-1,impossible_travel,TP,a\n")

    rows, _ = read_verdicts(path)

    assert rows == [VerdictRow("DT-1", "impossible_travel", "TP", 2)]


def test_closed_at_is_optional(tmp_path: Path) -> None:
    path = write_csv(tmp_path, "case_id,alert_class,verdict\nDT-1,oauth_consent,Benign\n")

    rows, _ = read_verdicts(path)

    assert rows[0].label == "Benign"


def test_reads_excel_csv_with_byte_order_mark_and_crlf(tmp_path: Path) -> None:
    path = tmp_path / "verdicts.csv"
    path.write_bytes("﻿case_id,alert_class,verdict\r\nDT-1,impossible_travel,FP\r\n".encode())

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


def test_field_over_the_csv_size_limit_raises_csv_error(tmp_path: Path) -> None:
    oversized = "x" * (csv.field_size_limit() + 1)
    path = write_csv(tmp_path, f"case_id,alert_class,verdict\nDT-1,{oversized},TP\n")

    with pytest.raises(VerdictFileError, match="could not be read as CSV"):
        read_verdicts(path)


def test_directory_path_raises_could_not_be_opened(tmp_path: Path) -> None:
    with pytest.raises(VerdictFileError, match="could not be opened"):
        read_verdicts(tmp_path)
