"""The HTTP app of `detecttrace ui`: one user's browser on this machine, with no tokens.

The app listens on loopback only, so the dangers are other sites the same browser visits.
Every request must name this app in its `Host` header, which defeats DNS rebinding: a
rebound name still sends its own host. Every write must also carry `X-DetectTrace: 1` and
this app's own `Origin`; a cross-site page can send neither without a CORS preflight, and
the app answers no preflight. Errors carry `serve`'s google.rpc.Status body, and the read
routes answer exactly as `serve`'s do.

`POST /api/upload/{traces|verdicts|checklists}?name=<file name>` takes the file's raw bytes
as application/octet-stream and answers `{"stored_text", "problems"}`, the problems shaped
as the view's notes. `GET /api/ui/state` answers `{"is_configured", "can_configure",
"has_results", "span_count_text", "verdict_count_text", "trace_family_text",
"checklist_classes", "checklist_error_text"}`.

`POST /api/config/proposal` and `POST /api/config` take the user's edits as JSON,
`{"fields": {name: key}, "labels": {"label_map": {label: verdict}, "agent_label_map": {...}}}`.
The proposal answers the configuration form's content, or 409 until traces and verdicts are
both stored; saving answers `{"saved_text"}` and starts a recompute. `POST /api/data/clear`
takes `{"confirm": true}` and deletes the stored data, the configuration and the checklists.
"""

import os
import shutil
import sqlite3
import tempfile
import threading
import time
from collections.abc import Callable
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import TypeVar

import anyio
from fastapi import Depends, FastAPI, Request
from fastapi.responses import JSONResponse, Response
from pydantic import BaseModel, ConfigDict, StrictBool, ValidationError
from starlette.concurrency import run_in_threadpool
from starlette.exceptions import HTTPException
from starlette.types import ASGIApp, Receive, Scope, Send
from starlette.websockets import WebSocketClose

from detecttrace.checklist import ChecklistFileError, load_checklists
from detecttrace.dashboard_view import format_count, to_note_view
from detecttrace.files import write_text_atomically
from detecttrace.model import InputFileError, Verdict
from detecttrace.results import to_note_data
from detecttrace.runconfig import load_ui_config
from detecttrace.serve.app import (
    IDLE_STATUS,
    MAX_CONCURRENT_INGESTS,
    MAX_CONCURRENT_VERDICT_POSTS,
    ServeApp,
    create_status_response,
    create_unavailable_response,
    create_unreadable_response,
    read_health_response,
    read_limited_body,
    read_page_response,
    read_results_response,
    read_status_response,
    receive_limited_body,
    respond_with_status,
)
from detecttrace.serve.mediatype import to_media_type
from detecttrace.serve.recompute import RecomputeCoordinator, RecomputeSettings, RecomputeStatus
from detecttrace.serve.store import Store, TraceFamily
from detecttrace.serve.ui_config import (
    CHECKLISTS_FOLDER,
    CONFIG_NAME,
    UiConfigError,
    build_proposal_content,
    to_recompute_settings,
    write_ui_config,
)
from detecttrace.serve.ui_recompute import UiRecompute
from detecttrace.serve.ui_uploads import (
    CHECKLIST_SUFFIXES,
    MAX_CHECKLIST_FILE_BYTES,
    MAX_TRACE_FILE_BYTES,
    MAX_VERDICT_FILE_BYTES,
    TRACE_SUFFIXES,
    VERDICT_SUFFIXES,
    UploadRefused,
    UploadReport,
    save_checklist_file,
    store_trace_file,
    store_verdict_file,
    to_safe_upload_name,
)

WRITE_HEADER = "X-DetectTrace"
UPLOAD_MEDIA_TYPE = "application/octet-stream"
JSON_MEDIA_TYPE = "application/json"
# The form's edits are a few fields and labels; anything near this size is not one.
MAX_JSON_BYTES = 1 << 20
SAVED_TEXT = "Configuration saved. The dashboard is being computed."
_LOOPBACK_HOSTS = ("127.0.0.1", "localhost")
# The ASGI code a server turns into a 403 when a websocket is closed before it is accepted.
_POLICY_VIOLATION = 1008
_TRACE_FAMILY_TEXTS: dict[TraceFamily, str] = {
    "otlp": "OTLP traces",
    "langfuse": "Langfuse traces",
}


