"""Parse analyst verdicts sent to the verdict API, without any web framework.

The CSV form goes through the same reader as a verdict file, so a file and a request are
checked by one rule. The JSON form applies the same per-value rules to each item.
"""

import io
from dataclasses import dataclass, replace
from email.message import Message

from detecttrace.config import Config
from detecttrace.jsontext import parse_json_text
from detecttrace.model import Issue, IssueKind, VerdictRow
from detecttrace.summary import to_visible_text
from detecttrace.verdicts import (
    REQUIRED_COLUMNS,
    VerdictFileError,
    VerdictRowLimitError,
    read_verdict_rows,
    shorten_value,
)

MAX_ROWS = 10_000
# Room for MAX_ROWS rows of a few hundred bytes each (values may be up to MAX_LABEL_LENGTH
# characters) with headroom, yet small enough that one request cannot exhaust memory. The
# route enforces it while reading the body; this module only sees a body that fits.
MAX_BODY_BYTES = 4 << 20
SUBJECT = "verdict API"
UTF8_CHARSETS = frozenset({"utf-8", "utf8"})


@dataclass(frozen=True, slots=True)
class RejectedRow:
    where: str  # "line 7" for CSV, "verdicts[3]" for JSON
    reason: str


class UnsupportedMediaType(Exception):
    """The body is neither application/json nor text/csv in UTF-8."""


class TooManyRows(Exception):
    """The body holds more than MAX_ROWS rows."""


class InvalidBody(Exception):
    """The body cannot be read as verdicts at all."""


def parse_verdicts_body(
    body: bytes, content_type: str | None, config: Config
) -> tuple[list[VerdictRow], list[RejectedRow], list[Issue]]:
    """Return the accepted rows, the rejected rows with reasons, and input issues.

    Accepted rows carry line_number 0. Within one request a case_id may appear once among the
    accepted rows: a later row repeating it is rejected, so no request is ambiguous about
    which verdict wins. Input issues are notes such as a shortened value.
    """
    is_json = _is_json(content_type)
    text = _decode(body, is_json)
    if is_json:
        candidates, rejected, issues = _read_json(text)
    else:
        candidates, rejected, issues = _read_csv(text)
    return _accept(candidates, rejected, issues, config)


@dataclass(frozen=True, slots=True)
class _Candidate:
    position: int  # the item index or the line, so rejections come back in body order
    where: str
    row: VerdictRow


def _is_json(content_type: str | None) -> bool:
    if not content_type:
        raise UnsupportedMediaType("Send application/json or text/csv.")
    message = Message()
    message["content-type"] = content_type
    media_type = message.get_content_type()
    charset = message.get_content_charset()
    if media_type not in ("application/json", "text/csv"):
        raise UnsupportedMediaType("Send application/json or text/csv.")
    if charset is not None and charset not in UTF8_CHARSETS:
        raise UnsupportedMediaType("The body must be UTF-8.")
    return media_type == "application/json"


def _decode(body: bytes, is_json: bool) -> str:
    try:
        # A byte order mark is what Excel writes to a CSV; JSON has none.
        return body.decode("utf-8" if is_json else "utf-8-sig")
    except UnicodeDecodeError as error:
        raise InvalidBody("The body is not UTF-8.") from error


def _read_json(text: str) -> tuple[list[_Candidate], list[tuple[int, RejectedRow]], list[Issue]]:
    document = parse_json_text(text)
    items = document.get("verdicts") if isinstance(document, dict) else None
    if not isinstance(items, list):
        raise InvalidBody('The body must be a JSON object with a "verdicts" list.')
    if len(items) > MAX_ROWS:
        raise TooManyRows(f"At most {MAX_ROWS} verdicts per request.")
    candidates: list[_Candidate] = []
    rejected: list[tuple[int, RejectedRow]] = []
    issues: list[Issue] = []
    reported_long: set[tuple[str, str]] = set()
    for index, entry in enumerate(items):
        where = f"verdicts[{index}]"
        values, reason = _read_item(entry)
        if reason is not None:
            rejected.append((index, RejectedRow(where, reason)))
            continue
        subject = f"{SUBJECT}:{where}"
        case_id, alert_class, label = (
            shorten_value(value, column, subject, issues, reported_long)
            for column, value in zip(REQUIRED_COLUMNS, values, strict=True)
        )
        candidates.append(_Candidate(index, where, VerdictRow(case_id, alert_class, label, 0)))
    return candidates, rejected, issues


def _read_item(entry: object) -> tuple[list[str], str | None]:
    if not isinstance(entry, dict):
        return [], "must be an object"
    values: list[str] = []
    for column in REQUIRED_COLUMNS:
        if column not in entry:
            return [], f"{column} is required"
        value = entry[column]
        if not isinstance(value, str):
            return [], f"{column} must be a string"
        value = value.strip()
        if not value:
            return [], f"{column} must have a value"
        if "\n" in value or "\r" in value:
            return [], f"{column} must not contain a line break"
        values.append(value)
    return values, None


def _read_csv(text: str) -> tuple[list[_Candidate], list[tuple[int, RejectedRow]], list[Issue]]:
    try:
        rows, issues = read_verdict_rows(io.StringIO(text, newline=""), SUBJECT, max_rows=MAX_ROWS)
    except VerdictRowLimitError as error:
        raise TooManyRows(f"At most {MAX_ROWS} verdicts per request.") from error
    except VerdictFileError as error:
        raise InvalidBody(str(error)) from error
    rejected: list[tuple[int, RejectedRow]] = []
    notes: list[Issue] = []
    for issue in issues:
        line = int(issue.subject.rpartition(":")[2])
        if issue.kind is IssueKind.INVALID_VERDICT_ROW:
            rejected.append((line, RejectedRow(f"line {line}", issue.detail)))
        else:
            # Name the line as a rejection does, as the JSON form names its item.
            notes.append(replace(issue, subject=f"{SUBJECT}:line {line}"))
    candidates = [
        _Candidate(row.line_number, f"line {row.line_number}", replace(row, line_number=0))
        for row in rows
    ]
    return candidates, rejected, notes


def _accept(
    candidates: list[_Candidate],
    rejected: list[tuple[int, RejectedRow]],
    issues: list[Issue],
    config: Config,
) -> tuple[list[VerdictRow], list[RejectedRow], list[Issue]]:
    rows: list[VerdictRow] = []
    seen: set[str] = set()
    rejections = list(rejected)
    for candidate in candidates:
        row = candidate.row
        if config.to_analyst_verdict(row.label) is None:
            reason = f"verdict label '{to_visible_text(row.label)}' is not in label_map"
        elif row.case_id in seen:
            reason = "duplicate case_id in this request"
        else:
            seen.add(row.case_id)
            rows.append(row)
            continue
        rejections.append((candidate.position, RejectedRow(candidate.where, reason)))
    rejections.sort(key=lambda rejection: rejection[0])
    return rows, [rejection for _, rejection in rejections], issues
