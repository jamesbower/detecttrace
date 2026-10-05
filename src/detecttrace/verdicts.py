"""Read the analyst verdict CSV.

A malformed row is reported and skipped. The file is unusable only when it is missing,
unreadable, not UTF-8, lacks or repeats a required column, or holds a runaway quoted value
the reader cannot recover from.
"""

import csv
from pathlib import Path
from typing import TextIO

from detecttrace.model import (
    REPORT_KEY_LENGTH,
    SHORTENED_DETAIL,
    InputFileError,
    Issue,
    IssueKind,
    VerdictRow,
    to_short_label,
)

REQUIRED_COLUMNS = ("case_id", "alert_class", "verdict")
# The csv module's default of 128 KiB would skip a row whose long case ID a trace also carries
# (both sides are shortened alike and still join); a longer field is surely a broken row.
MAX_FIELD_CHARACTERS = 4 << 20
# How to fix a missing verdict file, as check says it; init names its --verdicts option instead.
PATH_HINT = "Check verdicts.path in detecttrace.yaml."


class VerdictFileError(InputFileError):
    """The verdict file cannot be used at all: missing, unreadable, or its required columns are missing or repeated."""


def read_verdicts(
    path: Path, *, path_hint: str = PATH_HINT
) -> tuple[list[VerdictRow], list[Issue]]:
    """Read every verdict row. Rows that share a case ID are all returned; the join resolves them.

    `path_hint` ends the error for a missing file.
    """
    try:
        with path.open(encoding="utf-8-sig", newline="") as handle:
            return read_verdict_rows(handle, path.name, error_subject=str(path))
    except FileNotFoundError as error:
        raise VerdictFileError(f"Verdict file not found: {path}. {path_hint}") from error
    except UnicodeDecodeError as error:
        raise VerdictFileError(f"{path} is not UTF-8. Save it as UTF-8 CSV.") from error
    except OSError as error:
        raise VerdictFileError(
            f"Verdict file {path} could not be opened: {error.strerror or error}."
        ) from error


def read_verdict_rows(
    handle: TextIO, subject: str, *, error_subject: str | None = None
) -> tuple[list[VerdictRow], list[Issue]]:
    """Read verdict rows from open CSV text; the file reader and the verdict API share these rules.

    Issues name `subject` and the line, such as "verdicts.csv:7". A fatal CSV problem raises
    VerdictFileError naming `error_subject`, which is `subject` unless given (a file's full path).
    """
    # The limit is process-wide, so it is put back for any other csv user.
    previous_limit = csv.field_size_limit(MAX_FIELD_CHARACTERS)
    try:
        return _read_rows(handle, subject, error_subject or subject)
    finally:
        csv.field_size_limit(previous_limit)


def _read_rows(
    handle: TextIO, subject: str, error_subject: str
) -> tuple[list[VerdictRow], list[Issue]]:
    reader = csv.reader(handle, strict=True)
    try:
        header = [name.strip() for name in next((row for row in reader if row), [])]
    except csv.Error as error:
        raise VerdictFileError(
            f"{error_subject} header could not be read as CSV: {error}."
        ) from error
    missing = [column for column in REQUIRED_COLUMNS if column not in header]
    if missing:
        raise VerdictFileError(
            f"{error_subject} is missing the column(s) {', '.join(missing)}. "
            f"Required columns: {', '.join(REQUIRED_COLUMNS)}."
        )
    repeated = [column for column in REQUIRED_COLUMNS if header.count(column) > 1]
    if repeated:
        raise VerdictFileError(
            f"{error_subject} repeats the column(s) {', '.join(repeated)} in its header. Keep one of each."
        )
    positions = [header.index(column) for column in REQUIRED_COLUMNS]
    rows: list[VerdictRow] = []
    issues: list[Issue] = []
    reported_long: set[tuple[str, str]] = set()
    while True:
        previous_line = reader.line_num
        try:
            fields = next(reader)
        except StopIteration:
            break
        except csv.Error as error:
            line_number = reader.line_num
            if line_number - previous_line == 1:
                # On one physical line the reader drops the rest of it and resumes cleanly.
                issues.append(_invalid_row(subject, line_number, str(error)))
                continue
            # A quoted value left open to the end of the file swallowed everything after it,
            # so nothing is left to misread.
            if not handle.read(1):
                issues.append(_merged_rows(subject, previous_line, line_number))
                continue
            # Mid-file the reader would resume inside a quoted value, so what follows
            # cannot be trusted.
            raise VerdictFileError(
                f"{error_subject} could not be read as CSV at lines {previous_line + 1}"
                f"\u2013{line_number}: {error}. Check for an unbalanced quote."
            ) from error
        if not fields:
            continue
        line_number = reader.line_num
        values = [
            fields[position].strip() if position < len(fields) else "" for position in positions
        ]
        # A line break in a required value means a quote ran on into the next rows; other
        # columns such as notes may hold quoted multi-line text.
        if any("\n" in value or "\r" in value for value in values):
            issues.append(_merged_rows(subject, previous_line, line_number))
            continue
        if len(fields) > len(header):
            issues.append(
                _invalid_row(
                    subject,
                    line_number,
                    f"{len(fields) - len(header)} more fields than the header; "
                    "quote values that contain commas",
                )
            )
            continue
        case_id, alert_class, label = values
        if not (case_id and alert_class and label):
            issues.append(
                _invalid_row(
                    subject, line_number, "case_id, alert_class, and verdict must all have a value"
                )
            )
            continue
        where = f"{subject}:{line_number}"
        rows.append(
            VerdictRow(
                shorten_value(case_id, "case_id", where, issues, reported_long),
                shorten_value(alert_class, "alert_class", where, issues, reported_long),
                shorten_value(label, "verdict", where, issues, reported_long),
                line_number,
            )
        )
    return rows, issues


def shorten_value(
    value: str, column: str, subject: str, issues: list[Issue], reported: set[tuple[str, str]]
) -> str:
    short = to_short_label(value)
    if short is not value and (column, value[:REPORT_KEY_LENGTH]) not in reported:
        reported.add((column, value[:REPORT_KEY_LENGTH]))
        issues.append(
            Issue(IssueKind.LONG_VERDICT_VALUE, subject, f"{column} is {SHORTENED_DETAIL}")
        )
    return short


def _invalid_row(subject: str, line_number: int, detail: str) -> Issue:
    return Issue(IssueKind.INVALID_VERDICT_ROW, f"{subject}:{line_number}", detail)


def _merged_rows(subject: str, previous_line: int, line_number: int) -> Issue:
    return _invalid_row(
        subject,
        line_number,
        f"unbalanced quote; lines {previous_line + 1}\u2013{line_number} were read as one row",
    )