@dataclass(slots=True)
class UiState:
    """What the routes share: the recompute coordinator once a configuration exists.

    `recompute` and `clock` are what `configure` starts a coordinator with; a state without
    `recompute` can't be configured.
    """

    store: Store
    data_dir: Path
    coordinator: RecomputeCoordinator | None = None
    recompute: UiRecompute | None = None
    clock: Callable[[], float] = time.monotonic
    # Guards `coordinator`, which the server swaps while request threads read it.
    lock: threading.Lock = field(default_factory=threading.Lock)
    # Held through a whole save, clear or tick, so the timer never saves a snapshot of data
    # just cleared, and a save and a clear never interleave. Reentrant, because a save or a
    # clear calls configure or unconfigure while holding it.
    lifecycle_lock: threading.RLock = field(default_factory=threading.RLock)

    def configure(self, settings: RecomputeSettings) -> None:
        """Recompute with `settings` from now on, starting a run at the next tick."""
        if self.recompute is None:
            raise RuntimeError("this app state has no worker pool to recompute with")
        with self.lifecycle_lock:
            # Advanced before the coordinator starts, so it finds the input newer than any
            # snapshot and runs at once instead of after a debounce. First, so a store that
            # can't take the write leaves the running settings as they were.
            self.store.mark_changed()
            self.recompute.replace_settings(settings)
            coordinator = RecomputeCoordinator(self.recompute.submit, self.store, self.clock)
            with self.lock:
                # A run the previous coordinator started used the old settings; its outcome
                # goes with it, so the page never shows the old configuration again.
                self.coordinator = coordinator

    def unconfigure(self) -> None:
        """Drop the coordinator, so nothing recomputes until the next configure."""
        with self.lifecycle_lock, self.lock:
            self.coordinator = None

    def tick(self) -> None:
        """The timer's call: let the coordinator start or finish a run; idle when unconfigured."""
        with self.lifecycle_lock:
            coordinator = self.coordinator
            if coordinator is not None:
                coordinator.tick()

    def notify_write(self) -> None:
        """Tell the coordinator about a write; with no configuration yet, nothing recomputes."""
        with self.lock:
            coordinator = self.coordinator
        if coordinator is not None:
            coordinator.notify_write()

    def read_status(self) -> RecomputeStatus:
        with self.lock:
            coordinator = self.coordinator
        return IDLE_STATUS if coordinator is None else coordinator.status


