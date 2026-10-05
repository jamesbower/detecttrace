"""Turn an OTLP/HTTP trace export request body into spans; no web framework involved.

Only OTLP JSON is read, plain or gzip, as the Collector's `otlp_http` exporter sends it with
`encoding: json`. Tool results are dropped here, before anything is stored.
"""

import dataclasses
import zlib

from detecttrace.conventions import TOOL_CALL_RESULT
from detecttrace.jsontext import parse_json_text
from detecttrace.model import Issue, IssueKind, Span
from detecttrace.otlp import OtlpParser
from detecttrace.serve.mediatype import to_media_type
from detecttrace.serve.store import INGEST_SUBJECT
from detecttrace.traces import MAX_DOCUMENT_BYTES

# As received, before decompression; the Collector's default batches are far smaller.
MAX_BODY_BYTES = 16 << 20
# Concatenated gzip members are valid gzip; the cap stops a body of thousands of empty ones.
MAX_GZIP_MEMBERS = 64

_JSON_HINT = (
    "only OTLP JSON is accepted (Content-Type: application/json); set encoding: json on the "
    "Collector's otlp_http exporter"
)
_GZIP_INPUT_BYTES = 64 << 10
_GZIP_OUTPUT_BYTES = 1 << 20


class UnsupportedMediaType(Exception):
    """A content type or encoding this receiver does not read (HTTP 415)."""


class PayloadTooLarge(Exception):
    """A body over the size limit, before or after decompression (HTTP 413)."""


class InvalidBody(Exception):
    """A body that holds no OTLP JSON trace export (HTTP 400)."""


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
        # parse_json_text also gives None for the JSON value null.
        if text.strip() == "null":
            raise _to_invalid_body("the request body is not a JSON object")
        raise _to_invalid_body("the request body is not valid JSON, or nests too deep")
    issues: list[Issue] = []
    spans = list(OtlpParser().parse_document(document, INGEST_SUBJECT, None, issues))
    malformed = [issue for issue in issues if issue.kind is IssueKind.INVALID_FILE]
    if not spans and malformed:
        raise InvalidBody(
            f"the request body is not an OTLP JSON trace export: {malformed[0].detail}"
        )
    rejected = sum(issue.kind is IssueKind.INVALID_SPAN for issue in issues)
    return [_remove_tool_result(span) for span in spans], issues, rejected


def _check_content_type(content_type: str | None) -> None:
    if content_type is None:
        raise UnsupportedMediaType(f"the request has no Content-Type; {_JSON_HINT}")
    media_type, is_utf8 = to_media_type(content_type)
    if media_type != "application/json":
        raise UnsupportedMediaType(_JSON_HINT)
    if not is_utf8:
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
    """Decompress every gzip member, stopping as soon as the output passes the document limit.

    Input goes in small slices so a hostile stream costs at most the limit plus one slice's
    output, never its full decompressed size. All members share the one limit; more than
    MAX_GZIP_MEMBERS is a malformed body (400), not a large one, since it may be tiny.
    """
    pieces: list[bytes] = []
    total = 0
    position = 0
    member_count = 0
    try:
        while member_count == 0 or position < len(body):
            member_count += 1
            if member_count > MAX_GZIP_MEMBERS:
                raise _to_invalid_body(
                    f"the gzip request body has more than {MAX_GZIP_MEMBERS} members"
                )
            decompressor = zlib.decompressobj(16 + zlib.MAX_WBITS)
            pending = b""
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
            if not decompressor.eof:
                raise _to_invalid_body("the gzip request body ends early")
            # The next member starts with the bytes this one did not use.
            position -= len(decompressor.unused_data)
    except zlib.error:
        raise _to_invalid_body("the request body is not valid gzip") from None
    return b"".join(pieces)


def _to_payload_too_large() -> PayloadTooLarge:
    return PayloadTooLarge(
        f"the request body is over {MAX_DOCUMENT_BYTES >> 20} MiB uncompressed; lower "
        "send_batch_max_size in the Collector's batch processor"
    )


def _to_invalid_body(message: str) -> InvalidBody:
    return InvalidBody(message)


def _remove_tool_result(span: Span) -> Span:
    if TOOL_CALL_RESULT not in span.attributes:
        return span
    attributes = {key: value for key, value in span.attributes.items() if key != TOOL_CALL_RESULT}
    return dataclasses.replace(span, attributes=attributes)
