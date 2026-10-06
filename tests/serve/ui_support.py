"""Shared pieces for the `detecttrace ui` route tests: an app whose recompute runs in-process."""

from collections.abc import Callable
from concurrent.futures import Executor, Future
from pathlib import Path
from typing import Any

import httpx
from fastapi.testclient import TestClient

PORT = 8765
ORIGIN = f"http://127.0.0.1:{PORT}"
WRITE_HEADERS = {"X-DetectTrace": "1", "Origin": ORIGIN}
JSON_HEADERS = {**WRITE_HEADERS, "Content-Type": "application/json"}
UPLOAD_HEADERS = {**WRITE_HEADERS, "Content-Type": "application/octet-stream"}
DEMO_DATA = Path(__file__).parents[2] / "src" / "detecttrace" / "demo_data"
DEMO_TRACES = DEMO_DATA / "traces" / "traces.jsonl.gz"
DEMO_VERDICTS = DEMO_DATA / "verdicts.csv"
DEMO_CHECKLIST = DEMO_DATA / "checklists" / "impossible_travel.yaml"
NO_EDITS: dict[str, object] = {"fields": {}, "labels": {}}


class FakeClock:
    def __init__(self) -> None:
        self.now = 1_000.0

    def __call__(self) -> float:
        return self.now


class InlineExecutor(Executor):
    """Runs each call at once, in the caller's thread, and keeps each call's arguments."""

    def __init__(self) -> None:
        self.calls: list[tuple[Any, ...]] = []

    @property
    def count(self) -> int:
        return len(self.calls)

    def submit(self, fn: Callable[..., Any], /, *args: Any, **kwargs: Any) -> Future[Any]:
        self.calls.append(args)
        future: Future[Any] = Future()
        future.set_result(fn(*args, **kwargs))
        return future


def upload_file(client: TestClient, kind: str, path: Path) -> httpx.Response:
    return client.post(
        f"/api/upload/{kind}",
        params={"name": path.name},
        content=path.read_bytes(),
        headers=UPLOAD_HEADERS,
    )


def post_json(client: TestClient, path: str, body: object) -> httpx.Response:
    return client.post(path, json=body, headers=JSON_HEADERS)
