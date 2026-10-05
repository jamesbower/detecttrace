import functools
import gzip
import json
import sqlite3
import tracemalloc
import zlib
from collections.abc import Callable
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from pathlib import Path
from typing import NoReturn

import httpx
import pytest
from builders import otlp_document, otlp_span, span_hex
from fastapi.testclient import TestClient
from starlette.types import Message

from detecttrace.conventions import TOOL_CALL_RESULT
from detecttrace.model import IssueKind
from detecttrace.serve.app import MAX_CONCURRENT_INGESTS, create_app
from detecttrace.serve.receiver import (
    MAX_BODY_BYTES,
    MAX_GZIP_MEMBERS,
    InvalidBody,
    PayloadTooLarge,
    UnsupportedMediaType,
    parse_traces_body,
)
from detecttrace.serve.store import INGEST_SUBJECT, AddSpansResult, Store
from detecttrace.traces import MAX_DOCUMENT_BYTES
from serve.app_support import (
    INGEST_TOKEN,
    READ_TOKEN,
    ConcurrencyProbe,
    create_config,
    read_collector_request,
    run_raw_request,
)

JSON = "application/json"


def fail_with_locked_database(*args: object, **kwargs: object) -> NoReturn:
    raise sqlite3.OperationalError("database is locked")


def make_body(*spans: dict[str, object]) -> bytes:
    return json.dumps(otlp_document(list(spans))).encode("utf-8")


VALID_BODY = make_body(otlp_span(span_hex(1)))
TOOL_RESULT_BODY = make_body(
    otlp_span(span_hex(2), attributes={TOOL_CALL_RESULT: "secret", "gen_ai.tool.name": "lookup"})
)
ONE_BAD_SPAN_BODY = make_body(
    otlp_span(span_hex(3)), otlp_span("not-a-span-id"), otlp_span(span_hex(4))
)
DEEP_BODY = ('{"a": ' + "[" * 10_000 + "]" * 10_000 + "}").encode("utf-8")
# A null scope (a document-level problem) beside a valid one.
SKIPPED_SCOPE_BODY = json.dumps(
    {"resourceSpans": [{"scopeSpans": [None, {"spans": [otlp_span(span_hex(5))]}]}]}
).encode("utf-8")
# A null scope beside a scope with one invalid span and two valid ones.
SKIPPED_SCOPE_AND_BAD_SPAN_BODY = json.dumps(
    {
        "resourceSpans": [
            {
                "scopeSpans": [
                    None,
                    {
                        "spans": [
                            otlp_span(span_hex(6)),
                            otlp_span("not-a-span-id"),
                            otlp_span(span_hex(7)),
                        ]
                    },
                ]
            }
        ]
    }
).encode("utf-8")
INVALID_UTF_8_VALUE_BODY = make_body(
    otlp_span(span_hex(8), attributes={"gen_ai.tool.name": "PLACEHOLDER"})
).replace(b"PLACEHOLDER", b"look\xffup")


@functools.cache
def create_gzip_bomb() -> bytes:
    """1 GiB of zeros as gzip, built in pieces so the test never holds the 1 GiB."""
    compressor = zlib.compressobj(9, zlib.DEFLATED, 16 + zlib.MAX_WBITS)
    zeros = bytes(1 << 20)
    parts = [compressor.compress(zeros) for _ in range(1 << 10)]
    return b"".join([*parts, compressor.flush()])


