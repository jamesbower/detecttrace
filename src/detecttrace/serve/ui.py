"""The HTTP app of `detecttrace ui`: one user's browser on this machine, with no tokens.

The app listens on loopback only, so the dangers are other sites the same browser visits.
Every request must name this app in its `Host` header, which defeats DNS rebinding: a
rebound name still sends its own host. Every write must also carry `X-DetectTrace: 1` and
this app's own `Origin`; a cross-site page can send neither without a CORS preflight, and
the app answers no preflight. Errors carry `serve`'s google.rpc.Status body, and the read
routes answer exactly as `serve`'s do.
"""

import threading
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path

from fastapi import FastAPI, Request
from fastapi.responses import Response
from starlette.exceptions import HTTPException
from starlette.types import ASGIApp, Receive, Scope, Send
from starlette.websockets import WebSocketClose

from detecttrace.serve.app import (
    IDLE_STATUS,
    ServeApp,
    create_status_response,
    read_health_response,
    read_page_response,
    read_results_response,
    read_status_response,
    respond_with_status,
)
from detecttrace.serve.mediatype import to_media_type
from detecttrace.serve.recompute import RecomputeCoordinator, RecomputeStatus
from detecttrace.serve.store import Store

WRITE_HEADER = "X-DetectTrace"
_LOOPBACK_HOSTS = ("127.0.0.1", "localhost")
# The ASGI code a server turns into a 403 when a websocket is closed before it is accepted.
_POLICY_VIOLATION = 1008


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
