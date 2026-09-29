"""Read OTLP JSON trace files (JSON lines or one document; plain, gzip or zstd) into spans."""

import gzip
import io
import json
import os
import re
import zlib
from collections.abc import Callable, Iterator
from pathlib import Path
from typing import TYPE_CHECKING, Any, TypeVar

from detecttrace.model import InputFileError, Issue, IssueKind, Span, describe_os_error

if TYPE_CHECKING:
    from _typeshed import WriteableBuffer
    from zstandard import ZstdDecompressor

_GZIP_MAGIC = b"\x1f\x8b"
_ZSTD_MAGIC = b"\x28\xb5\x2f\xfd"
_ZSTD_CHUNK_SIZE = 64 * 1024
_SNIFF_SIZE = 64 * 1024
_CONSOLE_CONTEXT = b'"context": {'
_CONSOLE_TRACE_ID = b'"trace_id": "0x'
_CONSOLE_DETAIL = (
    "this is OpenTelemetry console exporter output, not OTLP JSON; write traces with "
    "the Collector file exporter, or with FileSpanExporter (pip install detecttrace[otel])"
)
_BOM = b"\xef\xbb\xbf"
_STATUS_ERROR = (2, "STATUS_CODE_ERROR")
_TRACE_ID = re.compile(r"[0-9a-f]{32}")
_SPAN_ID = re.compile(r"[0-9a-f]{16}")
_HEX = re.compile(r"[0-9a-f]*")
_BASE64 = re.compile(r"[A-Za-z0-9+/=]+")
_INT_TEXT = re.compile(r"-?[0-9]+")
_UINT64_LIMIT = 2**64


class _CorruptZstdError(Exception):
    """A zstd stream that cannot be decoded; not a ValueError, so JSON parsing never swallows it."""


class _MissingZstdError(Exception):
    """A zstd file was found but the optional zstandard package is not installed."""


# BadGzipFile covers a broken member header, including trailing bytes after the last member.
_COMPRESSION_ERRORS = (EOFError, zlib.error, gzip.BadGzipFile, _CorruptZstdError)

# OTLP JSON is untyped input: Any is the honest type until fields are validated in _to_span.
Json = Any
Report = Callable[[IssueKind, str], None]
T = TypeVar("T")


class TraceFileError(InputFileError):
    """The trace path cannot be used at all: missing, unreadable, or holding no trace files."""


def load_spans(path: Path) -> tuple[list[Span], list[Issue]]:
    """Read every trace file at `path` (a file or a folder) and return unique spans.

    Files are read in sorted path order and the first copy of a duplicate span wins,
    so the same input always gives the same spans. Invalid input is reported as an
    Issue; TraceFileError is raised only when there is nothing to read.
    """
    if not path.exists():
        raise TraceFileError(
            f"Trace path not found: {path}. Check traces.path in detecttrace.yaml."
        )
    issues: list[Issue] = []
    trace_files = _list_trace_files(path, issues)
    if not trace_files:
        raise TraceFileError(
            f"No trace files found under {path}. Check traces.path in detecttrace.yaml."
        )
    spans: list[Span] = []
    seen: dict[tuple[str, str], Span] = {}
    for file_path, subject in trace_files:
        file_issues: list[Issue] = []
        span_count = 0
        for document, line_number in _read_documents(file_path, subject, file_issues):
            for span in _parse_document(document, subject, line_number, file_issues):
                span_count += 1
                key = (span.trace_id, span.span_id)
                first = seen.get(key)
                if first is not None:
                    # repr() fallback: a NaN attribute never equals itself, even in an identical copy.
                    is_same = span == first or repr(span) == repr(first)
                    kind = (
                        IssueKind.DUPLICATE_SPAN
                        if is_same
                        else IssueKind.CONFLICTING_DUPLICATE_SPAN
                    )
                    file_issues.append(Issue(kind, subject, f"{span.trace_id}/{span.span_id}"))
                    continue
                seen[key] = span
                spans.append(span)
        # Only a file with no OTLP spans is sniffed, so OTLP text quoting the marker is safe.
        if span_count == 0 and file_issues and _is_console_exporter_output(file_path):
            # One clear issue beats a line-by-line flood about a format we don't read.
            issues.append(Issue(IssueKind.CONSOLE_EXPORTER_OUTPUT, subject, _CONSOLE_DETAIL))
        else:
            issues.extend(file_issues)
    return spans, issues


