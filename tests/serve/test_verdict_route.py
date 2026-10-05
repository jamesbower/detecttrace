import base64
import json
import sqlite3
import threading
from collections.abc import Callable
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from typing import NoReturn

import httpx
import pytest
from builders import otlp_document, otlp_span, span_hex
from fastapi.testclient import TestClient

from detecttrace.model import MAX_LABEL_LENGTH, VerdictRow
from detecttrace.serve import verdict_api
from detecttrace.serve.app import MAX_CONCURRENT_INGESTS, MAX_CONCURRENT_VERDICT_POSTS
from detecttrace.serve.store import Store
from serve.app_support import (
    INGEST_TOKEN,
    READ_TOKEN,
    SECOND_VERDICTS_TOKEN,
    VERDICTS_TOKEN,
    ConcurrencyProbe,
)

JSON = "application/json"
CSV = "text/csv"
CSV_HEADER = "case_id,alert_class,verdict\n"
BEARER = f"Bearer {VERDICTS_TOKEN}"


def item(case_id: str = "DT-1", verdict: str = "TP") -> dict[str, str]:
    return {"case_id": case_id, "alert_class": "impossible_travel", "verdict": verdict}


def json_body(*items: object) -> bytes:
    return json.dumps({"verdicts": list(items)}).encode("utf-8")


def post_verdicts(
    client: TestClient,
    body: bytes,
    content_type: str = JSON,
    authorization: str | None = BEARER,
) -> httpx.Response:
    headers = {"Content-Type": content_type}
    if authorization is not None:
        headers["Authorization"] = authorization
    return client.post("/api/verdicts", content=body, headers=headers)


def to_basic(password: str) -> str:
    return "Basic " + base64.b64encode(f"x:{password}".encode()).decode("ascii")


def fail_with_locked_database(*args: object, **kwargs: object) -> NoReturn:
    raise sqlite3.OperationalError("database is locked")


def read_current(database: Path) -> list[tuple[str, str, str]]:
    with sqlite3.connect(database) as connection:
        return connection.execute(
            "SELECT case_id, label, token_name FROM verdicts ORDER BY case_id"
        ).fetchall()


def read_history(database: Path) -> list[tuple[str, str, str]]:
    with sqlite3.connect(database) as connection:
        return connection.execute(
            "SELECT case_id, label, token_name FROM verdict_history ORDER BY id"
        ).fetchall()


STORED_ROW = VerdictRow("DT-1", "impossible_travel", "TP", 0)
LONG_CASE_ID = "x" * (MAX_LABEL_LENGTH + 1)
MID_FILE_BAD_QUOTE = f'{CSV_HEADER}DT-1,x,TP\nDT-2,"x\ny"z,TP\nDT-3,a,TP\n'.encode()
VALID_FORMS = [
    pytest.param(json_body(item()), JSON, id="json"),
    pytest.param(f"{CSV_HEADER}DT-1,impossible_travel,TP\n".encode(), CSV, id="csv"),
]
# Requests refused before anything is stored, with their status.
REFUSED = [
    pytest.param(json_body(item()), JSON, None, 401, id="no-header"),
    pytest.param(json_body(item()), JSON, to_basic(VERDICTS_TOKEN), 401, id="basic"),
    pytest.param(json_body(item()), JSON, f"Bearer {INGEST_TOKEN}", 403, id="ingest-token"),
    pytest.param(json_body(item()), JSON, f"Bearer {READ_TOKEN}", 403, id="read-token"),
    pytest.param(b" " * (verdict_api.MAX_BODY_BYTES + 1), JSON, BEARER, 413, id="over-4-mib"),
    pytest.param(
        json_body(*[item(f"DT-{index}") for index in range(verdict_api.MAX_ROWS + 1)]),
        JSON,
        BEARER,
        413,
        id="too-many-rows",
    ),
    pytest.param(b"<verdicts/>", "application/xml", BEARER, 415, id="xml"),
    pytest.param(MID_FILE_BAD_QUOTE, CSV, BEARER, 400, id="csv-bad-quote-mid-file"),
    pytest.param(b"[]", JSON, BEARER, 400, id="not-an-object"),
]
# Bodies whose every row is rejected.
NONE_ACCEPTED = [
    pytest.param(json_body(), JSON, id="empty-list"),
    pytest.param(CSV_HEADER.encode("utf-8"), CSV, id="header-only-csv"),
    pytest.param(json_body(item(verdict="MAYBE")), JSON, id="unmapped-label"),
]
EMPTY_BODIES = [
    pytest.param(json_body(), JSON, id="empty-list"),
    pytest.param(CSV_HEADER.encode("utf-8"), CSV, id="header-only-csv"),
]
TRACE_BODY = json.dumps(otlp_document([otlp_span(span_hex(1))])).encode("utf-8")


@pytest.mark.parametrize(("body", "content_type"), VALID_FORMS)
def test_verdict_is_stored(
    client: TestClient, app_store: Store, body: bytes, content_type: str
) -> None:
    post_verdicts(client, body, content_type)
    assert app_store.read_inputs().verdict_rows == [STORED_ROW]


