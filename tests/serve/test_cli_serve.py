"""`detecttrace serve`: startup failures, the worker pool, and a real process end to end."""

import json
import os
import socket
import sqlite3
import subprocess
import sys
from collections.abc import Callable, Iterator
from concurrent.futures import Executor, Future
from concurrent.futures.process import BrokenProcessPool
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import pytest
from typer.testing import CliRunner, Result

import detecttrace.serve
from detecttrace import cli
from detecttrace.serve.recompute import RecomputeOutcome
from detecttrace.serve.server import WorkerPool
from detecttrace.serve.store import Snapshot, Store
from serve.served_process import (
    ServedProcess,
    find_free_port,
    post_demo_data,
    start_server,
    write_serve_config,
)

needs_posix = pytest.mark.skipif(
    sys.platform == "win32", reason="sends SIGTERM, which Windows can't deliver to a process"
)
needs_permissions = pytest.mark.skipif(
    sys.platform == "win32" or os.geteuid() == 0, reason="needs POSIX permissions as non-root"
)


def write_tls_config(folder: Path) -> Path:
    config_path = write_serve_config(folder, find_free_port())
    config = json.loads(config_path.read_text(encoding="utf-8"))
    config["serve"]["tls"] = {"certfile": "cert.pem", "keyfile": "key.pem"}
    config_path.write_text(json.dumps(config), encoding="utf-8")
    return config_path


def create_missing_certfile(folder: Path) -> Path:
    (folder / "key.pem").write_text("key", encoding="utf-8")
    return write_tls_config(folder)


def create_unreadable_keyfile(folder: Path) -> Path:
    (folder / "cert.pem").write_text("cert", encoding="utf-8")
    keyfile = folder / "key.pem"
    keyfile.write_text("key", encoding="utf-8")
    keyfile.chmod(0)
    return write_tls_config(folder)


def create_invalid_certificate(folder: Path) -> Path:
    (folder / "cert.pem").write_text("not a certificate", encoding="utf-8")
    (folder / "key.pem").write_text("not a key", encoding="utf-8")
    return write_tls_config(folder)


def create_missing_database_folder(folder: Path) -> Path:
    config_path = write_serve_config(folder, find_free_port())
    config = json.loads(config_path.read_text(encoding="utf-8"))
    config["serve"]["database"] = "missing/detecttrace.db"
    config_path.write_text(json.dumps(config), encoding="utf-8")
    return config_path


def create_newer_database(folder: Path) -> Path:
    Store.open(folder / "detecttrace.db").close()
    with sqlite3.connect(folder / "detecttrace.db") as connection:
        connection.execute("UPDATE meta SET value = 99 WHERE key = 'schema_version'")
    connection.close()
    return write_serve_config(folder, find_free_port())


def create_corrupt_database(folder: Path) -> Path:
    (folder / "detecttrace.db").write_bytes(b"this is not a database file" * 100)
    return write_serve_config(folder, find_free_port())


def create_invalid_config(folder: Path) -> Path:
    path = folder / "detecttrace-serve.yaml"
    path.write_text("serve: {}\n", encoding="utf-8")
    return path


@dataclass(frozen=True)
class Failure:
    create: Callable[[Path], Path]
    expected_text: str


FAILURES = [
    pytest.param(Failure(create_missing_certfile, "serve.tls.certfile"), id="missing-certfile"),
    pytest.param(
        Failure(create_unreadable_keyfile, "serve.tls.keyfile"),
        id="unreadable-keyfile",
        marks=needs_permissions,
    ),
    pytest.param(Failure(create_invalid_certificate, "TLS certificate"), id="invalid-certificate"),
    pytest.param(
        Failure(create_missing_database_folder, "does not exist"), id="missing-database-folder"
    ),
    pytest.param(Failure(create_newer_database, "newer detecttrace"), id="newer-database"),
    pytest.param(Failure(create_corrupt_database, "damaged"), id="corrupt-database"),
    pytest.param(Failure(create_invalid_config, "invalid configuration"), id="invalid-config"),
]


@pytest.fixture(params=FAILURES)
def failed_start(request: pytest.FixtureRequest, tmp_path: Path) -> tuple[Result, str]:
    failure: Failure = request.param
    config_path = failure.create(tmp_path)
    result = CliRunner().invoke(cli.app, ["serve", "--config", str(config_path)])
    return result, failure.expected_text


def test_a_failed_start_exits_1(failed_start: tuple[Result, str]) -> None:
    assert failed_start[0].exit_code == 1


def test_a_failed_start_names_the_problem(failed_start: tuple[Result, str]) -> None:
    result, expected_text = failed_start
    assert expected_text in result.stderr


def test_a_failed_start_shows_no_traceback(failed_start: tuple[Result, str]) -> None:
    assert "Traceback" not in failed_start[0].stderr