def _list_trace_files(path: Path, issues: list[Issue]) -> list[tuple[Path, str]]:
    """Return (file, subject) pairs; the subject is the POSIX path relative to `path`."""
    if path.is_file():
        return [(path, path.name)]

    def report(error: OSError) -> None:
        folder = Path(error.filename)
        if folder == path:
            # An issue for "." would only be followed by a misleading "No trace files found".
            raise TraceFileError(
                f"Trace folder {path} cannot be read: {describe_os_error(error)}. "
                "Check its permissions."
            )
        subject = folder.relative_to(path).as_posix()
        issues.append(Issue(IssueKind.INVALID_FILE, subject, describe_os_error(error)))

    files: list[tuple[Path, str]] = []
    # os.walk, not Path.rglob: rglob on 3.11 silently skips folders it cannot list.
    for folder, folder_names, file_names in os.walk(path, onerror=report):
        # Sorted so issues come out in the same order on every platform and file system.
        folder_names[:] = sorted(name for name in folder_names if not name.startswith("."))
        for name in sorted(file_names):
            if name.startswith("."):
                continue
            file_path = Path(folder, name)
            subject = file_path.relative_to(path).as_posix()
            try:
                # On 3.11 is_file() swallows only a few errors; EACCES from a folder that is
                # listable but not enterable escapes, and ELOOP hides a symlink loop.
                if file_path.is_file():
                    files.append((file_path, subject))
                elif file_path.is_symlink():
                    issues.append(
                        Issue(
                            IssueKind.INVALID_FILE,
                            subject,
                            "symbolic link target is missing or loops",
                        )
                    )
            except OSError as error:
                issues.append(Issue(IssueKind.INVALID_FILE, subject, describe_os_error(error)))
    # POSIX form so Windows and Linux read files, and so pick duplicate winners, in the same order.
    return sorted(files, key=lambda item: item[1])


def _read_documents(
    file_path: Path, subject: str, issues: list[Issue]
) -> Iterator[tuple[Json, int | None]]:
    """Yield (document, line number); the line number is None for a one-document file."""
    try:
        first_line = _first_content_line(file_path)
        if first_line is None:
            issues.append(Issue(IssueKind.EMPTY_FILE, subject))
        elif first_line in (b"{", b"["):
            yield from _read_one_document(file_path, subject, issues)
        elif _may_open_document(first_line) and (document := _load_document(file_path)) is not None:
            yield document, None
        else:
            yield from _read_json_lines(file_path, subject, issues)
    except _COMPRESSION_ERRORS as error:
        issues.append(_compression_issue(error, subject))
    except _MissingZstdError:
        issues.append(
            Issue(
                IssueKind.UNSUPPORTED_COMPRESSION,
                subject,
                "zstd-compressed; install detecttrace[zstd] to read it",
            )
        )
    except OSError as error:
        issues.append(Issue(IssueKind.INVALID_FILE, subject, describe_os_error(error)))


def _compression_issue(error: Exception, subject: str, suffix: str = "") -> Issue:
    if isinstance(error, EOFError):
        return Issue(IssueKind.TRUNCATED_FILE, subject, "compressed file ends early" + suffix)
    return Issue(IssueKind.INVALID_FILE, subject, "corrupt compressed data" + suffix)


def _first_content_line(file_path: Path) -> bytes | None:
    """Return the first non-blank line, stripped, or None when the file has no content."""
    with _open_binary(file_path) as handle:
        for line in handle:
            stripped = line.removeprefix(_BOM).strip()
            if stripped:
                return stripped
    return None


def _may_open_document(first_line: bytes) -> bool:
    # A pretty-printed document can open with `{ "resourceSpans": [`; a JSON line parses alone.
    return first_line.startswith((b"{", b"[")) and _parse_json_line(first_line) is None


def _read_one_document(
    file_path: Path, subject: str, issues: list[Issue]
) -> Iterator[tuple[Json, None]]:
    document = _load_document(file_path)
    if document is None:
        issues.append(Issue(IssueKind.INVALID_FILE, subject, "not valid JSON"))
        return
    yield document, None