@pytest.mark.parametrize("charset", ["utf-8", "utf8", '"UTF8"'])
def test_utf_8_charset_is_accepted(client: TestClient, charset: str) -> None:
    response = post_verdicts(client, json_body(item()), f"{JSON}; charset={charset}")
    assert response.status_code == 200


EXACT_BODY = json_body(item())


def post_declared(client: TestClient) -> httpx.Response:
    return post_verdicts(client, EXACT_BODY)


def post_streamed(client: TestClient) -> httpx.Response:
    # An iterator is sent chunked, with no Content-Length, so only the read counts the bytes.
    headers = {"Content-Type": JSON, "Authorization": BEARER}
    chunks = iter([EXACT_BODY[:10], EXACT_BODY[10:]])
    return client.post("/api/verdicts", content=chunks, headers=headers)


@pytest.mark.parametrize("post", [post_declared, post_streamed])
def test_body_exactly_at_the_limit_is_accepted(
    client: TestClient,
    monkeypatch: pytest.MonkeyPatch,
    post: Callable[[TestClient], httpx.Response],
) -> None:
    monkeypatch.setattr(verdict_api, "MAX_BODY_BYTES", len(EXACT_BODY))
    assert post(client).status_code == 200


@pytest.mark.parametrize(("body", "content_type"), VALID_FORMS)
def test_stored_verdict_is_acknowledged(client: TestClient, body: bytes, content_type: str) -> None:
    assert post_verdicts(client, body, content_type).json() == {"accepted": 1, "rejected": []}


def test_mixed_body_is_accepted(client: TestClient) -> None:
    response = post_verdicts(client, json_body(item(), item("DT-2", "MAYBE")))
    assert response.status_code == 200


def test_mixed_body_stores_the_valid_rows(client: TestClient, app_store: Store) -> None:
    post_verdicts(client, json_body(item(), item("DT-2", "MAYBE")))
    assert app_store.read_inputs().verdict_rows == [STORED_ROW]


def test_mixed_body_lists_each_rejection_with_where_and_why(client: TestClient) -> None:
    response = post_verdicts(client, json_body(item(), item("DT-2", "MAYBE")))
    assert response.json()["rejected"] == [
        {"where": "verdicts[1]", "reason": "verdict label 'MAYBE' is not in label_map"}
    ]


@pytest.mark.parametrize(("body", "content_type"), NONE_ACCEPTED)
def test_body_with_no_accepted_row_is_unprocessable(
    client: TestClient, body: bytes, content_type: str
) -> None:
    assert post_verdicts(client, body, content_type).status_code == 422


@pytest.mark.parametrize(("body", "content_type"), NONE_ACCEPTED)
def test_body_with_no_accepted_row_stores_nothing(
    client: TestClient, app_store: Store, body: bytes, content_type: str
) -> None:
    post_verdicts(client, body, content_type)
    assert app_store.generation() == 0


@pytest.mark.parametrize(("body", "content_type"), NONE_ACCEPTED)
def test_body_with_no_accepted_row_signals_no_write(
    client: TestClient, writes: list[int], body: bytes, content_type: str
) -> None:
    post_verdicts(client, body, content_type)
    assert writes == []


def test_unprocessable_body_keeps_the_response_shape(client: TestClient) -> None:
    response = post_verdicts(client, json_body(item(verdict="MAYBE")))
    assert response.json() == {
        "accepted": 0,
        "rejected": [
            {"where": "verdicts[0]", "reason": "verdict label 'MAYBE' is not in label_map"}
        ],
    }


def test_unprocessable_body_with_a_stored_note_signals_a_write(
    client: TestClient, writes: list[int]
) -> None:
    post_verdicts(client, json_body(item(LONG_CASE_ID, "MAYBE")))
    assert writes == [1]


def test_accepted_body_signals_one_write(client: TestClient, writes: list[int]) -> None:
    post_verdicts(client, json_body(item()))
    assert writes == [1]


def test_second_verdict_for_a_case_replaces_the_first(client: TestClient, app_store: Store) -> None:
    post_verdicts(client, json_body(item()))
    post_verdicts(
        client, json_body(item(verdict="FP")), authorization=f"Bearer {SECOND_VERDICTS_TOKEN}"
    )
    assert [row.label for row in app_store.read_inputs().verdict_rows] == ["FP"]


def test_replaced_verdict_is_kept_in_history_with_its_token_name(
    client: TestClient, tmp_path: Path
) -> None:
    post_verdicts(client, json_body(item()))
    post_verdicts(
        client, json_body(item(verdict="FP")), authorization=f"Bearer {SECOND_VERDICTS_TOKEN}"
    )
    assert read_history(tmp_path / "detecttrace.db") == [("DT-1", "TP", "soar")]


def test_identical_repost_leaves_the_generation_unchanged(
    client: TestClient, app_store: Store
) -> None:
    post_verdicts(client, json_body(item()))
    generation = app_store.generation()
    post_verdicts(client, json_body(item()))
    assert app_store.generation() == generation


