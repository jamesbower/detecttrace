"""The HTTP app of `detecttrace serve`: token checks, the health check, OTLP/HTTP ingest,
the verdict API, and the read routes that serve the dashboard and its status.

Error responses carry a google.rpc.Status body (`code`, `message`), the shape OTLP/HTTP
clients expect. No response or error ever repeats the token a client sent, and the access log
records only the method, path, status, client address and duration of each request.

`GET /api/status` answers `{"generation", "updated_at", "recompute_running", "last_error",
"last_error_at", "held_back_cases", "span_count", "verdict_count", "last_ingest_at"}`. The
generation and time are the stored snapshot's, so a page can tell whether reloading would show
anything new; the counts are the stored input's. Times are ISO 8601 in UTC, or null.
"""

import base64
import logging
import sqlite3
import time
from collections.abc import Callable, Mapping
from typing import Annotated, Literal

import anyio
from fastapi import Depends, FastAPI, Request
from fastapi.responses import HTMLResponse, JSONResponse, PlainTextResponse, Response
from starlette.concurrency import run_in_threadpool
from starlette.exceptions import HTTPException
from starlette.requests import ClientDisconnect
from starlette.types import ASGIApp, Message, Receive, Scope, Send

from detecttrace.config import Config
from detecttrace.dashboard import render_waiting_page
from detecttrace.model import Issue, IssueKind
from detecttrace.serve import receiver, verdict_api
from detecttrace.serve.auth import Role, find_token_name
from detecttrace.serve.config import ServeConfig, TokenRoles
from detecttrace.serve.recompute import RecomputeStatus, to_iso_time
from detecttrace.serve.store import INGEST_SUBJECT, Store
from detecttrace.served_page import ServedPage, WaitingCounts
from detecttrace.summary import to_terminal_text

access_logger = logging.getLogger("detecttrace.serve.access")

# google.rpc.Code values for the HTTP statuses this app answers with.
_STATUS_CODES = {
    400: 3,
    401: 16,
    403: 7,
    404: 5,
    405: 12,
    409: 9,
    413: 3,
    415: 3,
    422: 3,
    503: 14,
}
_UNKNOWN_CODE = 2
_RETRY_AFTER_SECONDS = "5"
_ROLES: tuple[Role, ...] = ("ingest", "verdicts", "read")
# A span batch holds up to 16 MiB of body and 32 MiB decompressed plus its spans, so a burst
# of parallel batches is queued rather than allowed to exhaust a small container's memory.
MAX_CONCURRENT_INGESTS = 4
# Verdict posts come from human tooling and get their own, smaller limit, so slow uploads
# there can never take the slots span ingest needs.
MAX_CONCURRENT_VERDICT_POSTS = 2
# A client that stops sending mid-body would otherwise hold its slot forever.
BODY_READ_SECONDS = 30
SECURITY_HEADERS = {
    "X-Content-Type-Options": "nosniff",
    "Referrer-Policy": "no-referrer",
    # Pages and results hold investigation data, so no browser or proxy keeps a copy.
    "Cache-Control": "no-store",
}
# Only what a meta policy can't say: the page's own policy, with its script hashes, stays in
# the page, and a browser enforces both policies independently.
PAGE_CSP = "frame-ancestors 'none'"
_SECURITY_HEADER_ITEMS = {
    name.lower().encode("latin-1"): value.encode("latin-1")
    for name, value in SECURITY_HEADERS.items()
}
IDLE_STATUS = RecomputeStatus(
    is_running=False, last_error=None, last_error_at_ns=None, held_back_cases=0
)


class _FailedAfterCommit(Exception):
    """A store step failed after an earlier step of the same request had committed."""


