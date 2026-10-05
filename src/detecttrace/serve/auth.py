"""Access tokens: creating them, hashing them for the configuration, and matching them."""

import hashlib
import hmac
import secrets
from collections.abc import Sequence
from typing import Literal

from detecttrace.serve.config import TokenEntry

Role = Literal["ingest", "verdicts", "read"]


def create_token() -> str:
    return secrets.token_urlsafe(32)


def hash_token(token: str) -> str:
    return "sha256:" + hashlib.sha256(token.encode()).hexdigest()


def find_token_name(token: str, entries: Sequence[TokenEntry]) -> str | None:
    """Return the name of the entry whose hash matches `token`, or None."""
    candidate = hash_token(token)
    found: str | None = None
    # Every entry is compared, so the time taken does not reveal which entry matched.
    for entry in entries:
        if hmac.compare_digest(candidate, entry.hash) and found is None:
            found = entry.name
    return found
