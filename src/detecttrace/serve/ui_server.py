"""The `detecttrace ui` process: the data folder, the store, the worker pool, the recompute
timer, and uvicorn on 127.0.0.1.

The app is for the one person at this machine, so it listens on loopback only, with no
option to change that. Every problem found before it listens is a StartupError with a
one-line message. On SIGINT or SIGTERM it stops as `serve` does: uvicorn finishes the requests
in flight, then the timer stops, the store closes and the worker pool shuts down.
"""

import errno
import socket
import sqlite3
import sys
import threading
import webbrowser
from collections.abc import Callable
from pathlib import Path

import uvicorn
from starlette.types import ASGIApp

from detecttrace.model import InputFileError
from detecttrace.runconfig import load_ui_config
from detecttrace.serve.server import (
    StartupError,
    _AnnouncingServer,
    _create_executor,
    _run_until_signalled,
    _start_logging,
    open_store,
    tick_until_stopped,
)
from detecttrace.serve.ui import UiState, create_ui_app
from detecttrace.serve.ui_config import CHECKLISTS_FOLDER, CONFIG_NAME, to_recompute_settings
from detecttrace.serve.ui_recompute import UiRecompute

DEFAULT_PORT = 4321
HOST = "127.0.0.1"
DATABASE_NAME = "detecttrace.db"


def run_ui(
    data_dir: Path, port: int, *, should_open: bool, announce: Callable[[str], None]
) -> None:
    """Run the app on 127.0.0.1:`port`, keeping its data in `data_dir`, until a signal stops it.

    `announce` gets the one line that says where the app is, once it listens; then, when
    `should_open`, the browser opens it. Raises StartupError for a problem found before
    listening.
    """
    _create_data_folder(data_dir)
    recompute = UiRecompute(data_dir / DATABASE_NAME, _create_executor)
    try:
        store = open_store(data_dir / DATABASE_NAME)
        try:
            state = UiState(store=store, data_dir=data_dir, recompute=recompute)
            _load_saved_configuration(state)
            _serve(state, port, should_open, announce)
        finally:
            store.close()
    finally:
        # Only after the timer has stopped (in _serve), so no run can finish and write a
        # snapshot while the worker is being terminated.
        recompute.shutdown()


def create_ui_uvicorn_config(app: ASGIApp, port: int) -> uvicorn.Config:
    """The settings uvicorn runs the app with: `serve`'s, on 127.0.0.1 and without TLS."""
    return uvicorn.Config(
        app,
        host=HOST,
        port=port,
        access_log=False,
        # Only this machine connects, so no proxy's X-Forwarded-For is ever trusted.
        proxy_headers=False,
        # A stalled upload could otherwise hold the stop for its whole 30 s body deadline.
        timeout_graceful_shutdown=5,
        log_level="info",
    )


def _create_data_folder(data_dir: Path) -> None:
    try:
        # Owner-only, as the database file is: it holds investigation data. An existing
        # folder keeps the mode its owner gave it.
        data_dir.mkdir(mode=0o700, parents=True, exist_ok=True)
        (data_dir / CHECKLISTS_FOLDER).mkdir(mode=0o700, exist_ok=True)
    except OSError as error:
        raise StartupError(
            f"Can't create the data folder {data_dir}: {error.strerror or error}."
        ) from None


def _load_saved_configuration(state: UiState) -> None:
    config_path = state.data_dir / CONFIG_NAME
    if not config_path.exists():
        return
    try:
        settings = to_recompute_settings(load_ui_config(config_path), config_path)
    except InputFileError as error:
        raise StartupError(
            f"{error}\nThe saved configuration can't be used. Fix {config_path}, or delete it "
            "and confirm the configuration again in the app."
        ) from None
    try:
        state.configure(settings)
    except sqlite3.Error as error:
        raise StartupError(
            f"Can't read the database {state.data_dir / DATABASE_NAME}: {error}."
        ) from None


def _serve(state: UiState, port: int, should_open: bool, announce: Callable[[str], None]) -> None:
    url = f"http://{HOST}:{port}/"

    def on_started() -> None:
        announce(f"DetectTrace is running at {url} (press Ctrl+C to stop).")
        if should_open:
            webbrowser.open(url)

    with _bind(port) as listener:
        app = create_ui_app(port=port, state=state)
        server = _AnnouncingServer(create_ui_uvicorn_config(app, port), on_started)
        stop = threading.Event()
        timer = threading.Thread(
            target=tick_until_stopped, args=(state.tick, stop), name="recompute", daemon=True
        )
        _start_logging()
        timer.start()
        try:
            _run_until_signalled(server, [listener])
        except SystemExit:
            # uvicorn exits by itself, with its own code, when it can't start; it has logged
            # why already.
            raise StartupError(
                f"Could not start on {HOST}:{port}; the error above says why."
            ) from None
        finally:
            stop.set()
            timer.join()


def _bind(port: int) -> socket.socket:
    """The listening socket, bound here so a port in use is a message, not uvicorn's exit.

    uvicorn serves on this very socket, so no other program can take the port between the
    check and the start.
    """
    listener = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    # As uvicorn binds: a restart need not wait for the last run's connections to time out.
    # Not on Windows, where the option lets a second program bind a port already in use.
    if sys.platform != "win32":
        listener.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    try:
        listener.bind((HOST, port))
    except OSError as error:
        listener.close()
        if error.errno == errno.EADDRINUSE:
            raise StartupError(f"Port {port} is in use. Pass --port with a free port.") from None
        raise StartupError(f"Can't listen on {HOST}:{port}: {error.strerror or error}.") from None
    return listener
