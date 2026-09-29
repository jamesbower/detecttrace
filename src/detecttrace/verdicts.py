"""Read the analyst verdict CSV (PRD §7.2, Appendix B)."""

# csv.DictReader is not subscriptable at runtime on 3.11.
from __future__ import annotations

import csv
from pathlib import Path

from detecttrace.model import Issue, IssueKind, VerdictRow

REQUIRED_COLUMNS = ("case_id", "alert_class", "verdict")


class VerdictFileError(Exception):
    """The verdict file cannot be used at all: missing, unreadable, or without required columns."""


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
    reader.fieldnames = columns
    rows: list[VerdictRow] = []
    issues: list[Issue] = []
    # The reader raises csv.Error mid-iteration, so only here is its line number known.
    while True:
        try:
            row = next(reader)
        except StopIteration:
            break
        except csv.Error as error:
            raise VerdictFileError(
                f"{path} could not be read as CSV at line {reader.line_num}: {error}. "
                "Save it as a plain UTF-8 CSV."
            ) from error
        line_number = reader.line_num
        case_id = (row.get("case_id") or "").strip()
        alert_class = (row.get("alert_class") or "").strip()
        label = (row.get("verdict") or "").strip()
        if not (case_id and alert_class and label):
            issues.append(
                Issue(
                    IssueKind.INVALID_VERDICT_ROW,
                    f"{path.name}:{line_number}",
                    "case_id, alert_class, and verdict must all have a value",
                )
            )
            continue
        rows.append(VerdictRow(case_id, alert_class, label, line_number))
    return rows, issues