def create_ui_app(*, port: int, state: UiState) -> FastAPI:
    """Build the app that answers on 127.0.0.1:`port`; every request passes the Host guard."""
    # No docs pages and no trailing-slash redirect, as in `serve`'s app.
    app = ServeApp(docs_url=None, redoc_url=None, openapi_url=None, redirect_slashes=False)
    app.exception_handler(HTTPException)(respond_with_status)
    app.add_middleware(_HostGuard, port=port)

    @app.get("/healthz")
    async def check_health() -> Response:
        return await read_health_response(state.store)

    @app.get("/")
    async def show_dashboard() -> Response:
        return await read_page_response(state.store, "ui")

    @app.get("/api/status")
    async def show_status() -> Response:
        return await read_status_response(state.store, state.read_status())

    @app.get("/api/results.json")
    async def show_results() -> Response:
        return await read_results_response(state.store)

    @app.get("/api/ui/state")
    async def show_state() -> Response:
        try:
            content = await run_in_threadpool(_read_state_content, state)
        except sqlite3.OperationalError:
            return create_unreadable_response()
        return JSONResponse(content)

    require_write = Depends(require_same_origin_write(port))
    # Shared by every upload route, as in `serve`: a burst of large files queues rather than
    # holding many parsed files in memory at once.
    trace_slots = anyio.Semaphore(MAX_CONCURRENT_INGESTS)
    small_file_slots = anyio.Semaphore(MAX_CONCURRENT_VERDICT_POSTS)

    @app.post("/api/upload/traces", dependencies=[require_write])
    async def upload_traces(request: Request, name: str | None = None) -> Response:
        async with trace_slots:
            return await _receive_upload(
                request,
                name,
                state,
                MAX_TRACE_FILE_BYTES,
                TRACE_SUFFIXES,
                lambda path: store_trace_file(state.store, path),
            )

    @app.post("/api/upload/verdicts", dependencies=[require_write])
    async def upload_verdicts(request: Request, name: str | None = None) -> Response:
        async with small_file_slots:
            return await _receive_upload(
                request,
                name,
                state,
                MAX_VERDICT_FILE_BYTES,
                VERDICT_SUFFIXES,
                lambda path: store_verdict_file(state.store, path),
            )

    @app.post("/api/upload/checklists", dependencies=[require_write])
    async def upload_checklists(request: Request, name: str | None = None) -> Response:
        async with small_file_slots:
            return await _receive_upload(
                request,
                name,
                state,
                MAX_CHECKLIST_FILE_BYTES,
                CHECKLIST_SUFFIXES,
                lambda path: _save_checklist(state, path),
            )

    @app.post("/api/config/proposal", dependencies=[require_write])
    async def propose_config(request: Request) -> Response:
        edits = await _read_json_body(request, _ConfigEdits, 422)
        try:
            content = await run_in_threadpool(_build_proposal, state, edits)
        except UiConfigError as error:
            return create_status_response(422, str(error))
        except sqlite3.OperationalError:
            return create_unreadable_response()
        if content is None:
            return create_status_response(409, "Upload traces and verdicts first.")
        return JSONResponse(content)

    @app.post("/api/config", dependencies=[require_write])
    async def save_config(request: Request) -> Response:
        edits = await _read_json_body(request, _ConfigEdits, 422)
        try:
            await run_in_threadpool(_save_config, state, edits)
        except (UiConfigError, InputFileError) as error:
            return create_status_response(422, str(error))
        except sqlite3.OperationalError:
            return create_unavailable_response()
        return JSONResponse({"saved_text": SAVED_TEXT})

    @app.post("/api/data/clear", dependencies=[require_write])
    async def clear_data(request: Request) -> Response:
        clear_request = await _read_json_body(request, _ClearRequest, 400)
        if not clear_request.confirm:
            raise HTTPException(400, 'send {"confirm": true} to clear the data')
        try:
            await run_in_threadpool(_clear_data, state)
        except sqlite3.OperationalError:
            return create_unavailable_response()
        return JSONResponse({})

    return app


def require_same_origin_write(port: int) -> Callable[[Request], None]:
    """Return the dependency every POST route takes: 403 unless the request is this app's own.

    It needs `X-DetectTrace: 1` and an `Origin` of this app exactly. A custom header makes a
    browser preflight any cross-site request, and the app never approves one.
    """
    origins = frozenset(f"http://{host}:{port}" for host in _LOOPBACK_HOSTS)

    def check_write(request: Request) -> None:
        if request.headers.get(WRITE_HEADER) != "1":
            raise HTTPException(403, f"missing {WRITE_HEADER} header")
        if request.headers.get("origin") not in origins:
            raise HTTPException(403, "cross-site request refused")

    return check_write


def require_content_type(request: Request, expected: str) -> None:
    """Raise 415 unless the request's media type is `expected`, in any case."""
    media_type, _ = to_media_type(request.headers.get("content-type", ""))
    if media_type != expected:
        raise HTTPException(415, f"send the request body as {expected}")


async def save_body(request: Request, target: Path, max_bytes: int) -> None:
    """Write the body to the new file `target`, refusing it with 413 past `max_bytes`.

    The limits are those of `serve`'s bodies: Content-Length is checked before reading, and
    a body not fully received within BODY_READ_SECONDS is refused with 503. A refused body
    leaves a partial `target`, which the caller's temporary folder removes.
    """
    # Exclusive create: a name that already exists is never overwritten.
    with target.open("xb") as file:
        # Plain writes on the event loop: a local disk takes each piece from the page cache
        # in microseconds, and this app has a single user.
        await receive_limited_body(
            request, max_bytes, f"the file is over {max_bytes >> 20} MiB", file.write
        )


