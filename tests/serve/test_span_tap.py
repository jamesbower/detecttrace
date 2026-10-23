"""The span tap: a wrapping app sees each span batch with its tool results, before the CE
strips and stores it, and a failing tap never changes what the CE stores or answers."""

import json
import logging
from collections.abc import Callable, Iterator
from pathlib import Path
from typing import NoReturn

import httpx
import pytest
from builders import otlp_document, otlp_span, span_hex
from fastapi.testclient import TestClient

from detecttrace.conventions import TOOL_CALL_RESULT
from detecttrace.model import Span
from detecttrace.serve.app import create_app
from detecttrace.serve.store import Store
from serve.app_support import INGEST_TOKEN, create_config

TOOL_RESULT_BODY = json.dumps(
    otlp_document(
        [otlp_span(span_hex(2), attributes={TOOL_CALL_RESULT: "secret", "gen_ai.tool.name": "x"})]
    )
).encode("utf-8")


def fail_with_payload(spans: list[Span]) -> NoReturn:
    raise ValueError(f"payload {spans[0].attributes[TOOL_CALL_RESULT]}")


def create_client(
    tmp_path: Path, store: Store, on_spans: Callable[[list[Span]], None]
) -> Iterator[TestClient]:
    app = create_app(
        create_config(tmp_path / "detecttrace.db"), store, lambda: None, on_spans=on_spans
    )
    with TestClient(app) as test_client:
        yield test_client


@pytest.fixture
def tapped() -> list[Span]:
    """The spans the recording tap was given."""
    return []


@pytest.fixture
def recording_client(tmp_path: Path, app_store: Store, tapped: list[Span]) -> Iterator[TestClient]:
    yield from create_client(tmp_path, app_store, tapped.extend)


@pytest.fixture
def failing_client(tmp_path: Path, app_store: Store) -> Iterator[TestClient]:
    yield from create_client(tmp_path, app_store, fail_with_payload)


def post(client: TestClient) -> httpx.Response:
    headers = {"Content-Type": "application/json", "Authorization": f"Bearer {INGEST_TOKEN}"}
    return client.post("/v1/traces", content=TOOL_RESULT_BODY, headers=headers)


def test_the_tap_gets_the_tool_result(recording_client: TestClient, tapped: list[Span]) -> None:
    post(recording_client)
    assert tapped[0].attributes[TOOL_CALL_RESULT] == "secret"


def test_the_store_still_gets_no_tool_result(
    recording_client: TestClient, app_store: Store
) -> None:
    post(recording_client)
    assert TOOL_CALL_RESULT not in app_store.read_inputs().spans[0].attributes


def test_a_failing_tap_still_gets_a_success_answer(failing_client: TestClient) -> None:
    assert post(failing_client).status_code == 200


def test_a_failing_tap_still_stores_the_spans(failing_client: TestClient, app_store: Store) -> None:
    post(failing_client)
    assert len(app_store.read_inputs().spans) == 1


def test_a_failing_tap_is_logged_by_its_exception_type(
    failing_client: TestClient, caplog: pytest.LogCaptureFixture
) -> None:
    with caplog.at_level(logging.ERROR, logger="detecttrace.serve.app"):
        post(failing_client)
    assert "span tap failed: ValueError" in caplog.text


def test_a_failing_taps_log_holds_no_payload(
    failing_client: TestClient, caplog: pytest.LogCaptureFixture
) -> None:
    with caplog.at_level(logging.DEBUG):
        post(failing_client)
    assert "secret" not in caplog.text
