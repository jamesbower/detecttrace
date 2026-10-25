"""The routes seam: a wrapping app's routes under /api/ext/, behind the label token."""

from collections.abc import Iterator
from itertools import repeat
from pathlib import Path

import httpx
import pytest
from fastapi import Request
from fastapi.responses import JSONResponse, Response
from fastapi.routing import APIRoute
from fastapi.testclient import TestClient
from pydantic import BaseModel

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


class Item(BaseModel):
    name: str


async def show_item(item_id: int) -> dict[str, int]:
    return {"item_id": item_id}


async def create_item(item: Item) -> dict[str, str]:
    return {"name": item.name}


ECHO = APIRoute("/api/ext/echo", echo, methods=["POST"])
ROUTES = [
    ECHO,
    APIRoute("/api/ext/both", echo, methods=["GET", "POST"]),
    APIRoute("/api/ext/edit", echo, methods=["PUT", "PATCH"]),
    APIRoute("/api/ext/items/{item_id}", show_item, methods=["GET"]),
    APIRoute("/api/ext/items", create_item, methods=["POST"]),
]
OVER_LIMIT = b" " * ((1 << 20) + 1)
LABEL_HEADERS = {"Authorization": f"Bearer {LABEL_TOKEN}"}
# Each request FastAPI's own validation refuses: a body that doesn't fit its model, and a
# path parameter that isn't an int. Neither value may come back in the answer.
INVALID_REQUESTS = [
    ("POST", "/api/ext/items", b'{"name": ["echoed-value"]}'),
    ("GET", "/api/ext/items/echoed-value", b""),
]


@pytest.fixture
def ext_client(tmp_path: Path, app_store: Store) -> Iterator[TestClient]:
    app = create_app(
        create_config(tmp_path / "detecttrace.db"), app_store, lambda: None, routes=ROUTES
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
    assert post_echo(ext_client, LABEL_TOKEN, OVER_LIMIT).status_code == 413


def test_a_chunked_body_over_1_mib_is_413(ext_client: TestClient) -> None:
    # An iterator body goes out chunked, with no Content-Length to refuse it by.
    chunks = repeat(b" " * (1 << 16), 17)
    response = ext_client.post(
        "/api/ext/echo", content=chunks, headers={**JSON_HEADERS, **LABEL_HEADERS}
    )
    assert response.status_code == 413


def test_a_body_over_1_mib_without_a_token_is_401(ext_client: TestClient) -> None:
    assert post_echo(ext_client, None, OVER_LIMIT).status_code == 401


def test_the_media_type_is_checked_before_the_body_size(ext_client: TestClient) -> None:
    response = post_echo(ext_client, LABEL_TOKEN, OVER_LIMIT, {"Content-Type": "text/plain"})
    assert response.status_code == 415


def test_a_body_of_another_media_type_is_415(ext_client: TestClient) -> None:
    response = post_echo(ext_client, LABEL_TOKEN, headers={"Content-Type": "text/plain"})
    assert response.status_code == 415


@pytest.mark.parametrize("method", ["PUT", "PATCH"])
def test_a_put_or_patch_of_another_media_type_is_415(ext_client: TestClient, method: str) -> None:
    headers = {"Content-Type": "text/plain", **LABEL_HEADERS}
    response = ext_client.request(method, "/api/ext/edit", content=b"{}", headers=headers)
    assert response.status_code == 415


def test_a_body_of_another_media_type_is_told_what_to_send(ext_client: TestClient) -> None:
    response = post_echo(ext_client, LABEL_TOKEN, headers={"Content-Type": "text/plain"})
    assert response.json()["message"] == "send the request body as application/json"


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


@pytest.mark.parametrize("method", ["GET", "POST"])
def test_each_method_of_a_route_needs_a_token(ext_client: TestClient, method: str) -> None:
    response = ext_client.request(method, "/api/ext/both", content=b"{}", headers=JSON_HEADERS)
    assert response.status_code == 401


def test_head_on_a_get_route_needs_a_token(ext_client: TestClient) -> None:
    assert ext_client.head("/api/ext/items/7").status_code == 401


def test_a_path_parameter_reaches_the_route(ext_client: TestClient) -> None:
    response = ext_client.get("/api/ext/items/7", headers=LABEL_HEADERS)
    assert response.json() == {"item_id": 7}


@pytest.mark.parametrize(("method", "path", "body"), INVALID_REQUESTS)
def test_a_request_the_route_cannot_validate_is_400(
    ext_client: TestClient, method: str, path: str, body: bytes
) -> None:
    headers = {**JSON_HEADERS, **LABEL_HEADERS}
    response = ext_client.request(method, path, content=body, headers=headers)
    assert response.status_code == 400


@pytest.mark.parametrize(("method", "path", "body"), INVALID_REQUESTS)
def test_a_request_the_route_cannot_validate_gets_a_status_body_without_its_input(
    ext_client: TestClient, method: str, path: str, body: bytes
) -> None:
    headers = {**JSON_HEADERS, **LABEL_HEADERS}
    response = ext_client.request(method, path, content=body, headers=headers)
    assert response.json() == {"code": 3, "message": "the request is not valid for this route"}


def test_a_route_mounted_twice_under_other_parameter_names_is_refused(
    tmp_path: Path, app_store: Store
) -> None:
    first = APIRoute("/api/ext/a/{x}", echo, methods=["POST"])
    second = APIRoute("/api/ext/a/{y}", echo, methods=["POST"])
    with pytest.raises(RouteError, match="twice"):
        create_app(
            create_config(tmp_path / "detecttrace.db"),
            app_store,
            lambda: None,
            routes=[first, second],
        )


def test_the_bare_prefix_is_refused(tmp_path: Path, app_store: Store) -> None:
    bare = APIRoute("/api/ext/", echo, methods=["POST"])
    with pytest.raises(RouteError, match="nothing after"):
        create_app(
            create_config(tmp_path / "detecttrace.db"), app_store, lambda: None, routes=[bare]
        )
