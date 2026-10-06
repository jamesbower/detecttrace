"""Run `detecttrace serve` as a real process on the demo data, for end-to-end tests."""

import gzip
import json
import signal
import socket
import subprocess
import sys
import time
from dataclasses import dataclass
from importlib.resources import files
from pathlib import Path
from typing import IO

import httpx

from detecttrace.serve.auth import hash_token
from serve.app_support import INGEST_TOKEN, READ_TOKEN, VERDICTS_TOKEN

DEMO_FOLDER = Path(str(files("detecttrace") / "demo_data"))
DEMO_LABEL_MAP = {
    "TP": "true_positive",
    "Malicious": "true_positive",
    "FP": "false_positive",
    "Benign": "benign",
    "Closed - Benign": "benign",
}
STARTUP_SECONDS = 30
STOP_SECONDS = 30


@dataclass
class ServedProcess:
    process: subprocess.Popen[bytes]
    base_url: str
    stdout_path: Path
    stderr_path: Path
    _outputs: tuple[IO[bytes], IO[bytes]]

    def get(self, path: str) -> httpx.Response:
        return httpx.get(
            self.base_url + path, headers={"Authorization": f"Bearer {READ_TOKEN}"}, timeout=10
        )

    def post_traces(self, body: bytes) -> httpx.Response:
        return httpx.post(
            self.base_url + "/v1/traces",
            content=body,
            headers={"Authorization": f"Bearer {INGEST_TOKEN}", "Content-Type": "application/json"},
            timeout=30,
        )

    def post_verdicts_csv(self, text: str) -> httpx.Response:
        return httpx.post(
            self.base_url + "/api/verdicts",
            content=text.encode("utf-8"),
            headers={
                "Authorization": f"Bearer {VERDICTS_TOKEN}",
                "Content-Type": "text/csv; charset=utf-8",
            },
            timeout=30,
        )

    def wait_for_generation(self, minimum: int, timeout: float) -> dict[str, object]:
        """Poll /api/status until the snapshot's generation is at least `minimum`."""
        deadline = time.monotonic() + timeout
        status: dict[str, object] = {}
        while time.monotonic() < deadline:
            status = self.get("/api/status").json()
            generation = status["generation"]
            if isinstance(generation, int) and generation >= minimum:
                return status
            time.sleep(0.5)
        raise TimeoutError(f"the snapshot did not reach generation {minimum}: {status}")

    def stop(self, signum: int = signal.SIGTERM) -> int:
        """Send `signum` and return the exit code."""
        self.process.send_signal(signum)
        try:
            return self.process.wait(STOP_SECONDS)
        finally:
            self.close()

    def close(self) -> None:
        if self.process.poll() is None:
            self.process.kill()
            self.process.wait()
        for output in self._outputs:
            output.close()


def write_serve_config(folder: Path, port: int) -> Path:
    """A serve configuration for the demo data, with every case settled as soon as it arrives."""
    config = {
        "serve": {"database": "detecttrace.db", "port": port, "settle_seconds": 0},
        "label_map": DEMO_LABEL_MAP,
        "checklists": str(DEMO_FOLDER / "checklists"),
        "tokens": {
            "ingest": [{"name": "collector", "hash": hash_token(INGEST_TOKEN)}],
            "verdicts": [{"name": "soar", "hash": hash_token(VERDICTS_TOKEN)}],
            "read": [{"name": "analysts", "hash": hash_token(READ_TOKEN)}],
        },
    }
    path = folder / "detecttrace-serve.yaml"
    # JSON is YAML 1.2.
    path.write_text(json.dumps(config, indent=2), encoding="utf-8")
    return path


def find_free_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as probe:
        probe.bind(("127.0.0.1", 0))
        return probe.getsockname()[1]


