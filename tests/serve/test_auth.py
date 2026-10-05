import hmac
import re
from collections.abc import Callable

import pytest

from detecttrace.serve.auth import create_token, find_token_name, hash_token
from detecttrace.serve.config import HASH_PATTERN, TokenEntry

INGEST = [TokenEntry(name="collector", hash=hash_token("ingest-secret"))]
READ = [
    TokenEntry(name="analysts", hash=hash_token("read-secret")),
    TokenEntry(name="ops", hash=hash_token("ops-secret")),
]


def test_matching_token_gives_its_name() -> None:
    assert find_token_name("ops-secret", READ) == "ops"


def test_wrong_token_gives_none() -> None:
    assert find_token_name("nope", READ) is None


def test_token_from_another_role_gives_none() -> None:
    assert find_token_name("ingest-secret", READ) is None


def test_empty_entry_list_gives_none() -> None:
    assert find_token_name("ops-secret", []) is None


def test_hash_matches_the_config_pattern() -> None:
    assert re.fullmatch(HASH_PATTERN, hash_token("anything"))


def test_created_token_is_long_enough() -> None:
    assert len(create_token()) >= 43


def test_created_tokens_differ() -> None:
    assert create_token() != create_token()


def test_every_entry_is_compared_even_when_the_first_matches(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls: list[tuple[str, str]] = []
    real: Callable[[str, str], bool] = hmac.compare_digest

    def record(left: str, right: str) -> bool:
        calls.append((left, right))
        return real(left, right)

    monkeypatch.setattr("detecttrace.serve.auth.hmac.compare_digest", record)

    find_token_name("read-secret", READ)

    assert len(calls) == len(READ)
