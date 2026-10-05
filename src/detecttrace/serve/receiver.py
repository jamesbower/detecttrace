"""Turn an OTLP/HTTP trace export request body into spans; no web framework involved.

Only OTLP JSON is read, plain or gzip, as the Collector's `otlphttp` exporter sends it with
`encoding: json`. Tool results are dropped here, before anything is stored.
"""

import dataclasses
import zlib

from detecttrace.conventions import TOOL_CALL_RESULT
from detecttrace.jsontext import parse_json_text
from detecttrace.model import Issue, IssueKind, Span
from detecttrace.otlp import OtlpParser
from detecttrace.serve.store import INGEST_SUBJECT
from detecttrace.traces import MAX_DOCUMENT_BYTES

# As received, before decompression; the Collector's default batches are far smaller.
MAX_BODY_BYTES = 16 << 20

_JSON_HINT = (
    "only OTLP JSON is accepted (Content-Type: application/json); set encoding: json on the "
    "Collector's otlphttp exporter"
)
_GZIP_INPUT_BYTES = 64 << 10
_GZIP_OUTPUT_BYTES = 1 << 20


class UnsupportedMediaType(Exception):
    """A content type or encoding this receiver does not read (HTTP 415)."""


class PayloadTooLarge(Exception):
    """A body over the size limit, before or after decompression (HTTP 413)."""


class InvalidBody(Exception):
    """A body that holds no OTLP JSON trace export (HTTP 400); `issues` are for the data notes."""

    def __init__(self, message: str, issues: list[Issue]) -> None:
        super().__init__(message)
        self.issues = issues


def parse_traces_body(
    body: bytes, content_type: str | None, content_encoding: str | None
) -> tuple[list[Span], list[Issue], int]:
    """Return the valid spans with tool results removed, the issues, and the rejected span count.

    The rejected count is the number of spans the parser refused (INVALID_SPAN issues). Spans
    under a scope that is itself malformed can't be counted; their INVALID_FILE issue says so.
    Raises UnsupportedMediaType, PayloadTooLarge or InvalidBody.
    """
    _check_content_type(content_type)
    document_bytes = _decode_content(body, content_encoding)
    if not document_bytes:
        raise _to_invalid_body("the request body is empty")
    try:
        # Explicitly UTF-8: JSON parsers given bytes also accept UTF-16 and UTF-32.
        text = document_bytes.decode("utf-8")
    except UnicodeDecodeError:
        raise _to_invalid_body("the request body is not UTF-8 text") from None
    document = parse_json_text(text)
    if document is None:
        raise _to_invalid_body("the request body is not valid JSON, or nests too deep")
    issues: list[Issue] = []
    spans = list(OtlpParser().parse_document(document, INGEST_SUBJECT, None, issues))
    malformed = [issue for issue in issues if issue.kind is IssueKind.INVALID_FILE]
    if not spans and malformed:
        raise InvalidBody(
            f"the request body is not an OTLP JSON trace export: {malformed[0].detail}", issues
        )
    rejected = sum(issue.kind is IssueKind.INVALID_SPAN for issue in issues)
    return [_remove_tool_result(span) for span in spans], issues, rejected


def _check_content_type(content_type: str | None) -> None:
    if content_type is None:
        raise UnsupportedMediaType(f"the request has no Content-Type; {_JSON_HINT}")
    media_type, *parameters = content_type.split(";")
    if media_type.strip().lower() != "application/json":
        raise UnsupportedMediaType(_JSON_HINT)
    for parameter in parameters:
        name, _, value = parameter.partition("=")
        if name.strip().lower() == "charset" and value.strip().strip('"').lower() != "utf-8":
            raise UnsupportedMediaType("OTLP JSON must be UTF-8; send charset=utf-8 or none")


def _decode_content(body: bytes, content_encoding: str | None) -> bytes:
    encoding = (content_encoding or "identity").strip().lower()
    if encoding == "gzip":
        return _decompress_gzip(body)
    if encoding != "identity":
        raise UnsupportedMediaType("Content-Encoding must be gzip, identity, or absent")
    if len(body) > MAX_DOCUMENT_BYTES:
        raise _to_payload_too_large()
    return body


def _decompress_gzip(body: bytes) -> bytes:
    """Decompress one gzip member, stopping as soon as the output passes the document limit.

    Input goes in small slices so a hostile stream costs at most the limit plus one slice's
    output, never its full decompressed size. Bytes after the member, including a second
    member, are rejected: the Collector sends exactly one.
    """
    decompressor = zlib.decompressobj(16 + zlib.MAX_WBITS)
    pieces: list[bytes] = []
    total = 0
    position = 0
    pending = b""
    try:
        while not decompressor.eof:
            if not pending and position < len(body):
                pending = body[position : position + _GZIP_INPUT_BYTES]
                position += len(pending)
            piece = decompressor.decompress(pending, _GZIP_OUTPUT_BYTES)
            pending = decompressor.unconsumed_tail
            if not piece and not pending and position >= len(body):
                break
            total += len(piece)
            if total > MAX_DOCUMENT_BYTES:
                raise _to_payload_too_large()
            pieces.append(piece)
    except zlib.error:
        raise _to_invalid_body("the request body is not valid gzip") from None
    if not decompressor.eof:
        raise _to_invalid_body("the gzip request body ends early")
    if decompressor.unused_data or position < len(body):
        raise _to_invalid_body("the gzip request body has bytes after its end")
    return b"".join(pieces)


def _to_payload_too_large() -> PayloadTooLarge:
    return PayloadTooLarge(
        f"the request body is over {MAX_DOCUMENT_BYTES >> 20} MiB uncompressed; lower "
        "send_batch_max_size in the Collector's batch processor"
    )


def _to_invalid_body(message: str) -> InvalidBody:
    return InvalidBody(message, [Issue(IssueKind.INVALID_FILE, INGEST_SUBJECT, message)])


def _remove_tool_result(span: Span) -> Span:
    if TOOL_CALL_RESULT not in span.attributes:
        return span
    attributes = {key: value for key, value in span.attributes.items() if key != TOOL_CALL_RESULT}
    return dataclasses.replace(span, attributes=attributes)
