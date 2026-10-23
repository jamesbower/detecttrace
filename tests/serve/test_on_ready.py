"""The on_ready seam: a wrapping app starts with the server and stops before the store closes."""

import logging
from collections.abc import Callable
from pathlib import Path

import pytest

from detecttrace.serve import server
from detecttrace.serve.config import load_serve_config
from detecttrace.serve.server import StartupError, run_server
from detecttrace.serve.store import Store
from serve.served_process import find_free_port, write_serve_config

OnReady = Callable[[Callable[[], None]], Callable[[], None]]


@pytest.fixture
def config_path(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    # The server returns as soon as it would listen, so each test sees one whole start and stop.
    monkeypatch.setattr(server, "_run_until_signalled", lambda *args: None)
    return write_serve_config(tmp_path, find_free_port())


def serve_with(config_path: Path, on_ready: OnReady) -> None:
    run_server(load_serve_config(config_path), config_path, lambda line: None, on_ready=on_ready)


def test_on_ready_is_called_once(config_path: Path) -> None:
    calls: list[int] = []

    def on_ready(request_recompute: Callable[[], None]) -> Callable[[], None]:
        calls.append(1)
        return lambda: None

    serve_with(config_path, on_ready)
    assert calls == [1]


def test_the_stop_function_runs_when_the_server_stops(config_path: Path) -> None:
    stops: list[int] = []
    serve_with(config_path, lambda request_recompute: lambda: stops.append(1))
    assert stops == [1]


def test_request_recompute_advances_the_generation(config_path: Path, tmp_path: Path) -> None:
    def on_ready(request_recompute: Callable[[], None]) -> Callable[[], None]:
        request_recompute()
        return lambda: None

    serve_with(config_path, on_ready)
    store = Store.open_read_only(tmp_path / "detecttrace.db")
    try:
        generation = store.generation()
    finally:
        store.close()
    assert generation == 1


def test_a_failing_on_ready_stops_the_start(config_path: Path) -> None:
    def on_ready(request_recompute: Callable[[], None]) -> Callable[[], None]:
        raise RuntimeError("the dependency is not reachable")

    with pytest.raises(StartupError, match="the dependency is not reachable"):
        serve_with(config_path, on_ready)


def test_a_failing_stop_is_logged_by_its_type(
    config_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    def fail() -> None:
        raise RuntimeError("case DT-1 detail")

    with caplog.at_level(logging.ERROR):
        serve_with(config_path, lambda request_recompute: fail)
    assert "RuntimeError" in caplog.text


def test_a_failing_stop_does_not_log_its_message(
    config_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    def fail() -> None:
        raise RuntimeError("case DT-1 detail")

    with caplog.at_level(logging.ERROR):
        serve_with(config_path, lambda request_recompute: fail)
    assert "DT-1" not in caplog.text
