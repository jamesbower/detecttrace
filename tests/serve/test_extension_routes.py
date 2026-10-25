"""The routes seam: a wrapping app's routes under /api/ext/, behind the label token."""

from collections.abc import Iterator
from pathlib import Path

import httpx
import pytest
from fastapi import Request
from fastapi.responses import JSONResponse, Response
from fastapi.routing import APIRoute
from fastapi.testclient import TestClient

from detecttrace.serve import server
from detecttrace.serve.app import RouteError, create_app
from detecttrace.serve.config import load_serve_config
from detecttrace.serve.server import StartupError, run_server
from detecttrace.serve.store import Store
from serve.app_support import LABEL_TOKEN, READ_TOKEN, create_config
from serve.served_process import find_free_port, write_serve_config

JSON_HEADERS = {"Content-Type": "application/json"}


async def echo(request: Request) -> Response:
    return JSONResponse(
        {"token_name": request.state.token_name, "body": (await request.body()).decode()}
    )


ECHO = APIRoute("/api/ext/echo", echo, methods=["POST"])


@pytest.fixture
def ext_client(tmp_path: Path, app_store: Store) -> Iterator[TestClient]:
    app = create_app(
        create_config(tmp_path / "detecttrace.db"), app_store, lambda: None, routes=[ECHO]
    )
    with TestClient(app) as test_client:
        yield test_client


def post_echo(
    client: TestClient,
    token: str | None,
    body: bytes = b"{}",
    headers: dict[str, str] = JSON_HEADERS,
) -> httpx.Response:
    authorization = {} if token is None else {"Authorization": f"Bearer {token}"}
    return client.post("/api/ext/echo", content=body, headers={**headers, **authorization})


def test_a_route_is_reachable_with_a_label_token(ext_client: TestClient) -> None:
    assert post_echo(ext_client, LABEL_TOKEN).status_code == 200


def test_the_route_gets_the_tokens_name(ext_client: TestClient) -> None:
    assert post_echo(ext_client, LABEL_TOKEN).json()["token_name"] == "reviewers"


def test_the_route_gets_the_whole_body(ext_client: TestClient) -> None:
    assert post_echo(ext_client, LABEL_TOKEN, b'{"a": 1}').json()["body"] == '{"a": 1}'


def test_a_route_without_a_token_is_401(ext_client: TestClient) -> None:
    assert post_echo(ext_client, None).status_code == 401


def test_a_route_without_a_token_asks_for_a_bearer_token(ext_client: TestClient) -> None:
    assert post_echo(ext_client, None).headers.get_list("WWW-Authenticate") == ["Bearer"]


def test_a_route_with_a_read_token_is_403(ext_client: TestClient) -> None:
    assert post_echo(ext_client, READ_TOKEN).status_code == 403


def test_a_route_with_a_wrong_token_is_401(ext_client: TestClient) -> None:
    assert post_echo(ext_client, "not-a-token").status_code == 401


def test_a_label_token_may_not_read_the_dashboard(ext_client: TestClient) -> None:
    response = ext_client.get("/api/status", headers={"Authorization": f"Bearer {LABEL_TOKEN}"})
    assert response.status_code == 403


def test_a_body_over_1_mib_is_413(ext_client: TestClient) -> None:
    assert post_echo(ext_client, LABEL_TOKEN, b" " * ((1 << 20) + 1)).status_code == 413


def test_a_body_of_another_media_type_is_415(ext_client: TestClient) -> None:
    response = post_echo(ext_client, LABEL_TOKEN, headers={"Content-Type": "text/plain"})
    assert response.status_code == 415


def test_a_body_in_another_charset_is_415(ext_client: TestClient) -> None:
    headers = {"Content-Type": "application/json; charset=latin-1"}
    assert post_echo(ext_client, LABEL_TOKEN, headers=headers).status_code == 415


def test_a_body_with_no_media_type_is_415(ext_client: TestClient) -> None:
    assert post_echo(ext_client, LABEL_TOKEN, headers={}).status_code == 415


def test_the_token_is_checked_before_the_media_type(ext_client: TestClient) -> None:
    response = post_echo(ext_client, None, headers={"Content-Type": "text/plain"})
    assert response.status_code == 401


def test_another_method_on_a_route_is_405(ext_client: TestClient) -> None:
    response = ext_client.get("/api/ext/echo", headers={"Authorization": f"Bearer {LABEL_TOKEN}"})
    assert response.status_code == 405


def test_a_route_outside_the_prefix_is_refused(tmp_path: Path, app_store: Store) -> None:
    outside = APIRoute("/api/echo", echo, methods=["POST"])
    with pytest.raises(RouteError, match="/api/ext/"):
        create_app(
            create_config(tmp_path / "detecttrace.db"), app_store, lambda: None, routes=[outside]
        )


def test_a_route_mounted_twice_is_refused(tmp_path: Path, app_store: Store) -> None:
    with pytest.raises(RouteError, match="twice"):
        create_app(
            create_config(tmp_path / "detecttrace.db"), app_store, lambda: None, routes=[ECHO, ECHO]
        )


def test_run_server_refuses_a_route_outside_the_prefix(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # The server returns as soon as it would listen.
    monkeypatch.setattr(server, "_run_until_signalled", lambda *args: None)
    config_path = write_serve_config(tmp_path, find_free_port())
    outside = APIRoute("/api/echo", echo, methods=["POST"])
    with pytest.raises(StartupError, match="/api/ext/"):
        run_server(load_serve_config(config_path), config_path, lambda line: None, routes=[outside])
