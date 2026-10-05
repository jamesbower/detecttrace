from collections.abc import Iterator
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from detecttrace.serve.app import create_app
from detecttrace.serve.store import Store
from serve.app_support import create_config


@pytest.fixture
def app_store(tmp_path: Path) -> Iterator[Store]:
    opened = Store.open(tmp_path / "detecttrace.db")
    yield opened
    opened.close()


@pytest.fixture
def writes() -> list[int]:
    """One entry per on_write call the app makes."""
    return []


@pytest.fixture
def client(tmp_path: Path, app_store: Store, writes: list[int]) -> Iterator[TestClient]:
    app = create_app(
        create_config(tmp_path / "detecttrace.db"), app_store, lambda: writes.append(1)
    )
    with TestClient(app) as test_client:
        yield test_client
