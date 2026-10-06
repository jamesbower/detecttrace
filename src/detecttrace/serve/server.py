"""The `detecttrace serve` process: startup checks, the store, the worker pool, the recompute
timer, and uvicorn.

Every problem found before the server listens is a StartupError with a one-line message. On
SIGTERM or SIGINT uvicorn stops accepting connections and finishes the requests in flight;
then the timer stops, the worker pool shuts down and the store closes.
"""

import logging
import multiprocessing
import signal
import socket
import sqlite3
import sys
import threading
import time
from collections.abc import Callable
from concurrent.futures import Executor, Future, ProcessPoolExecutor
from concurrent.futures.process import BrokenProcessPool
from pathlib import Path
from types import FrameType

import uvicorn
from starlette.types import ASGIApp

from detecttrace.serve.app import create_app
from detecttrace.serve.config import ServeConfig
from detecttrace.serve.recompute import (
    RecomputeCoordinator,
    RecomputeOutcome,
    compute_snapshot,
    load_recompute_settings,
)
from detecttrace.serve.store import Store, StoreIntegrityError, StoreVersionError

logger = logging.getLogger(__name__)

TICK_SECONDS = 1.0
_WORKER_EXIT_SECONDS = 5
_LOG_FORMAT = "%(asctime)s %(levelname)s %(name)s: %(message)s"


class StartupError(Exception):
    """A problem that stops the server before it listens; the message is for the user."""


def run_server(config: ServeConfig, config_path: Path, announce: Callable[[str], None]) -> None:
    """Check, start and run the server until a signal stops it.

    `announce` gets the one line that says where the server listens, once it does. Raises
    StartupError for a problem found before listening, and ChecklistFileError for checklists
    that can't be used.
    """
    check_tls_files(config)
    pool = create_recompute_pool(config, config_path)
    try:
        store = open_store(config.serve.database)
        try:
            _serve(config, store, pool, announce)
        finally:
            store.close()
    finally:
        # Only after the timer has stopped (in _serve), so no run can finish and write a
        # snapshot while the worker is being terminated.
        pool.shutdown()


def create_recompute_pool(
    config: ServeConfig,
    config_path: Path,
    create_executor: Callable[[], Executor] | None = None,
) -> "WorkerPool":
    """The worker pool, with the settings every run uses read once, here.

    Raises ChecklistFileError for checklists that can't be used.
    """
    settings = load_recompute_settings(config, config_path)
    return WorkerPool(
        _create_executor if create_executor is None else create_executor,
        lambda executor: executor.submit(
            compute_snapshot, config.serve.database, settings, time.time_ns()
        ),
    )


def create_uvicorn_config(config: ServeConfig, app: ASGIApp) -> uvicorn.Config:
    """The settings uvicorn runs `app` with; the certificate is not read until `load()`."""
    tls = config.serve.tls
    return uvicorn.Config(
        app,
        host=config.serve.host,
        port=config.serve.port,
        ssl_certfile=None if tls is None else str(tls.certfile),
        ssl_keyfile=None if tls is None else str(tls.keyfile),
        access_log=False,
        # The access log's client address is the peer's; X-Forwarded-For is not trusted
        # until proxy support is added on purpose.
        proxy_headers=False,
        # A stalled upload could otherwise hold the stop for its whole 30 s body deadline.
        timeout_graceful_shutdown=5,
        log_level="info",
    )


def _serve(
    config: ServeConfig, store: Store, pool: "WorkerPool", announce: Callable[[str], None]
) -> None:
    try:
        coordinator = RecomputeCoordinator(pool.submit, store, time.monotonic)
    except sqlite3.Error as error:
        raise StartupError(f"Can't read the database {config.serve.database}: {error}.") from None
    stop = threading.Event()
    timer = threading.Thread(
        target=tick_until_stopped,
        args=(coordinator.tick, stop),
        name="recompute",
        daemon=True,
    )
    app = create_app(config, store, coordinator.notify_write, lambda: coordinator.status)
    uvicorn_config = create_uvicorn_config(config, app)
    try:
        # Loaded here rather than inside run, so a bad certificate is a message, not a
        # traceback from a half-started server.
        uvicorn_config.load()
    except OSError as error:
        raise StartupError(f"Can't use the TLS certificate and key: {error}.") from None
    server = _AnnouncingServer(uvicorn_config, lambda: announce(_to_listen_text(config)))
    _start_logging()
    timer.start()
    try:
        _run_until_signalled(server)
    except SystemExit:
        # uvicorn exits by itself, with its own code, when it can't listen; it has
        # logged why already.
        raise StartupError(
            f"Could not listen on {_to_address(config)}; the error above says why."
        ) from None
    finally:
        stop.set()
        timer.join()