def test_a_failed_start_prints_nothing_to_stdout(failed_start: tuple[Result, str]) -> None:
    assert failed_start[0].stdout == ""


@pytest.fixture
def start_without_extra(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Result:
    # A None entry makes `import fastapi` fail as if the package were not installed; the
    # server and app modules are dropped so the CLI imports them afresh.
    monkeypatch.setitem(sys.modules, "fastapi", None)
    monkeypatch.delitem(sys.modules, "detecttrace.serve.server")
    monkeypatch.delitem(sys.modules, "detecttrace.serve.app")
    monkeypatch.delattr(detecttrace.serve, "server")
    monkeypatch.delattr(detecttrace.serve, "app")
    config_path = write_serve_config(tmp_path, find_free_port())
    return CliRunner().invoke(cli.app, ["serve", "--config", str(config_path)])


def test_a_missing_serve_extra_exits_1(start_without_extra: Result) -> None:
    assert start_without_extra.exit_code == 1


def test_a_missing_serve_extra_says_how_to_install_it(start_without_extra: Result) -> None:
    assert start_without_extra.stderr == (
        'Error: detecttrace serve needs the serve extra: pip install "detecttrace[serve]"\n'
    )


@pytest.fixture
def start_on_busy_port(tmp_path: Path) -> subprocess.CompletedProcess[str]:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as taken:
        taken.bind(("127.0.0.1", 0))
        taken.listen()
        config_path = write_serve_config(tmp_path, taken.getsockname()[1])
        return subprocess.run(
            [sys.executable, "-m", "detecttrace", "serve", "--config", str(config_path)],
            capture_output=True,
            text=True,
            encoding="utf-8",
            timeout=60,
            check=False,
        )


def test_a_busy_port_exits_1(start_on_busy_port: subprocess.CompletedProcess[str]) -> None:
    assert start_on_busy_port.returncode == 1


def test_a_busy_port_says_the_server_could_not_listen(
    start_on_busy_port: subprocess.CompletedProcess[str],
) -> None:
    assert "Error: Could not listen on 127.0.0.1:" in start_on_busy_port.stderr


class BrokenExecutor(Executor):
    """An executor whose worker has died: every submit raises."""

    def submit(self, fn: Callable[..., Any], /, *args: Any, **kwargs: Any) -> Future[Any]:
        raise BrokenProcessPool("a worker died")


class InlineExecutor(Executor):
    """An executor that runs each call at once, in the caller's thread."""

    def submit(self, fn: Callable[..., Any], /, *args: Any, **kwargs: Any) -> Future[Any]:
        future: Future[Any] = Future()
        future.set_result(fn(*args, **kwargs))
        return future


OUTCOME = RecomputeOutcome(Snapshot(1, 2, "<p>page</p>", "{}"), 0, None)


def compute_outcome() -> RecomputeOutcome:
    return OUTCOME


def start_run(executor: Executor) -> Future[RecomputeOutcome]:
    return executor.submit(compute_outcome)


def test_a_broken_pool_is_replaced_and_the_run_submitted_again() -> None:
    executors: Iterator[Executor] = iter([BrokenExecutor(), InlineExecutor()])
    pool = WorkerPool(lambda: next(executors), start_run)
    assert pool.submit().result() == OUTCOME


@dataclass(frozen=True)
class EndToEnd:
    status: dict[str, object]
    page: str
    exit_code: int
    stdout: str


@pytest.fixture(scope="module")
def end_to_end(tmp_path_factory: pytest.TempPathFactory) -> EndToEnd:
    served: ServedProcess = start_server(tmp_path_factory.mktemp("served"))
    try:
        post_demo_data(served)
        status = served.wait_for_generation(1, timeout=60)
        page = served.get("/").text
    finally:
        exit_code = served.stop()
    return EndToEnd(status, page, exit_code, served.stdout_path.read_text(encoding="utf-8"))


@needs_posix
def test_served_status_counts_every_posted_verdict(end_to_end: EndToEnd) -> None:
    assert end_to_end.status["verdict_count"] == 201


@needs_posix
def test_served_page_is_the_dashboard(end_to_end: EndToEnd) -> None:
    assert 'id="case-filters"' in end_to_end.page


@needs_posix
def test_served_page_polls_for_newer_results(end_to_end: EndToEnd) -> None:
    assert 'id="dt-serve"' in end_to_end.page


@needs_posix
def test_sigterm_stops_the_server_with_exit_code_0(end_to_end: EndToEnd) -> None:
    assert end_to_end.exit_code == 0


@needs_posix
def test_the_server_says_where_it_listens(end_to_end: EndToEnd) -> None:
    assert end_to_end.stdout.startswith("Serving on http://127.0.0.1:")
