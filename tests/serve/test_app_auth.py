import base64
import json
import sqlite3
from collections.abc import Iterator
from pathlib import Path
from typing import Annotated, NoReturn

import httpx
import pytest
from builders import otlp_document, otlp_span, span_hex
from fastapi import Depends
from fastapi.testclient import TestClient

from detecttrace.serve.app import create_app, require_role
from detecttrace.serve.store import Store
from serve.app_support import (
    INGEST_TOKEN,
    READ_TOKEN,
    VERDICTS_TOKEN,
    create_config,
)

BODY_HEADERS = {"Content-Type": "application/json"}


def post_traces(client: TestClient, authorization: str | None) -> httpx.Response:
    headers = dict(BODY_HEADERS)
    if authorization is not None:
        headers["Authorization"] = authorization
    body = json.dumps(otlp_document([otlp_span(span_hex(1))])).encode("utf-8")
    return client.post("/v1/traces", content=body, headers=headers)


def to_basic(user: str, password: str) -> str:
    return "Basic " + base64.b64encode(f"{user}:{password}".encode()).decode("ascii")


def fail_with_locked_database(*args: object, **kwargs: object) -> NoReturn:
    raise sqlite3.OperationalError("database is locked")


@pytest.fixture
def basic_client(tmp_path: Path, app_store: Store) -> Iterator[TestClient]:
    """An app with one probe route that, like the read routes to come, also takes Basic."""
    config = create_config(tmp_path / "detecttrace.db")
    app = create_app(config, app_store, lambda: None)
    require_read = require_role(config.tokens, "read", allow_basic=True)

    @app.get("/probe")
    def probe(name: Annotated[str, Depends(require_read)]) -> str:
        return name

    with TestClient(app) as test_client:
        yield test_client


def test_missing_header_is_unauthorized(client: TestClient) -> None:
    assert post_traces(client, None).status_code == 401


def test_missing_header_asks_for_a_bearer_token(client: TestClient) -> None:
    assert post_traces(client, None).headers["WWW-Authenticate"] == "Bearer"


def test_unknown_token_is_unauthorized(client: TestClient) -> None:
    assert post_traces(client, "Bearer not-a-real-token").status_code == 401


@pytest.mark.parametrize("token", [READ_TOKEN, VERDICTS_TOKEN])
def test_token_of_another_role_is_forbidden(client: TestClient, token: str) -> None:
    assert post_traces(client, f"Bearer {token}").status_code == 403


def test_basic_with_the_ingest_token_is_unauthorized_on_a_write_route(client: TestClient) -> None:
    assert post_traces(client, to_basic("x", INGEST_TOKEN)).status_code == 401


@pytest.mark.parametrize(
    "authorization",
    [
        pytest.param(f"bearer {INGEST_TOKEN}", id="lowercase-scheme"),
        pytest.param(f"BEARER {INGEST_TOKEN}", id="uppercase-scheme"),
        pytest.param(f"Bearer   {INGEST_TOKEN}", id="extra-spaces"),
    ],
)
def test_bearer_scheme_is_case_insensitive_and_spacing_tolerant(
    client: TestClient, authorization: str
) -> None:
    assert post_traces(client, authorization).status_code == 200


@pytest.mark.parametrize(
    "authorization",
    [
        pytest.param("Bearer", id="no-token"),
        pytest.param(f"Bearer {INGEST_TOKEN} extra", id="two-tokens"),
        pytest.param(INGEST_TOKEN, id="no-scheme"),
        pytest.param(f"Token {INGEST_TOKEN}", id="other-scheme"),
    ],
)
def test_malformed_header_is_unauthorized(client: TestClient, authorization: str) -> None:
    assert post_traces(client, authorization).status_code == 401


def test_unauthorized_response_is_a_status_body(client: TestClient) -> None:
    assert post_traces(client, None).json()["code"] == 16


@pytest.mark.parametrize(
    ("authorization", "token"),
    [
        pytest.param(f"Bearer {INGEST_TOKEN}x", INGEST_TOKEN, id="401"),
        pytest.param(f"Bearer {READ_TOKEN}", READ_TOKEN, id="403"),
    ],
)
def test_response_never_contains_the_token(
    client: TestClient, authorization: str, token: str
) -> None:
    response = post_traces(client, authorization)
    assert token not in response.text + str(response.headers)


def test_healthz_needs_no_token(client: TestClient) -> None:
    assert client.get("/healthz").text == "ok"


def test_healthz_answers_503_when_the_database_takes_no_writes(
    client: TestClient, app_store: Store, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(app_store, "check_writable", fail_with_locked_database)
    assert client.get("/healthz").status_code == 503


def test_failed_write_answers_503(
    client: TestClient, app_store: Store, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(app_store, "add_spans", fail_with_locked_database)
    assert post_traces(client, f"Bearer {INGEST_TOKEN}").status_code == 503


def test_failed_write_asks_the_client_to_retry(
    client: TestClient, app_store: Store, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(app_store, "add_spans", fail_with_locked_database)
    assert post_traces(client, f"Bearer {INGEST_TOKEN}").headers["Retry-After"] == "5"


def test_failed_write_does_not_signal_a_write(
    client: TestClient, app_store: Store, writes: list[int], monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(app_store, "add_spans", fail_with_locked_database)
    post_traces(client, f"Bearer {INGEST_TOKEN}")
    assert writes == []


def test_api_docs_are_not_served(client: TestClient) -> None:
    assert client.get("/openapi.json").status_code == 404


def test_read_route_takes_the_read_token_as_the_basic_password(basic_client: TestClient) -> None:
    response = basic_client.get("/probe", headers={"Authorization": to_basic("any", READ_TOKEN)})
    assert response.json() == "analysts"


def test_read_route_takes_a_bearer_token(basic_client: TestClient) -> None:
    response = basic_client.get("/probe", headers={"Authorization": f"Bearer {READ_TOKEN}"})
    assert response.json() == "analysts"


@pytest.mark.parametrize(
    "authorization",
    [
        pytest.param("Basic !!!", id="not-base64"),
        pytest.param("Basic " + base64.b64encode(b"no-colon").decode("ascii"), id="no-colon"),
        pytest.param(to_basic("any", "wrong"), id="wrong-password"),
    ],
)
def test_bad_basic_credentials_are_unauthorized(
    basic_client: TestClient, authorization: str
) -> None:
    response = basic_client.get("/probe", headers={"Authorization": authorization})
    assert response.status_code == 401


def test_non_ascii_basic_credentials_are_unauthorized(basic_client: TestClient) -> None:
    response = basic_client.get("/probe", headers={"Authorization": b"Basic \xe9\xe9\xe9\xe9"})
    assert response.status_code == 401


def test_read_route_challenge_offers_basic(basic_client: TestClient) -> None:
    response = basic_client.get("/probe")
    assert response.headers["WWW-Authenticate"] == 'Bearer, Basic realm="detecttrace"'
