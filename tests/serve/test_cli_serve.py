"""`detecttrace serve`: startup failures, the worker pool, and a real process end to end."""

import json
import multiprocessing
import os
import shutil
import socket
import sqlite3
import subprocess
import sys
import threading
import time
from collections.abc import Callable, Iterator
from concurrent.futures import Executor, Future, ProcessPoolExecutor
from concurrent.futures.process import BrokenProcessPool
from dataclasses import dataclass
from pathlib import Path
from typing import Any, NoReturn

import pytest
import uvicorn
from page_data import read_results, read_view
from typer.testing import CliRunner, Result

import detecttrace.serve
from detecttrace import cli
from detecttrace.results import SCHEMA_VERSION
from detecttrace.serve import server
from detecttrace.serve.config import ServeConfig, load_serve_config
from detecttrace.serve.recompute import RecomputeOutcome
from detecttrace.serve.server import (
    WorkerPool,
    create_recompute_pool,
    create_uvicorn_config,
    tick_until_stopped,
)
from detecttrace.serve.store import Snapshot, Store
from serve.served_process import (
    DEMO_FOLDER,
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


def create_empty_checklist_folder(folder: Path) -> Path:
    config_path = write_serve_config(folder, find_free_port())
    config = json.loads(config_path.read_text(encoding="utf-8"))
    (folder / "checklists").mkdir()
    config["checklists"] = "checklists"
    config_path.write_text(json.dumps(config), encoding="utf-8")
    return config_path


def create_missing_checklist_folder(folder: Path) -> Path:
    config_path = write_serve_config(folder, find_free_port())
    config = json.loads(config_path.read_text(encoding="utf-8"))
    config["checklists"] = "no-such-folder"
    config_path.write_text(json.dumps(config), encoding="utf-8")
    return config_path


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
    pytest.param(
        Failure(create_empty_checklist_folder, "No checklist files"), id="empty-checklist-folder"
    ),
    pytest.param(
        Failure(create_missing_checklist_folder, "Check checklists in detecttrace-serve.yaml."),
        id="missing-checklist-folder",
    ),
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
def start_with_unrelated_import_error(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Result:
    monkeypatch.setitem(sys.modules, "detecttrace.serve.server", None)
    monkeypatch.delattr(detecttrace.serve, "server")
    config_path = write_serve_config(tmp_path, find_free_port())
    return CliRunner().invoke(cli.app, ["serve", "--config", str(config_path)])


def test_an_unrelated_missing_module_is_an_internal_error(
    start_with_unrelated_import_error: Result,
) -> None:
    assert start_with_unrelated_import_error.exit_code == 2


def fail_with_locked_database(*args: object, **kwargs: object) -> NoReturn:
    raise sqlite3.OperationalError("database is locked")


@pytest.fixture
def start_with_unreadable_database(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Result:
    monkeypatch.setattr(Store, "read_snapshot", fail_with_locked_database)
    config_path = write_serve_config(tmp_path, find_free_port())
    return CliRunner().invoke(cli.app, ["serve", "--config", str(config_path)])


def test_an_unreadable_database_at_startup_exits_1(start_with_unreadable_database: Result) -> None:
    assert start_with_unreadable_database.exit_code == 1


def test_an_unreadable_database_at_startup_names_the_database(
    start_with_unreadable_database: Result,
) -> None:
    assert "detecttrace.db: database is locked." in start_with_unreadable_database.stderr


def fail_unexpectedly(*args: object, **kwargs: object) -> NoReturn:
    raise RuntimeError("the timer thread could not start")


@pytest.fixture
def crash_while_serving(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Result:
    monkeypatch.setattr(server, "run_server", fail_unexpectedly)
    config_path = write_serve_config(tmp_path, find_free_port())
    return CliRunner().invoke(cli.app, ["serve", "--config", str(config_path)])


def test_an_unexpected_serve_error_shows_its_message(crash_while_serving: Result) -> None:
    assert "RuntimeError: the timer thread could not start" in crash_while_serving.stderr


def test_an_unexpected_serve_error_shows_the_traceback(crash_while_serving: Result) -> None:
    assert "Traceback (most recent call last)" in crash_while_serving.stderr


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


def load_config_with(folder: Path, **serve: object) -> ServeConfig:
    config_path = write_serve_config(folder, 8443)
    config = json.loads(config_path.read_text(encoding="utf-8"))
    config["serve"].update(serve)
    config_path.write_text(json.dumps(config), encoding="utf-8")
    return load_serve_config(config_path)


def test_the_start_line_names_https_when_tls_is_set(tmp_path: Path) -> None:
    config = load_config_with(tmp_path, tls={"certfile": "cert.pem", "keyfile": "key.pem"})
    assert server._to_listen_text(config) == "Serving on https://127.0.0.1:8443"  # pyright: ignore[reportPrivateUsage]


def test_the_start_line_brackets_an_ipv6_host(tmp_path: Path) -> None:
    config = load_config_with(tmp_path, host="::1")
    assert server._to_listen_text(config) == "Serving on http://[::1]:8443"  # pyright: ignore[reportPrivateUsage]


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


def test_a_second_broken_pool_is_reported_to_the_coordinator() -> None:
    executors: Iterator[Executor] = iter([BrokenExecutor(), BrokenExecutor()])
    pool = WorkerPool(lambda: next(executors), start_run)
    with pytest.raises(BrokenProcessPool):
        pool.submit()


@pytest.fixture
def pool_after_checklists_removed(tmp_path: Path) -> WorkerPool:
    """A pool created with a checklist folder that was deleted before its run."""
    Store.open(tmp_path / "detecttrace.db").close()
    shutil.copytree(DEMO_FOLDER / "checklists", tmp_path / "checklists")
    config_path = write_serve_config(tmp_path, find_free_port())
    config = json.loads(config_path.read_text(encoding="utf-8"))
    config["checklists"] = "checklists"
    config_path.write_text(json.dumps(config), encoding="utf-8")
    pool = create_recompute_pool(load_serve_config(config_path), config_path, InlineExecutor)
    shutil.rmtree(tmp_path / "checklists")
    return pool


def test_a_run_uses_the_checklists_read_when_the_pool_was_created(
    pool_after_checklists_removed: WorkerPool,
) -> None:
    outcome = pool_after_checklists_removed.submit().result()
    assert outcome.snapshot.generation == 0


async def ignore_requests(scope: Any, receive: Any, send: Any) -> None:
    """An ASGI app that is never called; the config tests only read the server's settings."""


@pytest.fixture
def uvicorn_config(tmp_path: Path) -> uvicorn.Config:
    config_path = write_serve_config(tmp_path, find_free_port())
    return create_uvicorn_config(load_serve_config(config_path), ignore_requests)


def test_the_server_gives_requests_5_seconds_to_finish_on_stop(
    uvicorn_config: uvicorn.Config,
) -> None:
    assert uvicorn_config.timeout_graceful_shutdown == 5


def test_the_server_does_not_trust_forwarded_headers(uvicorn_config: uvicorn.Config) -> None:
    assert uvicorn_config.proxy_headers is False


def sleep_in_worker(marker: Path) -> RecomputeOutcome:
    """A run that outlasts any test: it notes its process ID, then sleeps."""
    marker.write_text(str(os.getpid()), encoding="utf-8")
    time.sleep(30)
    raise AssertionError("the worker was not ended")


@dataclass(frozen=True)
class StoppedPool:
    shutdown_seconds: float
    worker_pid: int
    queued: Future[RecomputeOutcome]


@pytest.fixture
def stopped_mid_run(tmp_path: Path) -> StoppedPool:
    marker = tmp_path / "worker.pid"
    context = multiprocessing.get_context("spawn")
    pool = WorkerPool(
        lambda: ProcessPoolExecutor(max_workers=1, mp_context=context),
        lambda executor: executor.submit(sleep_in_worker, marker),
    )
    # The first runs; the next two fill the executor's call queue; the last waits its turn.
    futures = [pool.submit() for _ in range(4)]
    deadline = time.monotonic() + 30
    while not marker.exists() and time.monotonic() < deadline:
        time.sleep(0.05)
    started = time.monotonic()
    pool.shutdown()
    return StoppedPool(
        time.monotonic() - started, int(marker.read_text(encoding="utf-8")), futures[-1]
    )


@needs_posix
def test_shutdown_ends_a_running_worker_quickly(stopped_mid_run: StoppedPool) -> None:
    assert stopped_mid_run.shutdown_seconds < 2


@needs_posix
def test_shutdown_leaves_no_worker_process(stopped_mid_run: StoppedPool) -> None:
    with pytest.raises(ProcessLookupError):
        os.kill(stopped_mid_run.worker_pid, 0)


@needs_posix
def test_shutdown_cancels_a_queued_run(stopped_mid_run: StoppedPool) -> None:
    assert stopped_mid_run.queued.cancelled()


@pytest.fixture
def ticks_after_a_failure(monkeypatch: pytest.MonkeyPatch) -> threading.Event:
    monkeypatch.setattr(server, "TICK_SECONDS", 0.01)
    calls: list[int] = []
    second_tick = threading.Event()

    def tick() -> None:
        calls.append(1)
        if len(calls) == 1:
            raise RuntimeError("the first tick fails")
        second_tick.set()

    stop = threading.Event()
    timer = threading.Thread(target=tick_until_stopped, args=(tick, stop), daemon=True)
    timer.start()
    second_tick.wait(5)
    stop.set()
    timer.join(5)
    return second_tick


def test_the_timer_keeps_ticking_after_a_failed_tick(
    ticks_after_a_failure: threading.Event,
) -> None:
    assert ticks_after_a_failure.is_set()


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
    assert read_results(end_to_end.page)["schema_version"] == SCHEMA_VERSION


@needs_posix
def test_served_page_polls_for_newer_results(end_to_end: EndToEnd) -> None:
    # The page's script asks the server for newer results only when its view is served.
    assert read_view(end_to_end.page)["served"] is not None


@needs_posix
def test_sigterm_stops_the_server_with_exit_code_0(end_to_end: EndToEnd) -> None:
    assert end_to_end.exit_code == 0


@needs_posix
def test_the_server_says_where_it_listens(end_to_end: EndToEnd) -> None:
    assert end_to_end.stdout.startswith("Serving on http://127.0.0.1:")
