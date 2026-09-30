"""Read trace files (JSON lines or one document; plain, gzip or zstd) into spans."""

import gzip
import io
import itertools
import os
import re
import zlib
from collections.abc import Callable, Generator, Iterator
from pathlib import Path
from typing import TYPE_CHECKING, Protocol

from detecttrace import langfuse, otlp
from detecttrace.jsontext import Json, parse_json_text
from detecttrace.model import InputFileError, Issue, IssueKind, Span, describe_os_error
from detecttrace.runconfig import TraceFormat

if TYPE_CHECKING:
    from _typeshed import WriteableBuffer
    from zstandard import ZstdDecompressor

_GZIP_MAGIC = b"\x1f\x8b"
_ZSTD_MAGIC = b"\x28\xb5\x2f\xfd"
# A zstd block is at least 4 bytes and at most 128 KiB decoded, so 256 compressed bytes
# decode to at most about 8 MiB however hostile the file.
_ZSTD_CHUNK_SIZE = 256
_READ_SIZE = 1 << 20
# Bounds memory for any one line or one-document file, compressed or not.
_MAX_LINE_BYTES = 32 << 20
_MAX_LINE_TEXT = f"{_MAX_LINE_BYTES >> 20} MiB"
_SNIFF_SIZE = 64 * 1024
# Enough to pass a few leading lines of another shape, such as metrics written to the same
# file; each document is under _MAX_LINE_BYTES, so memory stays bounded.
_MAX_SNIFFED_DOCUMENTS = 16
_CONSOLE_CONTEXT = b'"context": {'
_CONSOLE_TRACE_ID = b'"trace_id": "0x'
_CONSOLE_DETAIL = (
    "this is OpenTelemetry console exporter output, not OTLP JSON; write traces with "
    "the Collector file exporter, or from the agent with FileSpanExporter: "
    "pip install detecttrace[otel], then add "
    'BatchSpanProcessor(FileSpanExporter("traces/{date}-{pid}.jsonl")) to your TracerProvider'
)
_BOM = b"\xef\xbb\xbf"
# A UTF-16 or UTF-32 BOM, or an ASCII character padded with NULs: JSON text starts with
# an ASCII character, so this is UTF-16 or UTF-32 without a BOM. Covers the UTF-32 BOMs too.
_UTF16_OR_UTF32_START = re.compile(rb"\xff\xfe|\xfe\xff|[^\x00]\x00|\x00{1,3}[^\x00]")


class _CorruptZstdError(Exception):
    """A zstd stream that cannot be decoded; not a ValueError, so JSON parsing never swallows it."""


class _MissingZstdError(Exception):
    """A zstd file was found but the optional zstandard package is not installed."""


# BadGzipFile covers a broken member header, including trailing bytes after the last member.
_COMPRESSION_ERRORS = (EOFError, zlib.error, gzip.BadGzipFile, _CorruptZstdError)


class _DocumentParser(Protocol):
    def parse_document(
        self, document: Json, subject: str, line_number: int | None, issues: list[Issue]
    ) -> Iterator[Span]: ...

    def finish(self, subject: str) -> list[Issue]: ...


# Each document format has its own parser; the files are read the same way for all of them.
_PARSERS: dict[TraceFormat, Callable[[], _DocumentParser]] = {
    "otlp_jsonl": otlp.OtlpParser,
    "otlp_json": otlp.OtlpParser,
    "langfuse": langfuse.LangfuseParser,
}


# How to fix a trace path that is missing or holds no files, as check says it; init names its
# --traces option instead.
PATH_HINT = "Check traces.path in detecttrace.yaml."


class TraceFileError(InputFileError):
    """The trace path cannot be used at all: missing, unreadable, or holding no trace files."""


def load_spans(
    path: Path, *, format: TraceFormat = "otlp_jsonl", path_hint: str = PATH_HINT
) -> tuple[list[Span], list[Issue]]:
    """Read every trace file at `path` (a file or a folder) and return unique spans.

    Files are read in sorted path order and the first copy of a duplicate span wins,
    so the same input always gives the same spans. Invalid input is reported as an
    Issue; TraceFileError is raised only when there is nothing to read. `format` picks the
    document parser, made new for each call; every format reads JSON lines and one-document
    files alike, and the parser adds its own issues about each whole file when the file ends.
    `path_hint` ends the error for a missing path or one with no trace files.
    """
    issues: list[Issue] = []
    trace_files = _find_trace_files(path, issues, path_hint)
    parser = _PARSERS[format]()
    spans: list[Span] = []
    seen: dict[tuple[str, str], Span] = {}
    for file_path, subject in trace_files:
        file_issues: list[Issue] = []
        span_count = 0
        for document, line_number in _read_documents(file_path, subject, file_issues):
            for span in parser.parse_document(document, subject, line_number, file_issues):
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
        file_issues.extend(parser.finish(subject))
        # Only a file with no OTLP spans is sniffed, so OTLP text quoting the marker is safe.
        if span_count == 0 and file_issues and _is_console_exporter_output(file_path):
            # One clear issue beats a line-by-line flood about a format we don't read.
            issues.append(Issue(IssueKind.CONSOLE_EXPORTER_OUTPUT, subject, _CONSOLE_DETAIL))
        else:
            issues.extend(file_issues)
    return spans, issues