def check_tls_files(config: ServeConfig) -> None:
    """Raise StartupError unless both TLS files, when set, can be opened for reading."""
    tls = config.serve.tls
    if tls is None:
        return
    for option, path in (("serve.tls.certfile", tls.certfile), ("serve.tls.keyfile", tls.keyfile)):
        try:
            with path.open("rb"):
                pass
        except OSError as error:
            raise StartupError(f"Can't read {option} {path}: {error.strerror or error}.") from None


def open_store(database: Path) -> Store:
    """Open the database, raising StartupError with the reason when it can't be used.

    The folder is never created: a typo in the path would otherwise start an empty service
    in a stray folder.
    """
    if not database.parent.is_dir():
        raise StartupError(
            f"The database folder {database.parent} does not exist. Create it, or change "
            "serve.database."
        )
    try:
        return Store.open(database)
    except (StoreVersionError, StoreIntegrityError) as error:
        raise StartupError(str(error)) from None
    except (sqlite3.Error, OSError) as error:
        raise StartupError(f"Can't open the database {database}: {error}.") from None


class WorkerPool:
    """The process pool the recompute runs in, replaced when its worker has died.

    A dead worker breaks the pool for good, so without a new one every later recompute
    would fail. The run that was going when it died fails through its own future.
    """

    def __init__(
        self,
        create_executor: Callable[[], Executor],
        start: Callable[[Executor], Future[RecomputeOutcome]],
    ) -> None:
        self._create_executor = create_executor
        self._start = start
        self._lock = threading.Lock()
        self._executor = create_executor()

    def submit(self) -> Future[RecomputeOutcome]:
        with self._lock:
            try:
                return self._start(self._executor)
            except BrokenProcessPool:
                logger.warning("The recompute worker died; starting a new one")
                self._executor.shutdown(wait=False, cancel_futures=True)
                self._executor = self._create_executor()
                return self._start(self._executor)

    def shutdown(self) -> None:
        """Cancel queued runs and end the worker at once, even mid-run.

        Waiting for a run could take longer than a container's stop grace period. Ending it is
        safe because a run only reads the database; the caller stops the timer first, so no
        half-done result is ever saved.
        """
        with self._lock:
            # A private attribute, read before shutdown clears it: the executor has no public
            # way to end a running call.
            processes = list((getattr(self._executor, "_processes", None) or {}).values())
            self._executor.shutdown(wait=False, cancel_futures=True)
            for process in processes:
                process.terminate()
            for process in processes:
                process.join(_WORKER_EXIT_SECONDS)


def _create_executor() -> Executor:
    # Spawn, not fork: a forked child would inherit the server's threads and SQLite handle.
    return ProcessPoolExecutor(max_workers=1, mp_context=multiprocessing.get_context("spawn"))


def tick_until_stopped(tick: Callable[[], None], stop: threading.Event) -> None:
    """Call `tick` every TICK_SECONDS until `stop` is set; a failing call is logged, not fatal."""
    while not stop.wait(TICK_SECONDS):
        try:
            tick()
        # The timer must outlive any one failure, or the page would never update again.
        except Exception:
            logger.exception("The recompute timer failed; it keeps running")


class _AnnouncingServer(uvicorn.Server):
    """A uvicorn server that calls `on_started` once it listens."""

    def __init__(self, config: uvicorn.Config, on_started: Callable[[], None]) -> None:
        super().__init__(config)
        self._on_started = on_started

    async def startup(self, sockets: list[socket.socket] | None = None) -> None:
        await super().startup(sockets)
        if self.started:
            self._on_started()


def _run_until_signalled(
    server: uvicorn.Server, sockets: list[socket.socket] | None = None
) -> None:
    # uvicorn handles SIGTERM and SIGINT itself, then raises the signal again so the previous
    # handler sees it. With these handlers that is a no-op, so a graceful stop exits 0.
    previous = {
        signum: signal.signal(signum, _ignore_signal) for signum in (signal.SIGINT, signal.SIGTERM)
    }
    try:
        server.run(sockets)
    finally:
        for signum, handler in previous.items():
            signal.signal(signum, handler)


def _ignore_signal(signum: int, frame: FrameType | None) -> None:
    pass


def _start_logging() -> None:
    package_logger = logging.getLogger("detecttrace")
    if package_logger.handlers:
        return
    handler = logging.StreamHandler(sys.stderr)
    handler.setFormatter(logging.Formatter(_LOG_FORMAT))
    package_logger.addHandler(handler)
    package_logger.setLevel(logging.INFO)


def _to_listen_text(config: ServeConfig) -> str:
    scheme = "http" if config.serve.tls is None else "https"
    return f"Serving on {scheme}://{_to_address(config)}"


def _to_address(config: ServeConfig) -> str:
    host = config.serve.host
    shown_host = f"[{host}]" if ":" in host else host
    return f"{shown_host}:{config.serve.port}"