def create_app(
    config: ServeConfig,
    store: Store,
    on_write: Callable[[], None],
    read_status: Callable[[], RecomputeStatus] = lambda: IDLE_STATUS,
) -> FastAPI:
    """Build the app; `on_write` is called after every commit, a rejected body's issue too.

    `read_status` gives the recompute's current state for `/api/status`.
    """
    # No schema or docs pages: the API surface is not advertised to whoever can reach it. No
    # trailing-slash redirect either: it is sent before any token check, and behind a TLS
    # proxy its absolute URL would send the client to plain http.
    app = ServeApp(docs_url=None, redoc_url=None, openapi_url=None, redirect_slashes=False)
    app.exception_handler(HTTPException)(respond_with_status)

    require_ingest = require_role(config.tokens, "ingest", allow_basic=False)
    require_verdicts = require_role(config.tokens, "verdicts", allow_basic=False)
    require_read = require_role(config.tokens, "read", allow_basic=True)
    ingest_slots = anyio.Semaphore(MAX_CONCURRENT_INGESTS)
    verdict_slots = anyio.Semaphore(MAX_CONCURRENT_VERDICT_POSTS)

    @app.get("/healthz")
    async def check_health() -> Response:
        return await read_health_response(store)

    @app.post("/v1/traces", dependencies=[Depends(require_ingest)])
    async def receive_traces(request: Request) -> Response:
        async with ingest_slots:
            return await _receive_traces(request, store, on_write)

    @app.post("/api/verdicts")
    async def receive_verdicts(
        request: Request, token_name: Annotated[str, Depends(require_verdicts)]
    ) -> Response:
        async with verdict_slots:
            return await _receive_verdicts(request, store, config, token_name, on_write)

    @app.get("/", dependencies=[Depends(require_read)])
    async def show_dashboard() -> Response:
        return await read_page_response(store, "served")

    @app.get("/api/status", dependencies=[Depends(require_read)])
    async def show_status() -> Response:
        return await read_status_response(store, read_status())

    @app.get("/api/results.json", dependencies=[Depends(require_read)])
    async def show_results() -> Response:
        return await read_results_response(store)

    return app


async def respond_with_status(request: Request, error: HTTPException) -> Response:
    """The exception handler: any HTTPException as a google.rpc.Status body."""
    response = create_status_response(error.status_code, str(error.detail), error.headers)
    if isinstance(error, _Unauthorized):
        # One header per scheme: Chromium reads only the first challenge of a header, so
        # "Bearer, Basic ..." in one header would never show the browser's sign-in prompt.
        for challenge in error.challenges:
            response.headers.append("WWW-Authenticate", challenge)
    return response


async def read_health_response(store: Store) -> Response:
    """`GET /healthz`: "ok", or 503 while the database can't take writes."""
    try:
        await run_in_threadpool(store.check_writable)
    except sqlite3.OperationalError:
        return PlainTextResponse("unavailable", status_code=503)
    return PlainTextResponse("ok")


async def read_page_response(store: Store, mode: Literal["served", "ui"]) -> Response:
    """`GET /`: the stored snapshot's page, or `mode`'s waiting page before the first one."""
    try:
        html = await run_in_threadpool(_read_page, store, mode)
    except sqlite3.OperationalError:
        return create_unreadable_response()
    return HTMLResponse(html, headers={"Content-Security-Policy": PAGE_CSP})


async def read_status_response(store: Store, status: RecomputeStatus) -> Response:
    """`GET /api/status`: the body the module docstring describes."""
    try:
        content = await run_in_threadpool(read_status_content, store, status)
    except sqlite3.OperationalError:
        return create_unreadable_response()
    return JSONResponse(content)


async def read_results_response(store: Store) -> Response:
    """`GET /api/results.json`: the stored snapshot's results, or 503 before the first one."""
    try:
        snapshot = await run_in_threadpool(store.read_snapshot)
    except sqlite3.OperationalError:
        return create_unreadable_response()
    if snapshot is None:
        # 503, not 404: the results will exist once the first recompute finishes.
        return create_status_response(
            503,
            "no results yet; the first recompute has not finished, retry later",
            {"Retry-After": _RETRY_AFTER_SECONDS},
        )
    return Response(snapshot.results_json, media_type="application/json")


