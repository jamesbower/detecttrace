"""The HTTP app of `detecttrace serve`: token checks, the health check, and OTLP/HTTP ingest.

Error responses carry a google.rpc.Status body (`code`, `message`), the shape OTLP/HTTP
clients expect. No response or error ever repeats the token a client sent.
"""

import base64
import sqlite3
from collections.abc import Callable, Mapping

import anyio
from fastapi import Depends, FastAPI, Request
from fastapi.responses import JSONResponse, PlainTextResponse, Response
from starlette.concurrency import run_in_threadpool
from starlette.exceptions import HTTPException
from starlette.requests import ClientDisconnect

from detecttrace.model import IssueKind
from detecttrace.serve.auth import Role, find_token_name
from detecttrace.serve.config import ServeConfig, TokenRoles
from detecttrace.serve.receiver import (
    MAX_BODY_BYTES,
    InvalidBody,
    PayloadTooLarge,
    UnsupportedMediaType,
    parse_traces_body,
)
from detecttrace.serve.store import Store

# google.rpc.Code values for the HTTP statuses this app answers with.
_STATUS_CODES = {400: 3, 401: 16, 403: 7, 404: 5, 405: 12, 413: 3, 415: 3, 503: 14}
_UNKNOWN_CODE = 2
_RETRY_AFTER_SECONDS = "5"
_ROLES: tuple[Role, ...] = ("ingest", "verdicts", "read")
# Each ingest holds up to 16 MiB of body and 32 MiB decompressed plus its spans, so a burst
# of parallel batches is queued rather than allowed to exhaust a small container's memory.
MAX_CONCURRENT_INGESTS = 4


def create_app(config: ServeConfig, store: Store, on_write: Callable[[], None]) -> FastAPI:
    """Build the app; `on_write` is called after every commit, a rejected body's issue too."""
    # No schema or docs pages: the API surface is not advertised to whoever can reach it.
    app = FastAPI(docs_url=None, redoc_url=None, openapi_url=None)

    @app.exception_handler(HTTPException)
    async def respond_with_status(request: Request, error: HTTPException) -> Response:
        return _create_status_response(error.status_code, str(error.detail), error.headers)

    require_ingest = require_role(config.tokens, "ingest", allow_basic=False)
    ingest_slots = anyio.Semaphore(MAX_CONCURRENT_INGESTS)

    @app.get("/healthz")
    async def check_health() -> Response:
        try:
            await run_in_threadpool(store.check_writable)
        except sqlite3.OperationalError:
            return PlainTextResponse("unavailable", status_code=503)
        return PlainTextResponse("ok")

    @app.post("/v1/traces", dependencies=[Depends(require_ingest)])
    async def receive_traces(request: Request) -> Response:
        async with ingest_slots:
            return await _receive_traces(request, store, on_write)

    return app


def require_role(tokens: TokenRoles, role: Role, *, allow_basic: bool) -> Callable[[Request], str]:
    """Return a dependency that admits only `role`'s tokens and gives the token's name.

    The token comes as `Authorization: Bearer <token>`: the scheme in any case, any spaces
    before the token, and nothing after it. With `allow_basic`, Basic credentials whose
    password is the token are also accepted, so a browser can sign in; the user is ignored.
    A missing or unknown token is 401; a known token of another role only is 403.
    """
    challenge = 'Bearer, Basic realm="detecttrace"' if allow_basic else "Bearer"

    def check_token(request: Request) -> str:
        token = _read_token(request.headers.get("authorization"), allow_basic)
        if token is None:
            raise _to_unauthorized(challenge)
        name = find_token_name(token, getattr(tokens, role))
        if name is not None:
            return name
        if any(find_token_name(token, getattr(tokens, other)) for other in _ROLES):
            raise HTTPException(403, f"this token may not be used for {role}")
        raise _to_unauthorized(challenge)

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


def _to_unauthorized(challenge: str) -> HTTPException:
    return HTTPException(
        401, "a valid access token is required", headers={"WWW-Authenticate": challenge}
    )


async def _receive_traces(request: Request, store: Store, on_write: Callable[[], None]) -> Response:
    try:
        body = await _read_limited_body(request)
    except ClientDisconnect:
        # No one is left to read a response, so this only ends the request without a trace.
        return Response(status_code=400)
    try:
        rejected, has_skipped_parts = await run_in_threadpool(
            _store_traces,
            store,
            body,
            request.headers.get("content-type"),
            request.headers.get("content-encoding"),
        )
    except InvalidBody as error:
        # Its issue was committed, so the data notes can show it.
        on_write()
        return _create_status_response(400, str(error))
    except PayloadTooLarge as error:
        return _create_status_response(413, str(error))
    except UnsupportedMediaType as error:
        return _create_status_response(415, str(error))
    except sqlite3.OperationalError:
        return _create_status_response(
            503,
            "the database can't take writes right now; retry later",
            {"Retry-After": _RETRY_AFTER_SECONDS},
        )
    on_write()
    if not rejected and not has_skipped_parts:
        return JSONResponse({})
    # OTLP reads a partial success with 0 rejected spans and a message as a warning.
    problems = [f"{rejected} spans were not valid OTLP and were dropped"] if rejected else []
    if has_skipped_parts:
        problems.append("parts of the request that were not valid OTLP were skipped")
    message = "; ".join([*problems, "the dashboard's data notes list them"])
    return JSONResponse({"partialSuccess": {"rejectedSpans": rejected, "errorMessage": message}})


async def _read_limited_body(request: Request) -> bytes:
    """Read the body, refusing it with 413 as soon as it is known to pass MAX_BODY_BYTES."""
    declared = request.headers.get("content-length", "")
    if declared.isascii() and declared.isdigit() and int(declared) > MAX_BODY_BYTES:
        raise _to_body_too_large()
    pieces: list[bytes] = []
    total = 0
    async for piece in request.stream():
        total += len(piece)
        if total > MAX_BODY_BYTES:
            raise _to_body_too_large()
        pieces.append(piece)
    return b"".join(pieces)


def _to_body_too_large() -> HTTPException:
    return HTTPException(
        413,
        f"the request body is over {MAX_BODY_BYTES >> 20} MiB; lower send_batch_max_size in "
        "the Collector's batch processor",
    )


def _store_traces(
    store: Store, body: bytes, content_type: str | None, content_encoding: str | None
) -> tuple[int, bool]:
    """Parse and store one request; runs in a worker thread.

    Return the rejected span count and whether document or scope parts were skipped.
    """
    try:
        spans, issues, rejected = parse_traces_body(body, content_type, content_encoding)
    except InvalidBody as error:
        # Recorded so the dashboard's data notes show that a sender is posting bad data.
        store.add_issues(error.issues)
        raise
    store.add_spans(spans, issues)
    return rejected, any(issue.kind is IssueKind.INVALID_FILE for issue in issues)


def _create_status_response(
    status: int, message: str, headers: Mapping[str, str] | None = None
) -> JSONResponse:
    return JSONResponse(
        {"code": _STATUS_CODES.get(status, _UNKNOWN_CODE), "message": message},
        status_code=status,
        headers=headers,
    )
