from collections.abc import Iterator
from pathlib import Path

import pytest
from fastapi import Depends, FastAPI, Request
from fastapi.testclient import TestClient
from page_data import read_view
from starlette.websockets import WebSocketDisconnect

from detecttrace.serve.store import Store
from detecttrace.serve.ui import (
    UiState,
    create_ui_app,
    require_content_type,
    require_same_origin_write,
)

PORT = 8765
OTHER_PORT = 8766
ORIGIN = f"http://127.0.0.1:{PORT}"
WRITE = {"X-DetectTrace": "1", "Origin": ORIGIN}
EVIL_HOST = {"Host": f"evil.example.com:{PORT}"}
WRITE_PATH = "/test/write"
UPLOAD_PATH = "/test/upload"


@pytest.fixture
def app(tmp_path: Path, app_store: Store) -> FastAPI:
    """The app with a write route and an upload route that exist only in these tests."""
    ui_app = create_ui_app(port=PORT, state=UiState(store=app_store, data_dir=tmp_path))

    @ui_app.post(WRITE_PATH, dependencies=[Depends(require_same_origin_write(PORT))])
    async def write() -> dict[str, str]:
        return {}

    @ui_app.post(UPLOAD_PATH)
    async def upload(request: Request) -> dict[str, str]:
        require_content_type(request, "application/octet-stream")
        return {}

    return ui_app


@pytest.fixture
def client(app: FastAPI) -> Iterator[TestClient]:
    with TestClient(app, base_url=ORIGIN) as test_client:
        yield test_client


# The write guard


@pytest.mark.parametrize(
    "headers",
    [
        pytest.param({"Origin": ORIGIN}, id="no-header"),
        pytest.param({"X-DetectTrace": "0", "Origin": ORIGIN}, id="header-zero"),
        pytest.param({"X-DetectTrace": "1"}, id="no-origin"),
        pytest.param({"X-DetectTrace": "1", "Origin": "https://example.com"}, id="other-site"),
        pytest.param(
            {"X-DetectTrace": "1", "Origin": f"http://127.0.0.1:{OTHER_PORT}"}, id="other-port"
        ),
        pytest.param({"X-DetectTrace": "1", "Origin": f"https://127.0.0.1:{PORT}"}, id="https"),
    ],
)
def test_a_write_without_the_header_and_own_origin_is_forbidden(
    client: TestClient, headers: dict[str, str]
) -> None:
    response = client.post(WRITE_PATH, headers=headers)

    assert response.status_code == 403


@pytest.mark.parametrize(
    ("headers", "message"),
    [
        pytest.param({"Origin": ORIGIN}, "missing X-DetectTrace header", id="no-header"),
        pytest.param({"X-DetectTrace": "1"}, "cross-site request refused", id="no-origin"),
    ],
)
def test_a_refused_write_says_why(
    client: TestClient, headers: dict[str, str], message: str
) -> None:
    response = client.post(WRITE_PATH, headers=headers)

    assert response.json() == {"code": 7, "message": message}


@pytest.mark.parametrize("origin", [ORIGIN, f"http://localhost:{PORT}"])
def test_a_write_with_the_header_and_own_origin_reaches_the_route(
    client: TestClient, origin: str
) -> None:
    response = client.post(WRITE_PATH, headers={"X-DetectTrace": "1", "Origin": origin})

    assert response.status_code == 200


def test_a_body_of_the_wrong_media_type_is_unsupported(client: TestClient) -> None:
    response = client.post(UPLOAD_PATH, content=b"x", headers={"Content-Type": "text/plain"})

    assert response.status_code == 415


def test_a_body_without_a_media_type_is_unsupported(client: TestClient) -> None:
    response = client.post(UPLOAD_PATH, content=b"x")

    assert response.status_code == 415


def test_a_body_of_the_expected_media_type_is_accepted(client: TestClient) -> None:
    response = client.post(
        UPLOAD_PATH, content=b"x", headers={"Content-Type": "Application/Octet-Stream"}
    )

    assert response.status_code == 200


# The Host guard


@pytest.mark.parametrize(
    ("method", "path"),
    [("GET", "/"), ("GET", "/api/status"), ("GET", "/api/results.json"), ("POST", WRITE_PATH)],
)
def test_a_request_for_another_host_is_forbidden(
    client: TestClient, method: str, path: str
) -> None:
    response = client.request(method, path, headers={**WRITE, **EVIL_HOST})

    assert response.status_code == 403


def test_a_request_for_another_host_says_why(client: TestClient) -> None:
    response = client.get("/", headers=EVIL_HOST)

    assert response.json() == {"code": 7, "message": "unexpected Host header"}