def require_role(tokens: TokenRoles, role: Role, *, allow_basic: bool) -> Callable[[Request], str]:
    """Return a dependency that admits only `role`'s tokens and gives the token's name.

    The token comes as `Authorization: Bearer <token>`: the scheme in any case, any spaces
    before the token, and nothing after it. With `allow_basic`, Basic credentials whose
    password is the token are also accepted, so a browser can sign in; the user is ignored.
    A missing or unknown token is 401; a known token of another role only is 403.
    """
    challenges = ("Bearer", 'Basic realm="detecttrace"') if allow_basic else ("Bearer",)

    def check_token(request: Request) -> str:
        token = _read_token(request.headers.get("authorization"), allow_basic)
        if token is None:
            raise _Unauthorized(challenges)
        name = find_token_name(token, getattr(tokens, role))
        if name is not None:
            return name
        if any(find_token_name(token, getattr(tokens, other)) for other in _ROLES):
            raise HTTPException(403, f"this token may not be used for {role}")
        raise _Unauthorized(challenges)

    return check_token


def _read_token(authorization: str | None, allow_basic: bool) -> str | None:
    if authorization is None:
        return None
    parts = authorization.split()
    if len(parts) != 2:
        return None
    scheme, credentials = parts
    if scheme.lower() == "bearer":
        return credentials
    if scheme.lower() == "basic" and allow_basic:
        try:
            decoded = base64.b64decode(credentials, validate=True).decode("utf-8")
        # ValueError covers bad base64, non-ASCII input, and bytes that are not UTF-8.
        except ValueError:
            return None
        _, colon, password = decoded.partition(":")
        return password if colon else None
    return None


class _Unauthorized(HTTPException):
    """A 401 that asks for a token with one WWW-Authenticate header per scheme."""

    def __init__(self, challenges: tuple[str, ...]) -> None:
        super().__init__(401, "a valid access token is required")
        self.challenges = challenges


async def _receive_traces(request: Request, store: Store, on_write: Callable[[], None]) -> Response:
    try:
        body = await read_limited_body(
            request,
            receiver.MAX_BODY_BYTES,
            "lower send_batch_max_size in the Collector's batch processor",
        )
    except _BodyTooLarge as error:
        try:
            await run_in_threadpool(store.add_issues, [_to_refusal_issue(413, error.detail)])
        except sqlite3.OperationalError:
            return create_unavailable_response()
        on_write()
        raise
    try:
        rejected, has_skipped_parts = await run_in_threadpool(
            _store_traces,
            store,
            body,
            request.headers.get("content-type"),
            request.headers.get("content-encoding"),
        )
    # Each refusal's issue was committed, so the data notes can show it.
    except receiver.InvalidBody as error:
        on_write()
        return create_status_response(400, str(error))
    except receiver.PayloadTooLarge as error:
        on_write()
        return create_status_response(413, str(error))
    except receiver.UnsupportedMediaType as error:
        on_write()
        return create_status_response(415, str(error))
    except sqlite3.OperationalError:
        return create_unavailable_response()
    on_write()
    if not rejected and not has_skipped_parts:
        return JSONResponse({})
    # OTLP reads a partial success with 0 rejected spans and a message as a warning.
    problems = [f"{rejected} spans were not valid OTLP and were dropped"] if rejected else []
    if has_skipped_parts:
        problems.append("parts of the request that were not valid OTLP were skipped")
    message = "; ".join([*problems, "the dashboard's data notes list them"])
    return JSONResponse({"partialSuccess": {"rejectedSpans": rejected, "errorMessage": message}})


async def _receive_verdicts(
    request: Request,
    store: Store,
    config: Config,
    token_name: str,
    on_write: Callable[[], None],
) -> Response:
    body = await read_limited_body(
        request, verdict_api.MAX_BODY_BYTES, "send the verdicts in smaller requests"
    )
    try:
        accepted, rejected, is_committed = await run_in_threadpool(
            _store_verdicts, store, config, body, request.headers.get("content-type"), token_name
        )
    except verdict_api.InvalidBody as error:
        return create_status_response(400, str(error))
    except verdict_api.TooManyRows as error:
        return create_status_response(413, str(error))
    except verdict_api.UnsupportedMediaType as error:
        return create_status_response(415, str(error))
    except _FailedAfterCommit:
        # The verdicts are stored even though the request failed, so the recompute must know.
        on_write()
        return create_unavailable_response()
    except sqlite3.OperationalError:
        return create_unavailable_response()
    if is_committed:
        on_write()
    if not accepted and not rejected:
        rejected = [verdict_api.RejectedRow("body", "the request has no verdict rows")]
    # Reasons come from the parser, which already makes echoed input printable and short.
    content = {
        "accepted": accepted,
        "rejected": [{"where": row.where, "reason": row.reason} for row in rejected],
    }
    return JSONResponse(content, status_code=200 if accepted else 422)


