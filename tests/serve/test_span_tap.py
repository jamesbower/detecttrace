"""The span tap: a wrapping app sees each span batch with its tool results, before the CE
strips and stores it. A failing tap never changes what the CE stores or answers; a tap that
refuses the batch stores none of it, and the sender is told why."""

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
from detecttrace.serve.app import SpanBatchRefused, create_app
from detecttrace.serve.store import Store
from serve.app_support import INGEST_TOKEN, create_config

TOOL_RESULT_BODY = json.dumps(
    otlp_document(
        [otlp_span(span_hex(2), attributes={TOOL_CALL_RESULT: "secret", "gen_ai.tool.name": "x"})]
    )
).encode("utf-8")


def fail_with_payload(spans: list[Span]) -> NoReturn:
    raise ValueError(f"payload {spans[0].attributes[TOOL_CALL_RESULT]}")


def refuse(spans: list[Span]) -> NoReturn:
    raise SpanBatchRefused("this batch is not accepted here")


def create_client(
    tmp_path: Path, store: Store, writes: list[int], on_spans: Callable[[list[Span]], None]
) -> Iterator[TestClient]:
    app = create_app(
        create_config(tmp_path / "detecttrace.db"),
        store,
        lambda: writes.append(1),
        on_spans=on_spans,
    )
    with TestClient(app) as test_client:
        yield test_client


@pytest.fixture
def tapped() -> list[Span]:
    """The spans the recording tap was given."""
    return []


@pytest.fixture
def recording_client(
    tmp_path: Path, app_store: Store, writes: list[int], tapped: list[Span]
) -> Iterator[TestClient]:
    yield from create_client(tmp_path, app_store, writes, tapped.extend)


@pytest.fixture
def failing_client(tmp_path: Path, app_store: Store, writes: list[int]) -> Iterator[TestClient]:
    yield from create_client(tmp_path, app_store, writes, fail_with_payload)


@pytest.fixture
def refusing_client(tmp_path: Path, app_store: Store, writes: list[int]) -> Iterator[TestClient]:
    yield from create_client(tmp_path, app_store, writes, refuse)


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


def test_a_refused_batch_is_answered_422(refusing_client: TestClient) -> None:
    assert post(refusing_client).status_code == 422


def test_a_refused_batch_is_told_the_taps_reason(refusing_client: TestClient) -> None:
    assert post(refusing_client).json()["message"] == "this batch is not accepted here"


def test_a_refused_batch_stores_no_span(refusing_client: TestClient, app_store: Store) -> None:
    post(refusing_client)
    assert app_store.read_inputs().spans == []


def test_a_refused_batch_is_a_data_note(refusing_client: TestClient, app_store: Store) -> None:
    post(refusing_client)
    assert [stored.issue.detail for stored in app_store.read_inputs().issues] == [
        "HTTP 422: this batch is not accepted here"
    ]


def test_a_refused_batch_signals_one_write(refusing_client: TestClient, writes: list[int]) -> None:
    post(refusing_client)
    assert writes == [1]
