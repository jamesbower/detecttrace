import base64
import json
import sqlite3
from pathlib import Path
from typing import NoReturn

import httpx
import pytest
from fastapi.testclient import TestClient

from detecttrace.model import MAX_LABEL_LENGTH, VerdictRow
from detecttrace.serve import verdict_api
from detecttrace.serve.store import Store
from serve.app_support import INGEST_TOKEN, READ_TOKEN, SECOND_VERDICTS_TOKEN, VERDICTS_TOKEN

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
    pytest.param(json_body(), id="empty-list"),
    pytest.param(json_body(item(verdict="MAYBE")), id="unmapped-label"),
]


@pytest.mark.parametrize(("body", "content_type"), VALID_FORMS)
def test_verdict_is_stored(
    client: TestClient, app_store: Store, body: bytes, content_type: str
) -> None:
    post_verdicts(client, body, content_type)
    assert app_store.read_inputs().verdict_rows == [STORED_ROW]


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


@pytest.mark.parametrize("body", NONE_ACCEPTED)
def test_body_with_no_accepted_row_is_unprocessable(client: TestClient, body: bytes) -> None:
    assert post_verdicts(client, body).status_code == 422


@pytest.mark.parametrize("body", NONE_ACCEPTED)
def test_body_with_no_accepted_row_stores_nothing(
    client: TestClient, app_store: Store, body: bytes
) -> None:
    post_verdicts(client, body)
    assert app_store.generation() == 0


@pytest.mark.parametrize("body", NONE_ACCEPTED)
def test_body_with_no_accepted_row_signals_no_write(
    client: TestClient, writes: list[int], body: bytes
) -> None:
    post_verdicts(client, body)
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