def read_truncated_gzip() -> bytes:
    body, _ = read_collector_request("json_gzip_1.json.gz")
    return body[: len(body) // 2]


@dataclass(frozen=True)
class Post:
    # A function for bodies too costly to build at import.
    body: bytes | Callable[[], bytes]
    content_type: str | None = JSON
    content_encoding: str | None = None
    token: str | None = INGEST_TOKEN

    def read_body(self) -> bytes:
        return self.body() if callable(self.body) else self.body

    def send(self, client: TestClient) -> httpx.Response:
        headers: dict[str, str] = {}
        if self.content_type is not None:
            headers["Content-Type"] = self.content_type
        if self.content_encoding is not None:
            headers["Content-Encoding"] = self.content_encoding
        if self.token is not None:
            headers["Authorization"] = f"Bearer {self.token}"
        return client.post("/v1/traces", content=self.read_body(), headers=headers)


def send_protobuf(client: TestClient) -> httpx.Response:
    body, headers = read_collector_request("protobuf_gzip_1.pb.gz")
    return client.post("/v1/traces", content=body, headers=headers)


INVALID_BODIES = [
    pytest.param(Post(read_truncated_gzip, content_encoding="gzip"), id="truncated-gzip"),
    pytest.param(Post(body="{}".encode("utf-16")), id="utf-16"),
    pytest.param(Post(body=b'{"resourceSpans": "\xff"}'), id="invalid-utf-8"),
    pytest.param(Post(body=INVALID_UTF_8_VALUE_BODY), id="invalid-utf-8-in-a-value"),
    pytest.param(Post(body=VALID_BODY.decode("utf-8").encode("utf-16")), id="utf-16-otlp"),
    pytest.param(Post(body=b"null"), id="null"),
    pytest.param(Post(body=b'{"resourceSpans": ['), id="invalid-json"),
    pytest.param(Post(body=b"[]"), id="array"),
    pytest.param(Post(body=b"{}"), id="no-resource-spans"),
    pytest.param(Post(body=DEEP_BODY), id="deep-nesting"),
    pytest.param(Post(body=b""), id="empty"),
]
# Bodies refused unread or unparsed: only a data note is stored, so a Collector left on the
# wrong encoding shows up on the dashboard.
UNREADABLE_POSTS = [
    ("no-content-type", Post(body=VALID_BODY, content_type=None), 415),
    ("latin-1", Post(body=VALID_BODY, content_type="application/json; charset=latin-1"), 415),
    ("brotli", Post(body=VALID_BODY, content_encoding="br"), 415),
    ("gzip-bomb", Post(create_gzip_bomb, content_encoding="gzip"), 413),
    ("over-16-mib", Post(body=b" " * (MAX_BODY_BYTES + 1)), 413),
]
UNREADABLE = [pytest.param(post, status, id=name) for name, post, status in UNREADABLE_POSTS]
# Requests refused before anything is stored.
UNAUTHORIZED = [
    pytest.param(Post(body=VALID_BODY, token=None), 401, id="no-token"),
    pytest.param(Post(body=VALID_BODY, token=READ_TOKEN), 403, id="read-token"),
]
REFUSED = [*UNREADABLE, *UNAUTHORIZED]
REJECTED = [
    *[pytest.param(param.values[0], 400, id=param.id) for param in INVALID_BODIES],
    *REFUSED,
]


# parse_traces_body


@pytest.mark.parametrize(
    "content_type",
    ["application/x-protobuf", "text/plain", "application/jsonx"],
)
def test_content_type_other_than_json_is_unsupported(content_type: str) -> None:
    with pytest.raises(UnsupportedMediaType):
        parse_traces_body(VALID_BODY, content_type, None)


def test_protobuf_message_says_how_to_switch_to_json() -> None:
    with pytest.raises(UnsupportedMediaType, match="encoding: json"):
        parse_traces_body(VALID_BODY, "application/x-protobuf", None)


def test_missing_content_type_is_unsupported() -> None:
    with pytest.raises(UnsupportedMediaType):
        parse_traces_body(VALID_BODY, None, None)


@pytest.mark.parametrize(
    "content_type",
    ["application/json", "Application/JSON", 'application/json; charset="UTF-8"'],
)
def test_json_content_type_is_accepted(content_type: str) -> None:
    assert len(parse_traces_body(VALID_BODY, content_type, None)[0]) == 1


def test_charset_other_than_utf_8_is_unsupported() -> None:
    with pytest.raises(UnsupportedMediaType):
        parse_traces_body(VALID_BODY, "application/json; charset=latin-1", None)


@pytest.mark.parametrize("encoding", ["br", "deflate", "zstd", "gzip, gzip", "x-gzip"])
def test_encoding_other_than_gzip_is_unsupported(encoding: str) -> None:
    with pytest.raises(UnsupportedMediaType):
        parse_traces_body(VALID_BODY, JSON, encoding)


@pytest.mark.parametrize("encoding", [None, "", "identity", "IDENTITY"])
def test_identity_encoding_reads_the_body_as_is(encoding: str | None) -> None:
    assert len(parse_traces_body(VALID_BODY, JSON, encoding)[0]) == 1


def test_gzip_encoding_is_decompressed() -> None:
    assert len(parse_traces_body(gzip.compress(VALID_BODY), JSON, "GZIP")[0]) == 1


def test_truncated_gzip_is_invalid() -> None:
    with pytest.raises(InvalidBody):
        parse_traces_body(read_truncated_gzip(), JSON, "gzip")


def test_bytes_after_the_gzip_stream_are_invalid() -> None:
    with pytest.raises(InvalidBody):
        parse_traces_body(gzip.compress(VALID_BODY) + b"junk", JSON, "gzip")


def test_body_that_is_not_gzip_is_invalid() -> None:
    with pytest.raises(InvalidBody):
        parse_traces_body(VALID_BODY, JSON, "gzip")


def test_gzip_bomb_is_too_large() -> None:
    with pytest.raises(PayloadTooLarge):
        parse_traces_body(create_gzip_bomb(), JSON, "gzip")


def test_gzip_bomb_is_stopped_within_bounded_memory() -> None:
    bomb = create_gzip_bomb()
    tracemalloc.start()
    try:
        with pytest.raises(PayloadTooLarge):
            parse_traces_body(bomb, JSON, "gzip")
        _, peak = tracemalloc.get_traced_memory()
    finally:
        tracemalloc.stop()
    assert peak < 100 << 20


@pytest.mark.parametrize("post", INVALID_BODIES)
def test_invalid_body_is_rejected(post: Post) -> None:
    with pytest.raises(InvalidBody):
        parse_traces_body(post.read_body(), JSON, post.content_encoding)


@pytest.mark.parametrize("post", INVALID_BODIES)
def test_invalid_body_carries_an_issue(post: Post) -> None:
    with pytest.raises(InvalidBody) as caught:
        parse_traces_body(post.read_body(), JSON, post.content_encoding)
    assert caught.value.issues


def test_tool_result_is_removed() -> None:
    spans, _, _ = parse_traces_body(TOOL_RESULT_BODY, JSON, None)
    assert TOOL_CALL_RESULT not in spans[0].attributes


def test_other_attributes_are_kept_when_the_tool_result_is_removed() -> None:
    spans, _, _ = parse_traces_body(TOOL_RESULT_BODY, JSON, None)
    assert spans[0].attributes == {"gen_ai.tool.name": "lookup"}


def test_invalid_span_is_counted_as_rejected() -> None:
    assert parse_traces_body(ONE_BAD_SPAN_BODY, JSON, None)[2] == 1


def test_invalid_span_is_reported() -> None:
    _, issues, _ = parse_traces_body(ONE_BAD_SPAN_BODY, JSON, None)
    assert [issue.kind for issue in issues] == [IssueKind.INVALID_SPAN]


def test_valid_spans_beside_an_invalid_one_are_kept() -> None:
    assert len(parse_traces_body(ONE_BAD_SPAN_BODY, JSON, None)[0]) == 2


def test_null_body_is_named_as_not_an_object() -> None:
    with pytest.raises(InvalidBody, match="not a JSON object"):
        parse_traces_body(b"null", JSON, None)


def test_spans_under_a_skipped_scope_are_not_counted_as_rejected() -> None:
    assert parse_traces_body(SKIPPED_SCOPE_AND_BAD_SPAN_BODY, JSON, None)[2] == 1


def test_gzip_members_are_read_in_turn() -> None:
    middle = len(VALID_BODY) // 2
    body = gzip.compress(VALID_BODY[:middle]) + gzip.compress(VALID_BODY[middle:])
    assert len(parse_traces_body(body, JSON, "gzip")[0]) == 1


def test_exactly_max_gzip_members_are_accepted() -> None:
    body = gzip.compress(VALID_BODY) + gzip.compress(b"") * (MAX_GZIP_MEMBERS - 1)
    assert len(parse_traces_body(body, JSON, "gzip")[0]) == 1


def test_gzip_member_without_its_trailer_ends_early() -> None:
    with pytest.raises(InvalidBody, match="ends early"):
        parse_traces_body(gzip.compress(VALID_BODY)[:-8], JSON, "gzip")


def test_too_many_gzip_members_are_invalid() -> None:
    body = gzip.compress(VALID_BODY) + gzip.compress(b"") * MAX_GZIP_MEMBERS
    with pytest.raises(InvalidBody):
        parse_traces_body(body, JSON, "gzip")


def test_gzip_members_share_one_size_limit() -> None:
    member = gzip.compress(b" " * (MAX_DOCUMENT_BYTES // 2 + 1))
    with pytest.raises(PayloadTooLarge):
        parse_traces_body(member + member, JSON, "gzip")


def test_document_with_empty_resource_spans_is_accepted() -> None:
    assert parse_traces_body(b'{"resourceSpans": []}', JSON, None) == ([], [], 0)


# POST /v1/traces


@pytest.mark.parametrize(("post", "status"), REJECTED)
def test_rejected_request_gets_its_status(client: TestClient, post: Post, status: int) -> None:
    assert post.send(client).status_code == status


@pytest.mark.parametrize(("post", "status"), REJECTED)
def test_rejected_request_stores_no_spans(
    client: TestClient, app_store: Store, post: Post, status: int
) -> None:
    post.send(client)
    assert app_store.read_counts().span_count == 0


@pytest.mark.parametrize(("post", "status"), UNAUTHORIZED)
def test_unauthorized_request_does_not_signal_a_write(
    client: TestClient, writes: list[int], post: Post, status: int
) -> None:
    post.send(client)
    assert writes == []


@pytest.mark.parametrize(("post", "status"), UNAUTHORIZED)
def test_unauthorized_request_records_no_issue(
    client: TestClient, app_store: Store, post: Post, status: int
) -> None:
    post.send(client)
    assert app_store.read_inputs().issues == []


def send_streamed_oversize(client: TestClient) -> httpx.Response:
    chunks = iter([b" " * (1 << 20)] * 17)
    return client.post(
        "/v1/traces",
        content=chunks,
        headers={"Content-Type": JSON, "Authorization": f"Bearer {INGEST_TOKEN}"},
    )


def send_declared_oversize(client: TestClient) -> httpx.Response:
    headers = {
        "Content-Type": JSON,
        "Authorization": f"Bearer {INGEST_TOKEN}",
        "Content-Length": str(MAX_BODY_BYTES + 1),
    }
    return client.post("/v1/traces", content=b"", headers=headers)


UNREADABLE_SENDS = [
    *[pytest.param(post.send, id=name) for name, post, _ in UNREADABLE_POSTS],
    pytest.param(send_protobuf, id="protobuf"),
    pytest.param(send_streamed_oversize, id="streamed-over-16-mib"),
    pytest.param(send_declared_oversize, id="declared-over-16-mib"),
]


@pytest.mark.parametrize("send", UNREADABLE_SENDS)
def test_unreadable_body_is_recorded_as_an_ingest_issue(
    client: TestClient, app_store: Store, send: Callable[[TestClient], httpx.Response]
) -> None:
    send(client)
    assert [
        (stored.issue.kind, stored.issue.subject) for stored in app_store.read_inputs().issues
    ] == [(IssueKind.INVALID_FILE, INGEST_SUBJECT)]


@pytest.mark.parametrize("send", UNREADABLE_SENDS)
def test_unreadable_body_signals_one_write_for_its_issue(
    client: TestClient, writes: list[int], send: Callable[[TestClient], httpx.Response]
) -> None:
    send(client)
    assert writes == [1]


def test_repeated_protobuf_requests_are_counted_in_one_issue(
    client: TestClient, app_store: Store
) -> None:
    send_protobuf(client)
    send_protobuf(client)
    assert [stored.count for stored in app_store.read_inputs().issues] == [2]


def test_unrecordable_unreadable_body_answers_503(
    client: TestClient, app_store: Store, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(app_store, "add_issues", fail_with_locked_database)
    assert send_protobuf(client).status_code == 503


@pytest.mark.parametrize("post", INVALID_BODIES)
def test_invalid_body_signals_one_write_for_its_issue(
    client: TestClient, writes: list[int], post: Post
) -> None:
    post.send(client)
    assert writes == [1]


@pytest.mark.parametrize("post", INVALID_BODIES)
def test_invalid_body_is_recorded_as_an_issue(
    client: TestClient, app_store: Store, post: Post
) -> None:
    post.send(client)
    assert [stored.issue.kind for stored in app_store.read_inputs().issues] == [
        IssueKind.INVALID_FILE
    ]


def test_invalid_body_gets_a_status_body(client: TestClient) -> None:
    assert Post(body=b"[]").send(client).json()["code"] == 3


def test_body_over_the_limit_without_a_length_is_too_large(client: TestClient) -> None:
    assert send_streamed_oversize(client).status_code == 413


def test_protobuf_request_is_unsupported(client: TestClient) -> None:
    assert send_protobuf(client).status_code == 415


def test_protobuf_response_says_how_to_switch_to_json(client: TestClient) -> None:
    assert "encoding: json" in send_protobuf(client).json()["message"]


def test_protobuf_response_names_the_current_exporter(client: TestClient) -> None:
    assert "otlp_http exporter" in send_protobuf(client).json()["message"]


def test_protobuf_request_stores_no_spans(client: TestClient, app_store: Store) -> None:
    send_protobuf(client)
    assert app_store.read_counts().span_count == 0


@pytest.mark.parametrize("charset", ["utf-8", "utf8", '"UTF8"'])
def test_utf_8_charset_is_accepted(client: TestClient, charset: str) -> None:
    post = Post(body=VALID_BODY, content_type=f"application/json; charset={charset}")
    assert post.send(client).status_code == 200


@pytest.mark.parametrize(
    ("name", "count"), [("json_gzip_1.json.gz", 68), ("json_gzip_2.json.gz", 126)]
)
def test_collector_request_is_accepted(client: TestClient, name: str, count: int) -> None:
    body, headers = read_collector_request(name)
    assert client.post("/v1/traces", content=body, headers=headers).status_code == 200


@pytest.mark.parametrize(
    ("name", "count"), [("json_gzip_1.json.gz", 68), ("json_gzip_2.json.gz", 126)]
)
def test_collector_request_stores_every_span(
    client: TestClient, app_store: Store, name: str, count: int
) -> None:
    body, headers = read_collector_request(name)
    client.post("/v1/traces", content=body, headers=headers)
    assert app_store.read_counts().span_count == count


def test_accepted_request_answers_with_an_empty_export_response(client: TestClient) -> None:
    assert Post(body=VALID_BODY).send(client).json() == {}


def test_stored_span_has_no_tool_result(client: TestClient, app_store: Store) -> None:
    Post(body=TOOL_RESULT_BODY).send(client)
    assert TOOL_CALL_RESULT not in app_store.read_inputs().spans[0].attributes


def test_no_collector_span_is_stored_with_a_tool_result(
    client: TestClient, app_store: Store
) -> None:
    body, headers = read_collector_request("json_gzip_2.json.gz")
    client.post("/v1/traces", content=body, headers=headers)
    stored = app_store.read_inputs().spans
    assert not [span for span in stored if TOOL_CALL_RESULT in span.attributes]


def test_one_invalid_span_is_a_partial_success(client: TestClient) -> None:
    response = Post(body=ONE_BAD_SPAN_BODY).send(client)
    assert response.json()["partialSuccess"]["rejectedSpans"] == 1


def test_partial_success_signals_a_write(client: TestClient, writes: list[int]) -> None:
    Post(body=ONE_BAD_SPAN_BODY).send(client)
    assert writes == [1]


def test_skipped_scope_is_a_partial_success_with_no_rejected_spans(client: TestClient) -> None:
    response = Post(body=SKIPPED_SCOPE_BODY).send(client)
    assert response.json()["partialSuccess"]["rejectedSpans"] == 0


def test_skipped_scope_partial_success_explains_itself(client: TestClient) -> None:
    response = Post(body=SKIPPED_SCOPE_BODY).send(client)
    assert response.json()["partialSuccess"]["errorMessage"]


def test_rejected_count_ignores_skipped_scopes(client: TestClient) -> None:
    response = Post(body=SKIPPED_SCOPE_AND_BAD_SPAN_BODY).send(client)
    assert response.json()["partialSuccess"]["rejectedSpans"] == 1


def test_declared_length_over_the_limit_is_refused_unread(client: TestClient) -> None:
    assert send_declared_oversize(client).status_code == 413


def test_partial_success_is_still_200(client: TestClient) -> None:
    assert Post(body=ONE_BAD_SPAN_BODY).send(client).status_code == 200


def test_partial_success_stores_the_valid_spans(client: TestClient, app_store: Store) -> None:
    Post(body=ONE_BAD_SPAN_BODY).send(client)
    assert app_store.read_counts().span_count == 2


def test_partial_success_records_the_invalid_span(client: TestClient, app_store: Store) -> None:
    Post(body=ONE_BAD_SPAN_BODY).send(client)
    assert [stored.issue.kind for stored in app_store.read_inputs().issues] == [
        IssueKind.INVALID_SPAN
    ]


def test_each_accepted_request_signals_one_write(client: TestClient, writes: list[int]) -> None:
    Post(body=VALID_BODY).send(client)
    Post(body=TOOL_RESULT_BODY).send(client)
    assert writes == [1, 1]


def test_retried_request_is_accepted(client: TestClient) -> None:
    body, headers = read_collector_request("json_gzip_1.json.gz")
    client.post("/v1/traces", content=body, headers=headers)
    assert client.post("/v1/traces", content=body, headers=headers).status_code == 200


def test_retried_request_leaves_the_generation_unchanged(
    client: TestClient, app_store: Store
) -> None:
    body, headers = read_collector_request("json_gzip_1.json.gz")
    client.post("/v1/traces", content=body, headers=headers)
    generation = app_store.generation()
    client.post("/v1/traces", content=body, headers=headers)
    assert app_store.generation() == generation


def test_concurrent_ingests_are_limited(
    client: TestClient, app_store: Store, monkeypatch: pytest.MonkeyPatch
) -> None:
    requests = MAX_CONCURRENT_INGESTS + 1
    probe = ConcurrencyProbe(requests, AddSpansResult(1, 0, 0))
    monkeypatch.setattr(app_store, "add_spans", probe)
    with ThreadPoolExecutor(requests) as executor:
        list(executor.map(lambda _: Post(body=VALID_BODY).send(client), range(requests)))
    assert probe.peak <= MAX_CONCURRENT_INGESTS


def test_client_gone_before_sending_its_body_is_handled_quietly(
    tmp_path: Path, app_store: Store
) -> None:
    app = create_app(create_config(tmp_path / "detecttrace.db"), app_store, lambda: None)

    async def receive() -> Message:
        return {"type": "http.disconnect"}

    sent = run_raw_request(app, "/v1/traces", INGEST_TOKEN, receive)
    assert sent[0]["status"] == 400