def _load_document(file_path: Path) -> Json | None:
    # NOTE: a one-document file is read whole; JSON lines is the format for large inputs.
    with _open_binary(file_path) as handle:
        try:
            # json.load on bytes detects UTF-8 with or without a BOM.
            return json.load(handle)
        except (ValueError, RecursionError):
            return None


def _read_json_lines(
    file_path: Path, subject: str, issues: list[Issue]
) -> Iterator[tuple[Json, int]]:
    with _open_binary(file_path) as handle:
        try:
            for line_number, line in enumerate(handle, start=1):
                if line_number == 1:
                    line = line.removeprefix(_BOM)
                if not line.strip():
                    continue
                document = _parse_json_line(line)
                if document is None:
                    # Only the last line of a file can lack its newline: a writer is still busy.
                    kind = (
                        IssueKind.INVALID_LINE if line.endswith(b"\n") else IssueKind.TRUNCATED_LINE
                    )
                    issues.append(Issue(kind, subject, f"line {line_number}"))
                    continue
                yield document, line_number
        except _COMPRESSION_ERRORS as error:
            issues.append(_compression_issue(error, subject, "; earlier lines were read"))


def _open_binary(file_path: Path) -> io.BufferedIOBase:
    with file_path.open("rb") as probe:
        magic = probe.read(len(_ZSTD_MAGIC))
    if magic.startswith(_GZIP_MAGIC):
        return gzip.open(file_path, "rb")
    if magic == _ZSTD_MAGIC:
        try:
            import zstandard
        except ImportError:
            raise _MissingZstdError from None
        return io.BufferedReader(
            _ZstdReader(file_path.open("rb"), zstandard.ZstdDecompressor(), zstandard.ZstdError)
        )
    return file_path.open("rb")


class _ZstdReader(io.RawIOBase):
    """Decompress a zstd stream of one or more frames, raising EOFError when it ends early.

    zstandard's own stream_reader treats a cut-off stream as a clean end, which would
    hide a truncated file, so frames are decoded here with decompressobj.
    """

    def __init__(
        self,
        source: io.BufferedIOBase,
        decompressor: "ZstdDecompressor",
        error_type: type[Exception],
    ) -> None:
        self._source = source
        self._decompressor = decompressor
        self._error_type = error_type
        self._frame = self._decompressor.decompressobj()
        self._is_frame_open = False
        self._pending = b""
        self._offset = 0
        self._deferred_error: _CorruptZstdError | None = None

    def readable(self) -> bool:
        return True

    def readinto(self, buffer: "WriteableBuffer", /) -> int:
        while self._offset == len(self._pending):
            if self._deferred_error is not None:
                raise self._deferred_error
            chunk = self._source.read(_ZSTD_CHUNK_SIZE)
            if not chunk:
                if self._is_frame_open:
                    raise EOFError("zstd stream ends inside a frame")
                return 0
            self._pending = self._decompress(chunk)
            self._offset = 0
        target = memoryview(buffer).cast("B")
        size = min(len(target), len(self._pending) - self._offset)
        target[:size] = self._pending[self._offset : self._offset + size]
        self._offset += size
        return size

    def close(self) -> None:
        self._source.close()
        super().close()

    def _decompress(self, chunk: bytes) -> bytes:
        output: list[bytes] = []
        while chunk:
            try:
                output.append(self._frame.decompress(chunk))
            except self._error_type as error:
                # Hand out frames decoded before the bad bytes first, as gzip does.
                self._deferred_error = _CorruptZstdError(str(error))
                break
            if self._frame.eof:
                chunk = self._frame.unused_data
                self._frame = self._decompressor.decompressobj()
                self._is_frame_open = False
            else:
                chunk = b""
                self._is_frame_open = True
        return b"".join(output)


def _is_console_exporter_output(file_path: Path) -> bool:
    """Whether the start of the file looks like the OTel SDK ConsoleSpanExporter's output."""
    try:
        with _open_binary(file_path) as handle:
            head = handle.read(_SNIFF_SIZE)
    except (*_COMPRESSION_ERRORS, _MissingZstdError, OSError):
        return False
    context_at = head.find(_CONSOLE_CONTEXT)
    return context_at != -1 and head.find(_CONSOLE_TRACE_ID, context_at) != -1


