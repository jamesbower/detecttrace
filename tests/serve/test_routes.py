"""The read routes, the headers every response carries, and the access log."""

import base64
import json
import logging
import sqlite3
from collections.abc import Callable, Iterator
from pathlib import Path
from typing import NoReturn

import httpx
import pytest
from fastapi.testclient import TestClient

from detecttrace.model import Issue, IssueKind, VerdictRow
from detecttrace.serve.app import create_app
from detecttrace.serve.recompute import RecomputeStatus
from detecttrace.serve.store import Snapshot, Store
from serve.app_support import INGEST_TOKEN, READ_TOKEN, VERDICTS_TOKEN, create_config

# 2026-10-05T12:00:00.123456Z
FINISHED_AT_NS = 1_791_201_600_123_456_789
FINISHED_AT = "2026-10-05T12:00:00.123456Z"
ERROR_AT_NS = 1_791_201_660_000_000_000
ERROR_AT = "2026-10-05T12:01:00.000000Z"
SNAPSHOT = Snapshot(3, FINISHED_AT_NS, "<!DOCTYPE html><p>stored page</p>", '{"cases":[]}')
STATUS = RecomputeStatus(
    is_running=True,
    last_error="ValueError: bad input",
    last_error_at_ns=ERROR_AT_NS,
    held_back_cases=7,
)
SECURITY_HEADERS = [
    ("X-Content-Type-Options", "nosniff"),
    ("Referrer-Policy", "no-referrer"),
    ("Cache-Control", "no-store"),
]
BEARER_READ = {"Authorization": f"Bearer {READ_TOKEN}"}
SECRET_QUERY = "query-secret-value"


def to_basic(password: str) -> dict[str, str]:
    credentials = base64.b64encode(f"anyone:{password}".encode()).decode("ascii")
    return {"Authorization": f"Basic {credentials}"}


@pytest.fixture
def served_store(app_store: Store) -> Store:
    """A store whose input is at generation 5 and whose snapshot is from generation 3."""
    for number in range(4):
        app_store.add_issues([Issue(IssueKind.INVALID_FILE, f"file-{number}")])
    app_store.put_verdicts([VerdictRow("DT-1", "phishing", "TP", 0)], "soar")
    app_store.write_snapshot(SNAPSHOT)
    return app_store


@pytest.fixture
def read_client(tmp_path: Path, served_store: Store) -> Iterator[TestClient]:
    app = create_app(
        create_config(tmp_path / "detecttrace.db"), served_store, lambda: None, lambda: STATUS
    )
    with TestClient(app) as test_client:
        yield test_client


@pytest.fixture
def status_body(read_client: TestClient) -> dict[str, object]:
    return read_client.get("/api/status", headers=BEARER_READ).json()


@pytest.fixture
def empty_status_body(client: TestClient) -> dict[str, object]:
    return client.get("/api/status", headers=BEARER_READ).json()


def test_page_with_basic_read_token_is_the_stored_html(read_client: TestClient) -> None:
    assert read_client.get("/", headers=to_basic(READ_TOKEN)).text == SNAPSHOT.html


def test_page_is_html(read_client: TestClient) -> None:
    response = read_client.get("/", headers=BEARER_READ)
    assert response.headers["Content-Type"] == "text/html; charset=utf-8"


def test_page_with_bearer_read_token_is_ok(read_client: TestClient) -> None:
    assert read_client.get("/", headers=BEARER_READ).status_code == 200


@pytest.mark.parametrize("token", [INGEST_TOKEN, VERDICTS_TOKEN])
def test_page_with_a_write_token_is_forbidden(read_client: TestClient, token: str) -> None:
    assert read_client.get("/", headers={"Authorization": f"Bearer {token}"}).status_code == 403


def test_page_without_a_token_is_unauthorized(read_client: TestClient) -> None:
    assert read_client.get("/").status_code == 401


def test_page_without_a_token_offers_each_scheme_in_its_own_header(
    read_client: TestClient,
) -> None:
    # A browser shows its sign-in prompt only when Basic leads a header of its own.
    assert read_client.get("/").headers.get_list("WWW-Authenticate") == [
        "Bearer",
        'Basic realm="detecttrace"',
    ]