async def _receive_upload(
    request: Request,
    name: str | None,
    state: UiState,
    max_bytes: int,
    suffixes: tuple[str, ...],
    store_file: Callable[[Path], UploadReport],
) -> Response:
    require_content_type(request, UPLOAD_MEDIA_TYPE)
    if name is None:
        raise HTTPException(400, "name the uploaded file with ?name=")
    try:
        safe_name = to_safe_upload_name(name, suffixes)
    except UploadRefused as error:
        return create_status_response(422, str(error))
    # Inside the data folder, so the file is on the same disk the store and checklists use;
    # the folder goes in every outcome, a 413 or a lost connection included.
    with tempfile.TemporaryDirectory(dir=state.data_dir) as folder:
        file_path = Path(folder) / safe_name
        await save_body(request, file_path, max_bytes)
        try:
            report = await run_in_threadpool(store_file, file_path)
        except UploadRefused as error:
            return create_status_response(422, str(error))
        except sqlite3.OperationalError:
            return create_unavailable_response()
    state.notify_write()
    problems = [asdict(to_note_view(to_note_data(line))) for line in report.problems]
    return JSONResponse({"stored_text": report.stored_text, "problems": problems})


class _ConfigEdits(BaseModel):
    model_config = ConfigDict(extra="forbid")

    fields: dict[str, str]
    labels: dict[str, dict[str, Verdict]]


class _ClearRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    # Strict, so only the JSON value true confirms: not 1 or "true", which Literal[True]
    # would take as equal to True.
    confirm: StrictBool


_Body = TypeVar("_Body", bound=BaseModel)


async def _read_json_body(request: Request, model: type[_Body], status: int) -> _Body:
    """The body as `model`, or an HTTPException with `status` saying what is wrong with it."""
    require_content_type(request, JSON_MEDIA_TYPE)
    body = await read_limited_body(request, MAX_JSON_BYTES, "send only the form's edits")
    try:
        return model.model_validate_json(body)
    except ValidationError as error:
        first = error.errors()[0]
        where = ".".join(str(part) for part in first["loc"])
        raise HTTPException(
            status, f"invalid request body: {f'{where}: ' if where else ''}{first['msg']}"
        ) from None


def _build_proposal(state: UiState, edits: _ConfigEdits) -> dict[str, object] | None:
    """The form's content, or None until traces and verdicts are both stored."""
    counts = state.store.read_counts()
    if counts.span_count == 0 or counts.verdict_count == 0:
        return None
    return build_proposal_content(state.store, state.data_dir, edits.fields, edits.labels)


def _save_config(state: UiState, edits: _ConfigEdits) -> None:
    """Write the configuration and recompute with it; a file that can't be used is undone."""
    config_path = state.data_dir / CONFIG_NAME
    with state.lifecycle_lock:
        previous = config_path.read_text(encoding="utf-8") if config_path.is_file() else None
        config = write_ui_config(state.store, state.data_dir, edits.fields, edits.labels)
        # The checklists are only read once the file names them, so a file whose checklists
        # can't be used is put back as it was: the next start would refuse to load it. A
        # store that can't record the change puts it back too, so the file always matches
        # the settings the app runs with.
        try:
            state.configure(to_recompute_settings(config, config_path))
        except BaseException:
            if previous is None:
                config_path.unlink(missing_ok=True)
            else:
                write_text_atomically(previous, config_path)
            raise


def _clear_data(state: UiState) -> None:
    with state.lifecycle_lock:
        state.unconfigure()
        state.store.clear()
        (state.data_dir / CONFIG_NAME).unlink(missing_ok=True)
        folder = state.data_dir / CHECKLISTS_FOLDER
        # A linked folder is left alone, so a clear never deletes files outside the data
        # folder; unlink removes a linked file's link, never its target.
        if folder.is_dir() and not folder.is_symlink():
            for entry in folder.iterdir():
                if entry.is_symlink() or entry.is_file():
                    entry.unlink()


