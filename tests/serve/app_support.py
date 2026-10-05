"""Tokens, a config and captured Collector requests for the HTTP app tests."""

import json
from pathlib import Path

from detecttrace.serve.auth import create_token, hash_token
from detecttrace.serve.config import ServeConfig

INGEST_TOKEN = create_token()
VERDICTS_TOKEN = create_token()
READ_TOKEN = create_token()
COLLECTOR_ROOT = Path(__file__).parent.parent / "fixtures" / "collector_real"


def create_config(database: Path) -> ServeConfig:
    return ServeConfig.model_validate(
        {
            "serve": {"database": str(database)},
            "tokens": {
                "ingest": [{"name": "collector", "hash": hash_token(INGEST_TOKEN)}],
                "verdicts": [{"name": "soar", "hash": hash_token(VERDICTS_TOKEN)}],
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