def test_page_before_the_first_recompute_is_the_waiting_page(client: TestClient) -> None:
    assert "Waiting for data." in client.get("/", headers=BEARER_READ).text


def test_waiting_page_before_the_first_recompute_is_ok(client: TestClient) -> None:
    assert client.get("/", headers=BEARER_READ).status_code == 200


def test_waiting_page_before_the_first_recompute_is_generation_zero(client: TestClient) -> None:
    assert 'data-generation="0"' in client.get("/", headers=BEARER_READ).text


def test_status_generation_is_the_stored_snapshots(status_body: dict[str, object]) -> None:
    assert status_body["generation"] == 3


def test_status_updated_at_is_when_the_snapshot_finished(status_body: dict[str, object]) -> None:
    assert status_body["updated_at"] == FINISHED_AT


def test_status_reports_a_running_recompute(status_body: dict[str, object]) -> None:
    assert status_body["recompute_running"] is True


def test_status_reports_the_last_error(status_body: dict[str, object]) -> None:
    assert status_body["last_error"] == "ValueError: bad input"


def test_status_reports_when_the_last_error_happened(status_body: dict[str, object]) -> None:
    assert status_body["last_error_at"] == ERROR_AT


def test_status_reports_the_held_back_cases(status_body: dict[str, object]) -> None:
    assert status_body["held_back_cases"] == 7


def test_status_reports_the_verdict_count(status_body: dict[str, object]) -> None:
    assert status_body["verdict_count"] == 1


def test_status_reports_the_span_count(status_body: dict[str, object]) -> None:
    assert status_body["span_count"] == 0


def test_status_reports_the_last_ingest_time(status_body: dict[str, object]) -> None:
    assert isinstance(status_body["last_ingest_at"], str)


def test_status_has_exactly_the_documented_fields(status_body: dict[str, object]) -> None:
    assert sorted(status_body) == [
        "generation",
        "held_back_cases",
        "last_error",
        "last_error_at",
        "last_ingest_at",
        "recompute_running",
        "span_count",
        "updated_at",
        "verdict_count",
    ]


def test_status_before_the_first_recompute_is_generation_zero(
    empty_status_body: dict[str, object],
) -> None:
    assert empty_status_body["generation"] == 0


def test_status_before_the_first_recompute_has_no_update_time(
    empty_status_body: dict[str, object],
) -> None:
    assert empty_status_body["updated_at"] is None


def test_status_without_a_status_source_is_idle(empty_status_body: dict[str, object]) -> None:
    assert empty_status_body["recompute_running"] is False


def test_status_before_any_write_has_no_ingest_time(empty_status_body: dict[str, object]) -> None:
    assert empty_status_body["last_ingest_at"] is None


def test_status_needs_a_read_token(read_client: TestClient) -> None:
    assert read_client.get("/api/status").status_code == 401


def test_results_are_the_stored_json(read_client: TestClient) -> None:
    assert read_client.get("/api/results.json", headers=BEARER_READ).json() == json.loads(
        SNAPSHOT.results_json
    )


def test_results_are_json(read_client: TestClient) -> None:
    response = read_client.get("/api/results.json", headers=BEARER_READ)
    assert response.headers["Content-Type"] == "application/json"


def test_results_before_the_first_recompute_are_unavailable(client: TestClient) -> None:
    assert client.get("/api/results.json", headers=BEARER_READ).status_code == 503


def test_results_before_the_first_recompute_ask_for_a_retry(client: TestClient) -> None:
    assert client.get("/api/results.json", headers=BEARER_READ).headers["Retry-After"] == "5"


def test_results_before_the_first_recompute_answer_a_status_body(client: TestClient) -> None:
    assert client.get("/api/results.json", headers=BEARER_READ).json()["code"] == 14


def get_page(test_client: TestClient) -> httpx.Response:
    return test_client.get("/", headers=BEARER_READ)


def get_status(test_client: TestClient) -> httpx.Response:
    return test_client.get("/api/status", headers=BEARER_READ)


def get_unauthorized(test_client: TestClient) -> httpx.Response:
    return test_client.get("/")