def _save_checklist(state: UiState, file_path: Path) -> UploadReport:
    """Save the checklist; once configured, recompute with it, or put the folder back as it was."""
    folder = state.data_dir / CHECKLISTS_FOLDER
    config_path = state.data_dir / CONFIG_NAME
    with state.lifecycle_lock:
        if not config_path.is_file():
            # Nothing recomputes before a configuration, and configure advances the
            # generation, so the store needs no write here.
            return save_checklist_file(file_path, folder)
        with tempfile.TemporaryDirectory(dir=state.data_dir) as backup_folder:
            backup = Path(backup_folder)
            folder.mkdir(parents=True, exist_ok=True)
            _copy_folder_files(folder, backup)
            report = save_checklist_file(file_path, folder)
            # The settings hold the checklists loaded when they were built, so they are built
            # again; a file that loads alone can still break the folder, as a second file for
            # its class in a subfolder does, and the next start would refuse that folder.
            try:
                settings = to_recompute_settings(load_ui_config(config_path), config_path)
            except InputFileError as error:
                _restore_folder_files(folder, backup)
                raise UploadRefused(str(error)) from None
            # The upload answers as not stored, so the folder must not keep it either.
            try:
                state.configure(settings)
            except BaseException:
                _restore_folder_files(folder, backup)
                raise
        return report


def _copy_folder_files(folder: Path, backup: Path) -> None:
    for path in folder.iterdir():
        if path.is_file() or path.is_symlink():
            # A link is copied as a link, so restoring it never turns it into a file.
            shutil.copy2(path, backup / path.name, follow_symlinks=False)


def _restore_folder_files(folder: Path, backup: Path) -> None:
    for path in folder.iterdir():
        if (path.is_file() or path.is_symlink()) and not os.path.lexists(backup / path.name):
            path.unlink()
    for path in backup.iterdir():
        os.replace(path, folder / path.name)


def _read_state_content(state: UiState) -> dict[str, object]:
    counts = state.store.read_counts()
    trace_family = state.store.read_trace_family()
    classes, checklist_error = _read_checklist_classes(state.data_dir / CHECKLISTS_FOLDER)
    return {
        "is_configured": (state.data_dir / CONFIG_NAME).is_file(),
        "can_configure": counts.span_count > 0 and counts.verdict_count > 0,
        "has_results": state.store.read_snapshot() is not None,
        "span_count_text": _describe_stored(counts.span_count, "span", "spans"),
        "verdict_count_text": _describe_stored(counts.verdict_count, "verdict", "verdicts"),
        "trace_family_text": None if trace_family is None else _TRACE_FAMILY_TEXTS[trace_family],
        "checklist_classes": classes,
        "checklist_error_text": checklist_error,
    }


def _read_checklist_classes(folder: Path) -> tuple[list[str], str | None]:
    """The saved checklists' alert classes, sorted, or none and the loader's message."""
    if not folder.exists():
        return [], None
    try:
        checklists = load_checklists(folder)
    except ChecklistFileError as error:
        return [], str(error)
    return sorted(checklist.alert_class for checklist in checklists.values()), None


def _describe_stored(count: int, singular: str, plural: str) -> str:
    return f"{format_count(count)} {singular if count == 1 else plural} stored."


class _HostGuard:
    """Refuses any request whose one `Host` header is not this app's loopback address."""

    def __init__(self, app: ASGIApp, port: int) -> None:
        self._app = app
        self._hosts = frozenset(f"{host}:{port}".encode("ascii") for host in _LOOPBACK_HOSTS)

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] not in ("http", "websocket") or self._is_expected_host(scope):
            await self._app(scope, receive, send)
            return
        if scope["type"] == "websocket":
            await WebSocketClose(_POLICY_VIOLATION)(scope, receive, send)
            return
        response = create_status_response(403, "unexpected Host header")
        await response(scope, receive, send)

    def _is_expected_host(self, scope: Scope) -> bool:
        hosts = [value for name, value in scope["headers"] if name.lower() == b"host"]
        return len(hosts) == 1 and hosts[0].lower() in self._hosts