def _parse_json_line(line: bytes) -> Json | None:
    try:
        return json.loads(line.decode("utf-8"))
    except (ValueError, RecursionError):
        return None


def _parse_document(
    document: Json, subject: str, line_number: int | None, issues: list[Issue]
) -> Iterator[Span]:
    prefix = "" if line_number is None else f"line {line_number}: "

    def report(kind: IssueKind, detail: str) -> None:
        issues.append(Issue(kind, subject, prefix + detail))

    if not isinstance(document, dict) or not isinstance(document.get("resourceSpans"), list):
        report(IssueKind.INVALID_FILE, "a document has no resourceSpans")
        return
    for index, resource_spans in enumerate(document["resourceSpans"]):
        yield from _parse_resource_spans(resource_spans, f"resourceSpans[{index}]", report)


def _parse_resource_spans(resource_spans: Json, where: str, report: Report) -> Iterator[Span]:
    if not isinstance(resource_spans, dict):
        report(IssueKind.INVALID_FILE, f"{where} is not an object")
        return
    resource = resource_spans.get("resource") or {}
    if not isinstance(resource, dict):
        report(IssueKind.INVALID_FILE, f"{where}.resource is not an object")
        return
    # NOTE: this one dict is shared by every span of the resource; treat it as read-only.
    resource_attributes = _decode_attributes(
        resource.get("attributes"), f"{where}.resource", report
    )
    scope_spans_list = resource_spans.get("scopeSpans") or []
    if not isinstance(scope_spans_list, list):
        report(IssueKind.INVALID_FILE, f"{where}.scopeSpans is not a list")
        return
    for scope_index, scope_spans in enumerate(scope_spans_list):
        scope_where = f"{where}.scopeSpans[{scope_index}]"
        if not isinstance(scope_spans, dict):
            report(IssueKind.INVALID_FILE, f"{scope_where} is not an object")
            continue
        raw_spans = scope_spans.get("spans") or []
        if not isinstance(raw_spans, list):
            report(IssueKind.INVALID_FILE, f"{scope_where}.spans is not a list")
            continue
        for span_index, raw in enumerate(raw_spans):
            span_where = f"{scope_where}.spans[{span_index}]"
            span = _to_span(raw, resource_attributes, span_where, report)
            if span is not None:
                yield span


def _to_span(
    raw: Json, resource_attributes: dict[str, object], where: str, report: Report
) -> Span | None:
    if not isinstance(raw, dict):
        report(IssueKind.INVALID_SPAN, f"{where} is not an object")
        return None
    try:
        trace_id = _to_id(raw.get("traceId"), _TRACE_ID, "trace ID", 32)
        span_id = _to_id(raw.get("spanId"), _SPAN_ID, "span ID", 16)
        parent = raw.get("parentSpanId")
        parent_span_id = (
            None if parent in (None, "") else _to_id(parent, _SPAN_ID, "parent span ID", 16)
        )
        start_ns = _to_nanos(raw.get("startTimeUnixNano"), "start time")
        end = raw.get("endTimeUnixNano")
        end_ns = start_ns if end is None else _to_nanos(end, "end time")
        if end_ns < start_ns:
            raise ValueError("end time is before start time")
        status = raw.get("status") or {}
        if not isinstance(status, dict):
            raise ValueError("status is not an object")
    except ValueError as error:
        report(IssueKind.INVALID_SPAN, f"{where}: {error}")
        return None
    return Span(
        trace_id=trace_id,
        span_id=span_id,
        parent_span_id=parent_span_id,
        name=_to_name(raw.get("name"), where, report),
        start_ns=start_ns,
        end_ns=end_ns,
        is_error=status.get("code") in _STATUS_ERROR,
        attributes=_decode_attributes(raw.get("attributes"), where, report),
        resource_attributes=resource_attributes,
    )


def _to_id(value: Json, pattern: re.Pattern[str], label: str, length: int) -> str:
    if not isinstance(value, str):
        raise ValueError(f"{label} is missing or not a string")
    lowered = value.lower()
    if pattern.fullmatch(lowered):
        return lowered
    reason = f"{label} must be {length} hex characters"
    if _BASE64.fullmatch(value) and not _HEX.fullmatch(lowered):
        reason += "; IDs look base64; OTLP JSON uses hex"
    raise ValueError(reason)