def post_verdicts(test_client: TestClient) -> httpx.Response:
    return test_client.post(
        "/api/verdicts",
        json={"verdicts": []},
        headers={"Authorization": f"Bearer {VERDICTS_TOKEN}"},
    )


def get_health(test_client: TestClient) -> httpx.Response:
    return test_client.get("/healthz")


@pytest.mark.parametrize(
    "request_route", [get_page, get_status, get_unauthorized, post_verdicts, get_health]
)
@pytest.mark.parametrize(("name", "value"), SECURITY_HEADERS)
def test_every_response_carries_the_security_header(
    read_client: TestClient,
    request_route: Callable[[TestClient], httpx.Response],
    name: str,
    value: str,
) -> None:
    assert request_route(read_client).headers.get_list(name) == [value]


def test_page_forbids_framing(read_client: TestClient) -> None:
    assert get_page(read_client).headers["Content-Security-Policy"] == "frame-ancestors 'none'"


def test_waiting_page_forbids_framing(client: TestClient) -> None:
    assert get_page(client).headers["Content-Security-Policy"] == "frame-ancestors 'none'"


def test_status_has_no_policy_header(read_client: TestClient) -> None:
    assert "Content-Security-Policy" not in get_status(read_client).headers


@pytest.fixture
def access_log(read_client: TestClient, caplog: pytest.LogCaptureFixture) -> str:
    with caplog.at_level(logging.INFO, logger="detecttrace.serve.access"):
        read_client.get(f"/api/status?token={SECRET_QUERY}", headers=BEARER_READ)
    return caplog.text


def test_access_log_names_the_method_path_and_status(access_log: str) -> None:
    assert "GET /api/status 200" in access_log


def test_access_log_omits_the_token(access_log: str) -> None:
    assert READ_TOKEN not in access_log


def test_access_log_omits_the_query(access_log: str) -> None:
    assert SECRET_QUERY not in access_log


def test_access_log_escapes_a_line_break_in_the_path(
    read_client: TestClient, caplog: pytest.LogCaptureFixture
) -> None:
    with caplog.at_level(logging.INFO, logger="detecttrace.serve.access"):
        read_client.get("/a%0Afake", headers=BEARER_READ)
    assert "\nfake" not in caplog.text


def fail_with_locked_database(*args: object, **kwargs: object) -> NoReturn:
    raise sqlite3.OperationalError("database is locked")


def fail_with_damaged_database(*args: object, **kwargs: object) -> NoReturn:
    raise sqlite3.DatabaseError("database disk image is malformed")


READ_PATHS = ["/", "/api/status", "/api/results.json"]


@pytest.fixture(params=READ_PATHS)
def locked_read(
    request: pytest.FixtureRequest,
    read_client: TestClient,
    served_store: Store,
    monkeypatch: pytest.MonkeyPatch,
) -> httpx.Response:
    monkeypatch.setattr(served_store, "read_snapshot", fail_with_locked_database)
    return read_client.get(request.param, headers=BEARER_READ)


def test_a_locked_database_makes_a_read_route_unavailable(locked_read: httpx.Response) -> None:
    assert locked_read.status_code == 503


def test_a_locked_database_asks_a_reader_to_retry(locked_read: httpx.Response) -> None:
    assert locked_read.headers["Retry-After"] == "5"


@pytest.fixture
def crashed_response(
    tmp_path: Path, served_store: Store, monkeypatch: pytest.MonkeyPatch
) -> httpx.Response:
    monkeypatch.setattr(served_store, "read_snapshot", fail_with_damaged_database)
    app = create_app(create_config(tmp_path / "detecttrace.db"), served_store, lambda: None)
    with TestClient(app, raise_server_exceptions=False) as test_client:
        return test_client.get("/api/status", headers=BEARER_READ)


def test_an_unexpected_error_answers_500(crashed_response: httpx.Response) -> None:
    assert crashed_response.status_code == 500


@pytest.mark.parametrize(("name", "value"), SECURITY_HEADERS)
def test_an_unexpected_error_still_carries_the_security_header(
    crashed_response: httpx.Response, name: str, value: str
) -> None:
    assert crashed_response.headers.get_list(name) == [value]
