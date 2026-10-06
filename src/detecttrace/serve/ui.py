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
"""

import sqlite3
import tempfile
import threading
from collections.abc import Callable
from dataclasses import asdict, dataclass, field
from pathlib import Path

import anyio
from fastapi import Depends, FastAPI, Request
from fastapi.responses import JSONResponse, Response
from starlette.concurrency import run_in_threadpool
from starlette.exceptions import HTTPException
from starlette.types import ASGIApp, Receive, Scope, Send
from starlette.websockets import WebSocketClose

from detecttrace.checklist import ChecklistFileError, load_checklists
from detecttrace.dashboard_view import format_count, to_note_view
from detecttrace.results import to_note_data
from detecttrace.serve.app import (
    IDLE_STATUS,
    MAX_CONCURRENT_INGESTS,
    MAX_CONCURRENT_VERDICT_POSTS,
    ServeApp,
    create_status_response,
    create_unavailable_response,
    create_unreadable_response,
    read_health_response,
    read_page_response,
    read_results_response,
    read_status_response,
    receive_limited_body,
    respond_with_status,
)
from detecttrace.serve.mediatype import to_media_type
from detecttrace.serve.recompute import RecomputeCoordinator, RecomputeStatus
from detecttrace.serve.store import Store, TraceFamily
from detecttrace.serve.ui_config import CHECKLISTS_FOLDER, CONFIG_NAME
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
_LOOPBACK_HOSTS = ("127.0.0.1", "localhost")
# The ASGI code a server turns into a 403 when a websocket is closed before it is accepted.
_POLICY_VIOLATION = 1008
_TRACE_FAMILY_TEXTS: dict[TraceFamily, str] = {
    "otlp": "OTLP traces",
    "langfuse": "Langfuse traces",
}


@dataclass(slots=True)
class UiState:
    """What the routes share: the recompute coordinator once a configuration exists."""

    store: Store
    data_dir: Path
    coordinator: RecomputeCoordinator | None = None
    # Guards `coordinator`, which the server swaps while request threads read it.
    lock: threading.Lock = field(default_factory=threading.Lock)

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


def _save_checklist(state: UiState, file_path: Path) -> UploadReport:
    report = save_checklist_file(file_path, state.data_dir / CHECKLISTS_FOLDER)
    # Checklists live outside the store, so the generation is advanced for the recompute to
    # see the change.
    state.store.mark_changed()
    return report


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