def _to_name(value: Json, where: str, report: Report) -> str:
    if value is None or isinstance(value, str):
        return value or ""
    report(IssueKind.INVALID_ATTRIBUTE, f"{where}: name is not a string")
    return ""


def _to_nanos(value: Json, label: str) -> int:
    # OTLP JSON times are unsigned 64-bit integers, written as digit strings or JSON integers.
    # The length check keeps int() off absurdly long digit strings.
    if isinstance(value, str) and value.isascii() and value.isdigit() and len(value) <= 20:
        value = int(value)
    if isinstance(value, int) and not isinstance(value, bool) and 0 <= value < _UINT64_LIMIT:
        return value
    raise ValueError(f"{label} must be an unsigned 64-bit integer of nanoseconds")


def _decode_attributes(items: Json, where: str, report: Report) -> dict[str, object]:
    """Decode an attribute list, skipping and reporting each malformed entry."""
    if items is None:
        return {}
    if not isinstance(items, list):
        report(IssueKind.INVALID_ATTRIBUTE, f"{where}.attributes is not a list")
        return {}
    attributes: dict[str, object] = {}
    for index, item in enumerate(items):
        # Fast path for the common string attribute: this loop runs millions of times at scale.
        if isinstance(item, dict):
            key = item.get("key")
            value = item.get("value")
            if isinstance(key, str) and isinstance(value, dict):
                text = value.get("stringValue")
                if isinstance(text, str):
                    attributes[key] = text
                    continue
        try:
            key, value = _decode_key_value(item)
        except (ValueError, RecursionError) as error:
            report(IssueKind.INVALID_ATTRIBUTE, f"{where}.attributes[{index}]: {error}")
            continue
        attributes[key] = value
    return attributes


def _decode_key_value(item: Json) -> tuple[str, object]:
    if not isinstance(item, dict) or not isinstance(item.get("key"), str):
        raise ValueError("attribute has no string key")
    value = item.get("value")
    return item["key"], _decode_value({} if value is None else value)


def _decode_value(value: Json) -> object:
    """Decode one OTLP AnyValue; raise ValueError when its shape is wrong."""
    if not isinstance(value, dict):
        raise ValueError("value is not an object")
    if "stringValue" in value:
        return _require(value["stringValue"], str, "stringValue")
    if "boolValue" in value:
        return _require(value["boolValue"], bool, "boolValue")
    if "intValue" in value:
        return _to_int(value["intValue"])
    if "doubleValue" in value:
        raw = value["doubleValue"]
        if isinstance(raw, int | float) and not isinstance(raw, bool):
            return _to_float(raw)
        # Strings cover the JSON encodings "NaN", "Infinity" and "-Infinity".
        return float(_require(raw, str, "doubleValue"))
    if "arrayValue" in value:
        return [_decode_value(v) for v in _values(value["arrayValue"], "arrayValue")]
    if "kvlistValue" in value:
        return dict(_decode_key_value(i) for i in _values(value["kvlistValue"], "kvlistValue"))
    if "bytesValue" in value:
        return _require(value["bytesValue"], str, "bytesValue")
    if value:
        # A mistyped key such as "stringvalue" would otherwise decode to None unnoticed.
        raise ValueError(f"unknown value type: {', '.join(sorted(value))}")
    return None


def _to_int(raw: Json) -> int:
    # OTLP JSON encodes 64-bit integers as strings. int() alone would also accept
    # spaces, underscores, "+" and non-ASCII digits.
    if isinstance(raw, int) and not isinstance(raw, bool):
        return raw
    if not _INT_TEXT.fullmatch(_require(raw, str, "intValue")):
        raise ValueError("intValue is not an integer")
    return int(raw)


def _to_float(raw: int | float) -> float:
    try:
        return float(raw)
    except OverflowError:
        raise ValueError("doubleValue is out of range") from None


def _values(container: Json, name: str) -> list[Json]:
    if not isinstance(container, dict):
        raise ValueError(f"{name} is not an object")
    values = container.get("values") or []
    if not isinstance(values, list):
        raise ValueError(f"{name}.values is not a list")
    return values


def _require(value: Json, expected: type[T], name: str) -> T:
    if not isinstance(value, expected):
        raise ValueError(f"{name} is not a {expected.__name__}")
    return value