async def read_limited_body(request: Request, max_bytes: int, hint: str) -> bytes:
    """Read the body, refusing it with 413 as soon as it is known to pass `max_bytes`.

    A body not fully received within BODY_READ_SECONDS is refused with 503, which OTLP
    clients retry.
    """
    pieces: list[bytes] = []
    too_large_message = f"the request body is over {max_bytes >> 20} MiB; {hint}"
    await receive_limited_body(request, max_bytes, too_large_message, pieces.append)
    return b"".join(pieces)


async def receive_limited_body(
    request: Request, max_bytes: int, too_large_message: str, write: Callable[[bytes], object]
) -> None:
    """Pass each piece of the body to `write`, as read_limited_body reads it.

    Raises 413 with `too_large_message` before reading when Content-Length is over
    `max_bytes`, or as soon as the pieces pass it; 503 after BODY_READ_SECONDS.
    """
    declared = request.headers.get("content-length", "")
    if declared.isascii() and declared.isdigit() and int(declared) > max_bytes:
        raise _BodyTooLarge(too_large_message)
    total = 0
    try:
        with anyio.fail_after(BODY_READ_SECONDS):
            async for piece in request.stream():
                total += len(piece)
                if total > max_bytes:
                    raise _BodyTooLarge(too_large_message)
                write(piece)
    except TimeoutError:
        raise HTTPException(
            503,
            f"the request body did not arrive within {BODY_READ_SECONDS} seconds; retry later",
            headers={"Retry-After": _RETRY_AFTER_SECONDS},
        ) from None
    except ClientDisconnect:
        # No one is left to read the answer; this only ends the request without a traceback.
        raise HTTPException(400, "the client closed the connection") from None


class _BodyTooLarge(HTTPException):
    """A 413 for a body over a route's size limit, refused before it was all read."""

    def __init__(self, message: str) -> None:
        super().__init__(413, message)


def _store_traces(
    store: Store, body: bytes, content_type: str | None, content_encoding: str | None
) -> tuple[int, bool]:
    """Parse and store one span batch; runs in a worker thread.

    Return the rejected span count and whether document or scope parts were skipped.
    """
    try:
        spans, issues, rejected = receiver.parse_traces_body(body, content_type, content_encoding)
    # Recorded so the dashboard's data notes show that a sender is posting bad data; an
    # exporter left on protobuf would otherwise lose every batch without a trace on the page.
    except receiver.InvalidBody as error:
        store.add_issues([_to_refusal_issue(400, str(error))])
        raise
    except receiver.PayloadTooLarge as error:
        store.add_issues([_to_refusal_issue(413, str(error))])
        raise
    except receiver.UnsupportedMediaType as error:
        store.add_issues([_to_refusal_issue(415, str(error))])
        raise
    store.add_spans(spans, issues)
    return rejected, any(issue.kind is IssueKind.INVALID_FILE for issue in issues)


def _to_refusal_issue(status: int, message: str) -> Issue:
    # The status leads the detail: the data notes count refusals by it and name its fix.
    return Issue(IssueKind.REFUSED_TRACE_REQUEST, INGEST_SUBJECT, f"HTTP {status}: {message}")


def _store_verdicts(
    store: Store, config: Config, body: bytes, content_type: str | None, token_name: str
) -> tuple[int, list[verdict_api.RejectedRow], bool]:
    """Parse and store one verdict post; runs in a worker thread.

    Return the accepted count, the rejected rows, and whether anything was committed.
    """
    rows, rejected, issues = verdict_api.parse_verdicts_body(body, content_type, config)
    if rows:
        store.put_verdicts(rows, token_name)
    if issues:
        try:
            store.add_issues(issues)
        except sqlite3.OperationalError as error:
            if rows:
                raise _FailedAfterCommit from error
            raise
    return len(rows), rejected, bool(rows or issues)