@pytest.mark.parametrize(("body", "content_type", "authorization", "status"), REFUSED)
def test_refused_request_gets_its_status(
    client: TestClient, body: bytes, content_type: str, authorization: str | None, status: int
) -> None:
    assert post_verdicts(client, body, content_type, authorization).status_code == status


@pytest.mark.parametrize(("body", "content_type", "authorization", "status"), REFUSED)
def test_refused_request_stores_nothing(
    client: TestClient,
    app_store: Store,
    body: bytes,
    content_type: str,
    authorization: str | None,
    status: int,
) -> None:
    post_verdicts(client, body, content_type, authorization)
    assert app_store.generation() == 0


@pytest.mark.parametrize(("body", "content_type", "authorization", "status"), REFUSED)
def test_refused_request_signals_no_write(
    client: TestClient,
    writes: list[int],
    body: bytes,
    content_type: str,
    authorization: str | None,
    status: int,
) -> None:
    post_verdicts(client, body, content_type, authorization)
    assert writes == []


def test_refusal_is_a_status_body(client: TestClient) -> None:
    response = post_verdicts(client, b"<verdicts/>", "application/xml")
    assert response.json()["code"] == 3


def test_failed_write_answers_503(
    client: TestClient, app_store: Store, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(app_store, "put_verdicts", fail_with_locked_database)
    assert post_verdicts(client, json_body(item())).status_code == 503


def test_failed_write_asks_the_client_to_retry(
    client: TestClient, app_store: Store, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(app_store, "put_verdicts", fail_with_locked_database)
    assert post_verdicts(client, json_body(item())).headers["Retry-After"] == "5"


def test_failed_note_after_stored_verdicts_answers_503(
    client: TestClient, app_store: Store, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(app_store, "add_issues", fail_with_locked_database)
    response = post_verdicts(client, json_body(item(), item(LONG_CASE_ID)))
    assert response.status_code == 503


def test_failed_note_after_stored_verdicts_still_signals_the_write(
    client: TestClient, app_store: Store, writes: list[int], monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(app_store, "add_issues", fail_with_locked_database)
    post_verdicts(client, json_body(item(), item(LONG_CASE_ID)))
    assert writes == [1]


def test_failed_note_without_stored_verdicts_signals_no_write(
    client: TestClient, app_store: Store, writes: list[int], monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(app_store, "add_issues", fail_with_locked_database)
    post_verdicts(client, json_body(item(LONG_CASE_ID, "MAYBE")))
    assert writes == []


@pytest.mark.parametrize(("body", "content_type"), EMPTY_BODIES)
def test_body_without_rows_says_so(client: TestClient, body: bytes, content_type: str) -> None:
    assert post_verdicts(client, body, content_type).json()["rejected"] == [
        {"where": "body", "reason": "the request has no verdict rows"}
    ]


def test_replacing_verdict_records_the_new_token_name(client: TestClient, tmp_path: Path) -> None:
    post_verdicts(client, json_body(item()))
    post_verdicts(
        client, json_body(item(verdict="FP")), authorization=f"Bearer {SECOND_VERDICTS_TOKEN}"
    )
    assert read_current(tmp_path / "detecttrace.db") == [("DT-1", "FP", "case-tool")]


def test_concurrent_verdict_posts_are_limited(
    client: TestClient, app_store: Store, monkeypatch: pytest.MonkeyPatch
) -> None:
    requests = MAX_CONCURRENT_VERDICT_POSTS + 1
    probe = ConcurrencyProbe(requests, 1)
    monkeypatch.setattr(app_store, "put_verdicts", probe)
    with ThreadPoolExecutor(requests) as executor:
        list(executor.map(lambda _: post_verdicts(client, json_body(item())), range(requests)))
    assert probe.peak <= MAX_CONCURRENT_VERDICT_POSTS


def test_held_verdict_posts_do_not_stall_span_ingest(
    client: TestClient, app_store: Store, monkeypatch: pytest.MonkeyPatch
) -> None:
    condition = threading.Condition()
    release = threading.Event()
    inside = [0]

    def hold(*args: object, **kwargs: object) -> int:
        with condition:
            inside[0] += 1
            condition.notify_all()
        release.wait(timeout=10)
        return 1

    monkeypatch.setattr(app_store, "put_verdicts", hold)
    posts = MAX_CONCURRENT_INGESTS
    with ThreadPoolExecutor(posts + 1) as executor:
        try:
            for _ in range(posts):
                executor.submit(post_verdicts, client, json_body(item()))
            # Give every verdict post the chance to take a slot; with shared slots they
            # would take all of the ingest ones.
            with condition:
                condition.wait_for(lambda: inside[0] >= posts, timeout=0.5)
            trace = executor.submit(
                client.post,
                "/v1/traces",
                content=TRACE_BODY,
                headers={"Content-Type": JSON, "Authorization": f"Bearer {INGEST_TOKEN}"},
            )
            status = trace.result(timeout=2).status_code
        finally:
            release.set()
    assert status == 200
