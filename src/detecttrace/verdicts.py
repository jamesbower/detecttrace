"""Read the analyst verdict CSV (PRD §7.2, Appendix B)."""

# csv.DictReader is not subscriptable at runtime on 3.11.
from __future__ import annotations

import csv
from pathlib import Path
from typing import cast

from detecttrace.model import Issue, IssueKind, VerdictRow

REQUIRED_COLUMNS = ("case_id", "alert_class", "verdict")


class VerdictFileError(Exception):
    """The verdict file cannot be used at all: missing, unreadable, or its required columns are missing or repeated."""


def read_verdicts(path: Path) -> tuple[list[VerdictRow], list[Issue]]:
    """Read every verdict row. Rows that share a case ID are resolved by the join (decision D5)."""
    try:
        with path.open(encoding="utf-8-sig", newline="") as handle:
            return _read_rows(csv.DictReader(handle), path)
    except FileNotFoundError as error:
        raise VerdictFileError(
            f"Verdict file not found: {path}. Check verdicts.path in detecttrace.yaml."
        ) from error
    except UnicodeDecodeError as error:
        raise VerdictFileError(f"{path} is not UTF-8. Save it as UTF-8 CSV.") from error
    except OSError as error:
        raise VerdictFileError(
            f"Verdict file {path} could not be opened: {error.strerror or error}."
        ) from error


def _read_rows(reader: csv.DictReader[str], path: Path) -> tuple[list[VerdictRow], list[Issue]]:
    columns = [name.strip() for name in reader.fieldnames or []]
    missing = [column for column in REQUIRED_COLUMNS if column not in columns]
    if missing:
        raise VerdictFileError(
            f"{path} is missing the column(s) {', '.join(missing)}. "
            f"Required columns: {', '.join(REQUIRED_COLUMNS)}."
        )
    repeated = [column for column in REQUIRED_COLUMNS if columns.count(column) > 1]
    if repeated:
        raise VerdictFileError(
            f"{path} repeats the column(s) {', '.join(repeated)} in its header. Keep one of each."
        )
    reader.fieldnames = columns
    rows: list[VerdictRow] = []
    issues: list[Issue] = []
    while True:
        # DictReader.line_num goes stale when the reader raises; the inner reader's does not.
        previous_line = reader.reader.line_num
        try:
            row = next(reader)
        except StopIteration:
            break
        except csv.Error as error:
            line_number = reader.reader.line_num
            # On one physical line the reader drops the rest of it and resumes cleanly. Across
            # lines it resumes inside a quoted value, so what follows cannot be trusted.
            if line_number - previous_line > 1:
                raise VerdictFileError(
                    f"{path} could not be read as CSV at lines {previous_line + 1}"
                    f"\u2013{line_number}: {error}. Check for an unbalanced quote."
                ) from error
            issues.append(_invalid_row(path, line_number, str(error)))
            continue
        line_number = reader.reader.line_num
        # DictReader puts fields beyond the header in a list under the None key.
        extra_fields = cast("list[str]", row.get(None) or [])
        values = [value for value in row.values() if isinstance(value, str)] + extra_fields
        line_breaks = sum(_count_line_breaks(value) for value in values)
        if line_breaks:
            # A quote left open at end of file also holds the last line's own line ending.
            first_line = max(previous_line + 1, line_number - line_breaks)
            issues.append(
                _invalid_row(
                    path,
                    line_number,
                    f"unbalanced quote; lines {first_line}\u2013{line_number} were read as one row",
                )
            )
            continue
        if extra_fields:
            issues.append(
                _invalid_row(
                    path,
                    line_number,
                    f"{len(extra_fields)} more fields than the header; "
                    "quote values that contain commas",
                )
            )
            continue
        case_id = (row.get("case_id") or "").strip()
        alert_class = (row.get("alert_class") or "").strip()
        label = (row.get("verdict") or "").strip()
        if not (case_id and alert_class and label):
            issues.append(
                _invalid_row(
                    path, line_number, "case_id, alert_class, and verdict must all have a value"
                )
            )
            continue
        rows.append(VerdictRow(case_id, alert_class, label, line_number))
    return rows, issues


def _invalid_row(path: Path, line_number: int, detail: str) -> Issue:
    return Issue(IssueKind.INVALID_VERDICT_ROW, f"{path.name}:{line_number}", detail)


def _count_line_breaks(value: str) -> int:
    # Counts line endings the way a newline="" file splits lines, so CRLF is one break.
    return value.count("\n") + value.count("\r") - value.count("\r\n")