def _read_page(store: Store, mode: Literal["served", "ui"]) -> str:
    """The stored snapshot's page, or a waiting page until the first recompute has finished."""
    snapshot = store.read_snapshot()
    if snapshot is not None:
        return snapshot.html
    counts = store.read_counts()
    # Generation 0 and no time. A snapshot is computed only after a write, which advances the
    # generation, so the first snapshot's higher generation reads as newer and the page offers
    # a reload. A snapshot at generation 0 would not: with no time on the page, the tie never
    # breaks.
    served = ServedPage(generation=0, updated_at="", held_back_cases=0)
    waiting = WaitingCounts(
        span_count=counts.span_count,
        case_count=0,
        held_back_count=0,
        verdict_count=counts.verdict_count,
    )
    return render_waiting_page(waiting, [], served, mode=mode)


def read_status_content(store: Store, status: RecomputeStatus) -> dict[str, object]:
    """`GET /api/status`'s body; reads the store, so call it off the event loop."""
    snapshot = store.read_snapshot()
    counts = store.read_counts()
    return {
        "generation": 0 if snapshot is None else snapshot.generation,
        "updated_at": None if snapshot is None else to_iso_time(snapshot.finished_at_ns),
        "recompute_running": status.is_running,
        "last_error": status.last_error,
        "last_error_at": _to_optional_iso_time(status.last_error_at_ns),
        "held_back_cases": status.held_back_cases,
        "span_count": counts.span_count,
        "verdict_count": counts.verdict_count,
        "last_ingest_at": _to_optional_iso_time(counts.last_ingest_ns),
    }


def _to_optional_iso_time(time_ns: int | None) -> str | None:
    return None if time_ns is None else to_iso_time(time_ns)


class ServeApp(FastAPI):
    """The app with the access log and the security headers outside its whole stack.

    Middleware added the usual way runs inside the error handler, so the 500 it sends for an
    unexpected exception would carry no security headers and never reach the log.
    """

    def build_middleware_stack(self) -> ASGIApp:
        return _AccessLog(_SecurityHeaders(super().build_middleware_stack()))


class _SecurityHeaders:
    """Adds SECURITY_HEADERS to every response, errors included."""

    def __init__(self, app: ASGIApp) -> None:
        self._app = app

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self._app(scope, receive, send)
            return

        async def send_with_headers(message: Message) -> None:
            if message["type"] == "http.response.start":
                headers = [
                    (name, value)
                    for name, value in message.get("headers", [])
                    if name.lower() not in _SECURITY_HEADER_ITEMS
                ]
                headers.extend(_SECURITY_HEADER_ITEMS.items())
                message = {**message, "headers": headers}
            await send(message)

        await self._app(scope, receive, send_with_headers)


class _AccessLog:
    """Logs one line per request: method, path, status, client address and duration.

    Never the query string, headers or body: tokens travel in headers, and a careless client
    may put one in the query.
    """

    def __init__(self, app: ASGIApp) -> None:
        self._app = app

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self._app(scope, receive, send)
            return
        started = time.perf_counter()
        # An exception before the response starts becomes a 500 further out.
        status = 500

        async def send_and_note_status(message: Message) -> None:
            nonlocal status
            if message["type"] == "http.response.start":
                status = message["status"]
            await send(message)

        try:
            await self._app(scope, receive, send_and_note_status)
        finally:
            client = scope.get("client")
            access_logger.info(
                "%s %s %d %s %.1fms",
                scope["method"],
                # Escaped: a percent-encoded line break would otherwise forge a log line.
                to_terminal_text(scope["path"], limit=200),
                status,
                "-" if client is None else client[0],
                (time.perf_counter() - started) * 1000,
            )


def create_unreadable_response() -> JSONResponse:
    return create_status_response(
        503,
        "the database can't be read right now; retry later",
        {"Retry-After": _RETRY_AFTER_SECONDS},
    )


def create_unavailable_response() -> JSONResponse:
    return create_status_response(
        503,
        "the database can't take writes right now; retry later",
        {"Retry-After": _RETRY_AFTER_SECONDS},
    )


def create_status_response(
    status: int, message: str, headers: Mapping[str, str] | None = None
) -> JSONResponse:
    return JSONResponse(
        {"code": _STATUS_CODES.get(status, _UNKNOWN_CODE), "message": message},
        status_code=status,
        headers=headers,
    )