@pytest.mark.parametrize(
    "host",
    [
        f"127.0.0.1:{OTHER_PORT}",
        "127.0.0.1",
        "localhost",
        f"127.0.0.1:{PORT}.evil.example.com",
        f"0.0.0.0:{PORT}",
    ],
)
def test_a_loopback_name_on_another_port_or_none_is_forbidden(
    client: TestClient, host: str
) -> None:
    response = client.get("/", headers={"Host": host})

    assert response.status_code == 403


@pytest.mark.parametrize("host", [f"127.0.0.1:{PORT}", f"localhost:{PORT}", f"LocalHost:{PORT}"])
def test_a_request_for_this_app_is_served(client: TestClient, host: str) -> None:
    response = client.get("/", headers={"Host": host})

    assert response.status_code == 200


def test_a_request_naming_this_app_and_another_host_is_forbidden(client: TestClient) -> None:
    response = client.get(
        "/", headers=[("Host", f"127.0.0.1:{PORT}"), ("Host", "evil.example.com")]
    )

    assert response.status_code == 403


def test_a_websocket_for_another_host_is_refused(client: TestClient) -> None:
    with (
        pytest.raises(WebSocketDisconnect) as refusal,
        client.websocket_connect("/ws", headers=EVIL_HOST),
    ):
        pass

    assert refusal.value.code == 1008


# No CORS


def test_the_page_allows_no_other_origin(client: TestClient) -> None:
    response = client.get("/", headers={"Origin": "https://example.com"})

    assert "access-control-allow-origin" not in response.headers


def test_a_preflight_is_not_approved(client: TestClient) -> None:
    response = client.options(
        WRITE_PATH,
        headers={
            "Origin": "https://example.com",
            "Access-Control-Request-Method": "POST",
            "Access-Control-Request-Headers": "x-detecttrace",
        },
    )

    assert "access-control-allow-origin" not in response.headers


# The read routes


def test_the_page_before_any_snapshot_is_the_ui_waiting_page(client: TestClient) -> None:
    response = client.get("/")

    assert read_view(response.text)["mode"] == "ui"


def test_the_page_forbids_framing(client: TestClient) -> None:
    response = client.get("/")

    assert response.headers["content-security-policy"] == "frame-ancestors 'none'"


@pytest.mark.parametrize("path", ["/versions", "/cases", "/data", "/help"])
def test_a_page_path_is_the_ui_page(client: TestClient, path: str) -> None:
    response = client.get(path)

    assert read_view(response.text)["mode"] == "ui"


@pytest.mark.parametrize("path", ["/versions", "/cases", "/data", "/help"])
def test_a_page_path_forbids_framing(client: TestClient, path: str) -> None:
    response = client.get(path)

    assert response.headers["content-security-policy"] == "frame-ancestors 'none'"


def test_a_page_path_for_another_host_is_refused(client: TestClient) -> None:
    response = client.get("/versions", headers=EVIL_HOST)

    assert response.json() == {"code": 7, "message": "unexpected Host header"}


@pytest.mark.parametrize("path", ["/versions/extra", "/Versions", "/favicon.ico"])
def test_a_path_that_names_no_page_is_not_found(client: TestClient, path: str) -> None:
    response = client.get(path)

    assert response.status_code == 404


@pytest.mark.parametrize(
    ("method", "path"),
    [("POST", "/v2"), ("PUT", "/zzz"), ("POST", "/cases"), ("GET", "/api")],
)
def test_a_request_for_no_page_is_not_found(client: TestClient, method: str, path: str) -> None:
    response = client.request(method, path, headers=WRITE)

    assert response.status_code == 404


def test_a_route_added_after_the_app_is_built_is_reached(tmp_path: Path, app_store: Store) -> None:
    ui_app = create_ui_app(port=PORT, state=UiState(store=app_store, data_dir=tmp_path))

    @ui_app.get("/metrics")
    def show_metrics() -> str:
        return "metrics"

    with TestClient(ui_app, base_url=ORIGIN) as test_client:
        response = test_client.get("/metrics")

    assert response.json() == "metrics"


def test_a_page_path_does_not_shadow_the_status_route(client: TestClient) -> None:
    response = client.get("/api/status")

    assert response.json()["recompute_running"] is False


def test_the_results_before_any_snapshot_are_unavailable(client: TestClient) -> None:
    response = client.get("/api/results.json")

    assert response.status_code == 503


def test_the_status_without_a_configuration_shows_no_recompute(client: TestClient) -> None:
    response = client.get("/api/status")

    assert response.json()["recompute_running"] is False


def test_the_health_check_is_ok(client: TestClient) -> None:
    response = client.get("/healthz")

    assert response.text == "ok"