def start_server(folder: Path) -> ServedProcess:
    """Start `detecttrace serve` in `folder` and wait until /healthz answers."""
    port = find_free_port()
    config_path = write_serve_config(folder, port)
    stdout_path = folder / "stdout.txt"
    stderr_path = folder / "stderr.txt"
    stdout = stdout_path.open("wb")
    stderr = stderr_path.open("wb")
    process = subprocess.Popen(
        [sys.executable, "-m", "detecttrace", "serve", "--config", str(config_path)],
        stdout=stdout,
        stderr=stderr,
        cwd=folder,
    )
    served = ServedProcess(
        process, f"http://127.0.0.1:{port}", stdout_path, stderr_path, (stdout, stderr)
    )
    return _wait_until_healthy(served, "detecttrace serve")


def start_ui(folder: Path, data_dir: Path, args: list[str], env: dict[str, str]) -> ServedProcess:
    """Start `detecttrace ui` with its data in `data_dir` and wait until /healthz answers."""
    port = find_free_port()
    stdout_path = folder / "stdout.txt"
    stderr_path = folder / "stderr.txt"
    stdout = stdout_path.open("wb")
    stderr = stderr_path.open("wb")
    process = subprocess.Popen(
        [
            sys.executable,
            "-m",
            "detecttrace",
            "ui",
            "--port",
            str(port),
            "--data-dir",
            str(data_dir),
            *args,
        ],
        stdout=stdout,
        stderr=stderr,
        cwd=folder,
        env=env,
    )
    served = ServedProcess(
        process, f"http://127.0.0.1:{port}", stdout_path, stderr_path, (stdout, stderr)
    )
    return _wait_until_healthy(served, "detecttrace ui")


def _wait_until_healthy(served: ServedProcess, command: str) -> ServedProcess:
    process = served.process
    stderr_path = served.stderr_path
    deadline = time.monotonic() + STARTUP_SECONDS
    while time.monotonic() < deadline:
        if process.poll() is not None:
            served.close()
            raise RuntimeError(stderr_path.read_text(encoding="utf-8"))
        try:
            if httpx.get(served.base_url + "/healthz", timeout=1).status_code == 200:
                return served
        except httpx.TransportError:
            pass
        time.sleep(0.2)
    served.close()
    raise TimeoutError(f"{command} did not answer /healthz in time")


def post_demo_data(served: ServedProcess) -> None:
    """Post every demo trace document, one per request as a Collector would, then the verdicts."""
    with httpx.Client(
        base_url=served.base_url,
        headers={"Authorization": f"Bearer {INGEST_TOKEN}", "Content-Type": "application/json"},
        timeout=30,
    ) as client:
        for path in sorted((DEMO_FOLDER / "traces").glob("*.jsonl.gz")):
            for line in gzip.decompress(path.read_bytes()).splitlines():
                client.post("/v1/traces", content=line).raise_for_status()
    csv_text = (DEMO_FOLDER / "verdicts.csv").read_text(encoding="utf-8")
    served.post_verdicts_csv(csv_text).raise_for_status()


def split_demo_traces(spans_per_batch: int) -> list[bytes]:
    """The demo spans as OTLP JSON bodies of at most `spans_per_batch` spans each.

    Each batch keeps its spans' resource and scope, so it stores exactly those spans.
    """
    batches: list[bytes] = []
    for path in sorted((DEMO_FOLDER / "traces").glob("*.jsonl.gz")):
        for line in gzip.decompress(path.read_bytes()).splitlines():
            for resource_spans in json.loads(line)["resourceSpans"]:
                for scope_spans in resource_spans["scopeSpans"]:
                    spans = scope_spans["spans"]
                    for start in range(0, len(spans), spans_per_batch):
                        scope_part = {
                            **scope_spans,
                            "spans": spans[start : start + spans_per_batch],
                        }
                        document = {
                            "resourceSpans": [{**resource_spans, "scopeSpans": [scope_part]}]
                        }
                        batches.append(json.dumps(document).encode("utf-8"))
    return batches