def detect_format(
    path: Path, *, path_hint: str = PATH_HINT
) -> tuple[TraceFormat | None, list[Issue]]:
    """Name the format of the trace files at `path` from the first file with a known document.

    Files are tried in the order load_spans reads them, and up to _MAX_SNIFFED_DOCUMENTS
    documents of each (a one-document file is read whole). With no format found, the issues
    say why, as load_spans would: a console exporter file gives one CONSOLE_EXPORTER_OUTPUT
    issue in place of its line issues, and files that cannot be read give theirs. No format
    and no issue means no file holds a document of a known shape. With a format, no issues
    are returned: load_spans reports them. Raises TraceFileError as load_spans does.
    """
    issues: list[Issue] = []
    for file_path, subject in _find_trace_files(path, issues, path_hint):
        file_issues: list[Issue] = []
        documents = _read_documents(file_path, subject, file_issues)
        try:
            for document, line_number in itertools.islice(documents, _MAX_SNIFFED_DOCUMENTS):
                if isinstance(document, dict) and isinstance(document.get("resourceSpans"), list):
                    return ("otlp_json" if line_number is None else "otlp_jsonl"), []
                if langfuse.is_langfuse_document(document):
                    return "langfuse", []
        finally:
            documents.close()
        # Sniffed only when the file holds no known document, as in load_spans.
        if _is_console_exporter_output(file_path):
            issues.append(Issue(IssueKind.CONSOLE_EXPORTER_OUTPUT, subject, _CONSOLE_DETAIL))
        else:
            issues.extend(file_issues)
    return None, issues


def _find_trace_files(path: Path, issues: list[Issue], path_hint: str) -> list[tuple[Path, str]]:
    if not path.exists():
        raise TraceFileError(f"Trace path not found: {path}. {path_hint}")
    trace_files = _list_trace_files(path, issues)
    if not trace_files:
        raise TraceFileError(f"No trace files found under {path}. {path_hint}")
    return trace_files


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
) -> Generator[tuple[Json, int | None], None, None]:
    """Yield (document, line number); the line number is None for a one-document file."""
    try:
        first_line = _first_content_line(file_path)
        if first_line is None:
            issues.append(Issue(IssueKind.EMPTY_FILE, subject))
        elif _UTF16_OR_UTF32_START.match(first_line):
            # One issue for the file: every line of it would fail the same way.
            issues.append(Issue(IssueKind.INVALID_FILE, subject, "not UTF-8"))
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
        # Pieces, not whole lines: the start of a line is enough to tell the format.
        for piece in iter(lambda: handle.readline(_READ_SIZE), b""):
            stripped = piece.removeprefix(_BOM).strip()
            if stripped:
                return stripped
    return None


def _may_open_document(first_line: bytes) -> bool:
    # A pretty-printed document can open with `{ "resourceSpans": [`; a JSON line parses alone.
    return first_line.startswith((b"{", b"[")) and _parse_json_line(first_line) is None


def _read_one_document(
    file_path: Path, subject: str, issues: list[Issue]
) -> Iterator[tuple[Json, None]]:
    data = _read_document_bytes(file_path)
    if data is None:
        issues.append(Issue(IssueKind.INVALID_FILE, subject, f"document is over {_MAX_LINE_TEXT}"))
        return
    try:
        text = data.decode("utf-8-sig")
    except UnicodeDecodeError:
        issues.append(Issue(IssueKind.INVALID_FILE, subject, "not UTF-8"))
        return
    document = parse_json_text(text)
    if document is None:
        issues.append(Issue(IssueKind.INVALID_FILE, subject, "not valid JSON"))
        return
    yield document, None


def _load_document(file_path: Path) -> Json | None:
    # A too-large file whose first line is not a whole document is read as JSON lines instead.
    data = _read_document_bytes(file_path)
    try:
        return None if data is None else parse_json_text(data.decode("utf-8-sig"))
    except UnicodeDecodeError:
        return None


def _read_document_bytes(file_path: Path) -> bytes | None:
    """Return the whole decompressed file, or None when it is over the size limit."""
    # NOTE: a one-document file is read whole; JSON lines is the format for large inputs.
    # Read in chunks, not with one `read(limit + 1)`: from Python 3.12 gzip's read(n) holds its
    # chunks and their joined copy at once, so an over-limit file would cost twice the limit.
    chunks: list[bytes] = []
    total = 0
    with _open_binary(file_path) as handle:
        while chunk := handle.read(_READ_SIZE):
            total += len(chunk)
            if total > _MAX_LINE_BYTES:
                return None
            chunks.append(chunk)
    return b"".join(chunks)


def _read_json_lines(
    file_path: Path, subject: str, issues: list[Issue]
) -> Iterator[tuple[Json, int]]:
    with _open_binary(file_path) as handle:
        try:
            for line_number, line in enumerate(_read_lines(handle), start=1):
                if line is None:
                    detail = f"line {line_number} is over {_MAX_LINE_TEXT}"
                    issues.append(Issue(IssueKind.INVALID_LINE, subject, detail))
                    continue
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


def _read_lines(handle: io.BufferedIOBase) -> Iterator[bytes | None]:
    """Yield each line with its newline, or None for a line over the size limit."""
    while line := handle.readline(_READ_SIZE):
        if line.endswith(b"\n"):
            yield line
            continue
        pieces = [line]
        size = len(line)
        while not line.endswith(b"\n") and (line := handle.readline(_READ_SIZE)):
            size += len(line)
            if size > _MAX_LINE_BYTES:
                # Keep reading to the newline so the next line starts in the right place.
                pieces.clear()
            else:
                pieces.append(line)
        yield b"".join(pieces) if size <= _MAX_LINE_BYTES else None


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
    hide a truncated file, so frames are decoded here with decompressobj. It is fed a
    few compressed bytes at a time because it has no cap on how much one call decodes.
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
        text = line.decode("utf-8")
    except UnicodeDecodeError:
        return None
    return parse_json_text(text)
