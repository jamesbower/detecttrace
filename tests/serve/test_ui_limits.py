import gzip
from collections.abc import Iterator
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from detecttrace import traces
from detecttrace.serve import ui
from detecttrace.serve.store import Store
from detecttrace.serve.ui import UiState, create_ui_app
from detecttrace.serve.ui_uploads import MAX_TRACE_FILE_BYTES, MAX_VERDICT_FILE_BYTES

PORT = 8765
ORIGIN = f"http://127.0.0.1:{PORT}"
UPLOAD_HEADERS = {
    "X-DetectTrace": "1",
    "Origin": ORIGIN,
    "Content-Type": "application/octet-stream",
}
DEMO_DATA = Path(__file__).parents[2] / "src" / "detecttrace" / "demo_data"
DEMO_TRACES = DEMO_DATA / "traces" / "traces.jsonl.gz"
DEMO_VERDICTS = DEMO_DATA / "verdicts.csv"
LANGFUSE_TRACES = (
    Path(__file__).parent.parent / "fixtures" / "formats" / "langfuse" / "traces" / "api_page.json"
)
SMALL_CAP = 1024


@pytest.fixture
def data_dir(tmp_path: Path) -> Path:
    folder = tmp_path / "data"
    folder.mkdir()
    return folder


@pytest.fixture
def store(data_dir: Path) -> Iterator[Store]:
    opened = Store.open(data_dir / "detecttrace.db")
    yield opened
    opened.close()


@pytest.fixture
def client(store: Store, data_dir: Path) -> Iterator[TestClient]:
    app = create_ui_app(port=PORT, state=UiState(store=store, data_dir=data_dir))
    with TestClient(app, base_url=ORIGIN) as test_client:
        yield test_client


@pytest.fixture
def small_trace_cap(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(ui, "MAX_TRACE_FILE_BYTES", SMALL_CAP)


def upload(client: TestClient, kind: str, name: str, body: bytes, **headers: str):
    return client.post(
        f"/api/upload/{kind}",
        params={"name": name},
        content=body,
        headers={**UPLOAD_HEADERS, **headers},
    )


def upload_in_pieces(client: TestClient, body: bytes):
    """Send the body without a Content-Length, so only the reading can find it too large."""
    return client.post(
        "/api/upload/traces",
        params={"name": DEMO_TRACES.name},
        content=iter([body[:SMALL_CAP], body[SMALL_CAP:]]),
        headers=UPLOAD_HEADERS,
    )


def list_temporary_folders(data_dir: Path) -> list[str]:
    return [entry.name for entry in data_dir.iterdir() if entry.name.startswith("tmp")]


# The temporary folder


def test_the_temporary_folder_is_removed_after_an_upload(
    client: TestClient, data_dir: Path
) -> None:
    upload(client, "traces", DEMO_TRACES.name, DEMO_TRACES.read_bytes())

    assert list_temporary_folders(data_dir) == []


def test_the_temporary_folder_is_removed_after_a_refusal(
    client: TestClient, data_dir: Path
) -> None:
    upload(client, "traces", DEMO_TRACES.name, DEMO_TRACES.read_bytes())
    upload(client, "traces", LANGFUSE_TRACES.name, LANGFUSE_TRACES.read_bytes())

    assert list_temporary_folders(data_dir) == []


@pytest.mark.usefixtures("small_trace_cap")
def test_the_temporary_folder_is_removed_after_a_file_over_the_cap(
    client: TestClient, data_dir: Path
) -> None:
    upload_in_pieces(client, DEMO_TRACES.read_bytes())

    assert list_temporary_folders(data_dir) == []


# The size cap


@pytest.mark.usefixtures("small_trace_cap")
def test_a_file_over_the_cap_answers_413(client: TestClient) -> None:
    response = upload_in_pieces(client, DEMO_TRACES.read_bytes())

    assert response.status_code == 413


@pytest.mark.usefixtures("small_trace_cap")
def test_a_file_over_the_cap_stores_nothing(client: TestClient, store: Store) -> None:
    upload_in_pieces(client, DEMO_TRACES.read_bytes())

    assert store.read_counts().span_count == 0


def test_a_declared_length_over_the_cap_is_refused_before_reading(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(ui, "MAX_VERDICT_FILE_BYTES", SMALL_CAP)
    # The body is under the cap; only the declared length is over it.
    response = upload(
        client, "verdicts", "verdicts.csv", b"case_id\n", **{"Content-Length": str(SMALL_CAP + 1)}
    )

    assert response.status_code == 413


@pytest.mark.parametrize(
    ("kind", "name", "cap", "message"),
    [
        pytest.param(
            "traces", "x.jsonl", MAX_TRACE_FILE_BYTES, "the file is over 256 MiB", id="traces"
        ),
        pytest.param(
            "verdicts", "x.csv", MAX_VERDICT_FILE_BYTES, "the file is over 64 MiB", id="verdicts"
        ),
    ],
)
def test_a_declared_length_over_the_real_cap_names_it(
    client: TestClient, kind: str, name: str, cap: int, message: str
) -> None:
    response = upload(client, kind, name, b"", **{"Content-Length": str(cap + 1)})

    assert response.json() == {"code": 3, "message": message}


# Compressed files


def test_a_compressed_document_past_the_document_limit_stores_no_spans(
    client: TestClient, store: Store
) -> None:
    # A real span document padded past the limit; it compresses to a few dozen kilobytes.
    first_line = gzip.decompress(DEMO_TRACES.read_bytes()).split(b"\n", 1)[0]
    padding = b'{"padding": "' + b"a" * traces.MAX_DOCUMENT_BYTES + b'", '
    bomb = gzip.compress(padding + first_line[1:] + b"\n", compresslevel=1)

    upload(client, "traces", "bomb.jsonl.gz", bomb)

    assert store.read_counts().span_count == 0
