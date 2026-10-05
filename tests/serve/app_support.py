"""Tokens, a config, captured Collector requests and request drivers for the HTTP app tests."""

import json
import threading
from collections.abc import Awaitable, Callable
from pathlib import Path

import anyio
from starlette.types import ASGIApp, Message, Scope

from detecttrace.serve.auth import create_token, hash_token
from detecttrace.serve.config import ServeConfig

INGEST_TOKEN = create_token()
VERDICTS_TOKEN = create_token()
SECOND_VERDICTS_TOKEN = create_token()
READ_TOKEN = create_token()
COLLECTOR_ROOT = Path(__file__).parent.parent / "fixtures" / "collector_real"


def create_config(database: Path) -> ServeConfig:
    return ServeConfig.model_validate(
        {
            "serve": {"database": str(database)},
            "label_map": {"TP": "true_positive", "FP": "false_positive"},
            "tokens": {
                "ingest": [{"name": "collector", "hash": hash_token(INGEST_TOKEN)}],
                "verdicts": [
                    {"name": "soar", "hash": hash_token(VERDICTS_TOKEN)},
                    {"name": "case-tool", "hash": hash_token(SECOND_VERDICTS_TOKEN)},
                ],
                "read": [{"name": "analysts", "hash": hash_token(READ_TOKEN)}],
            },
        }
    )


def read_collector_request(name: str) -> tuple[bytes, dict[str, str]]:
    """Return a captured Collector request's body and the headers to resend it with."""
    body = (COLLECTOR_ROOT / name).read_bytes()
    sidecar = COLLECTOR_ROOT / (name.split(".")[0] + ".headers.json")
    captured = json.loads(sidecar.read_text(encoding="utf-8"))["headers"]
    # httpx sets Host and Content-Length itself; the captured token is redacted.
    headers = {
        key: value
        for key, value in captured.items()
        if key not in ("Host", "Content-Length", "Authorization")
    }
    return body, {**headers, "Authorization": f"Bearer {INGEST_TOKEN}"}


def run_raw_request(
    app: ASGIApp, path: str, token: str, receive: Callable[[], Awaitable[Message]]
) -> list[Message]:
    """Run one POST through the app with a hand-written `receive`; return what it sent.

    A 5-second guard turns an app that waits forever into a failure instead of a hang.
    """
    scope: Scope = {
        "type": "http",
        "asgi": {"version": "3.0"},
        "http_version": "1.1",
        "method": "POST",
        "scheme": "http",
        "path": path,
        "raw_path": path.encode("ascii"),
        "query_string": b"",
        "root_path": "",
        "headers": [
            (b"authorization", f"Bearer {token}".encode("ascii")),
            (b"content-type", b"application/json"),
        ],
        "client": ("127.0.0.1", 50000),
        "server": ("testserver", 80),
    }
    sent: list[Message] = []

    async def send(message: Message) -> None:
        sent.append(message)

    async def run() -> None:
        with anyio.fail_after(5):
            await app(scope, receive, send)

    anyio.run(run)
    return sent


class ConcurrencyProbe:
    """A stand-in store method that records how many callers are inside it at once.

    Each caller is held until `expected` are inside together. Under a lower limit that never
    happens, so the first wait times out and releases everyone after it.
    """

    def __init__(self, expected: int, result: object) -> None:
        self.peak = 0
        self._expected = expected
        self._result = result
        self._active = 0
        self._is_released = False
        self._condition = threading.Condition()

    def __call__(self, *args: object, **kwargs: object) -> object:
        with self._condition:
            self._active += 1
            self.peak = max(self.peak, self._active)
            self._condition.notify_all()
            self._condition.wait_for(
                lambda: self._is_released or self._active == self._expected, timeout=0.5
            )
            self._is_released = True
            self._condition.notify_all()
            self._active -= 1
        return self._result
