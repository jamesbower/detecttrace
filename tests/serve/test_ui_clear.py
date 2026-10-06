import sqlite3
import sys
import threading
from collections.abc import Iterator
from concurrent.futures import ThreadPoolExecutor, wait
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from detecttrace.serve import ui_uploads
from detecttrace.serve.store import Store
from detecttrace.serve.ui import UiState, create_ui_app
from detecttrace.serve.ui_config import CHECKLISTS_FOLDER, CONFIG_NAME
from detecttrace.serve.ui_recompute import UiRecompute
from detecttrace.traces import load_spans
from serve.ui_support import (
    DEMO_CHECKLIST,
    DEMO_DATA,
    DEMO_TRACES,
    DEMO_VERDICTS,
    NO_EDITS,
    ORIGIN,
    PORT,
    WRITE_HEADERS,
    FakeClock,
    InlineExecutor,
    post_json,
    upload_file,
)

CONFIRM = {"confirm": True}
UNCONFIRMED_BODIES = [
    pytest.param({}, id="no confirm"),
    pytest.param({"confirm": False}, id="false"),
    pytest.param({"confirm": 1}, id="one"),
    pytest.param({"confirm": "true"}, id="text"),
    pytest.param({"confirm": True, "everything": True}, id="extra key"),
    pytest.param([True], id="not an object"),
]
# Past every debounce and maximum wait, so a configured app would have recomputed by then.
LONG_AFTER_SECONDS = 3_600.0
SECOND_TRACES = DEMO_DATA / "traces" / "traces-2026-08-18T00-57-52.000.jsonl.gz"
LANGFUSE_TRACES = (
    DEMO_DATA.parents[2]
    / "tests"
    / "fixtures"
    / "formats"
    / "langfuse"
    / "traces"
    / "api_page.json"
)
# Long enough for an unserialised request to finish; a serialised one waits regardless.
OVERLAP_SECONDS = 0.5
needs_symlinks = pytest.mark.skipif(
    sys.platform == "win32", reason="creating a symlink needs a privilege on Windows"
)


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
def executor() -> InlineExecutor:
    return InlineExecutor()


@pytest.fixture
def clock() -> FakeClock:
    return FakeClock()


@pytest.fixture
def state(store: Store, data_dir: Path, executor: InlineExecutor, clock: FakeClock) -> UiState:
    recompute = UiRecompute(data_dir / "detecttrace.db", lambda: executor)
    return UiState(store=store, data_dir=data_dir, recompute=recompute, clock=clock)


@pytest.fixture
def client(state: UiState) -> Iterator[TestClient]:
    with TestClient(create_ui_app(port=PORT, state=state), base_url=ORIGIN) as test_client:
        yield test_client


@pytest.fixture
def configured(client: TestClient, state: UiState) -> TestClient:
    """Uploaded demo data, a saved configuration and a computed dashboard."""
    upload_file(client, "traces", DEMO_TRACES)
    upload_file(client, "verdicts", DEMO_VERDICTS)
    upload_file(client, "checklists", DEMO_CHECKLIST)
    post_json(client, "/api/config", NO_EDITS).raise_for_status()
    state.tick()
    state.tick()
    return client


@pytest.fixture
def cleared(configured: TestClient) -> TestClient:
    post_json(configured, "/api/data/clear", CONFIRM).raise_for_status()
    return configured


# Refused


@pytest.mark.parametrize("body", UNCONFIRMED_BODIES)
def test_a_clear_without_confirm_is_refused(configured: TestClient, body: object) -> None:
    response = post_json(configured, "/api/data/clear", body)

    assert response.status_code == 400


def test_a_clear_whose_body_is_not_json_is_refused(configured: TestClient) -> None:
    response = configured.post(
        "/api/data/clear",
        content=b"confirm",
        headers={**WRITE_HEADERS, "Content-Type": "application/json"},
    )

    assert response.status_code == 400


def test_a_clear_without_the_write_header_is_refused(configured: TestClient) -> None:
    response = configured.post("/api/data/clear", json=CONFIRM, headers={"Origin": ORIGIN})

    assert response.status_code == 403


def test_a_refused_clear_keeps_the_stored_spans(configured: TestClient, store: Store) -> None:
    post_json(configured, "/api/data/clear", {})

    assert store.read_counts().span_count == 584


def test_a_refused_clear_keeps_the_configuration(configured: TestClient, data_dir: Path) -> None:
    post_json(configured, "/api/data/clear", {})

    assert (data_dir / CONFIG_NAME).is_file()


def test_a_refused_clear_keeps_the_checklists(configured: TestClient, data_dir: Path) -> None:
    post_json(configured, "/api/data/clear", {})

    assert (data_dir / CHECKLISTS_FOLDER / DEMO_CHECKLIST.name).is_file()


# Cleared


def test_a_clear_answers_with_an_empty_object(configured: TestClient) -> None:
    response = post_json(configured, "/api/data/clear", CONFIRM)

    assert response.json() == {}


def test_a_clear_empties_the_store(cleared: TestClient, store: Store) -> None:
    counts = store.read_counts()

    assert (counts.span_count, counts.verdict_count) == (0, 0)


def test_a_clear_drops_the_dashboard(cleared: TestClient, store: Store) -> None:
    assert store.read_snapshot() is None


def test_a_clear_deletes_the_configuration(cleared: TestClient, data_dir: Path) -> None:
    assert not (data_dir / CONFIG_NAME).exists()


def test_a_clear_deletes_the_checklists(cleared: TestClient, data_dir: Path) -> None:
    assert list((data_dir / CHECKLISTS_FOLDER).iterdir()) == []


def test_a_clear_keeps_the_checklists_folder(cleared: TestClient, data_dir: Path) -> None:
    assert (data_dir / CHECKLISTS_FOLDER).is_dir()


def test_the_state_is_not_configured_after_a_clear(cleared: TestClient) -> None:
    assert cleared.get("/api/ui/state").json()["is_configured"] is False


def test_an_upload_after_a_clear_computes_nothing(
    cleared: TestClient, state: UiState, clock: FakeClock, executor: InlineExecutor
) -> None:
    upload_file(cleared, "traces", DEMO_TRACES)
    clock.now += LONG_AFTER_SECONDS

    state.tick()
    state.tick()

    assert executor.count == 1


def test_a_clear_without_a_configuration_succeeds(client: TestClient) -> None:
    response = post_json(client, "/api/data/clear", CONFIRM)

    assert response.status_code == 200


@needs_symlinks
def test_a_clear_keeps_the_target_of_a_linked_checklist(
    configured: TestClient, tmp_path: Path, data_dir: Path
) -> None:
    target = tmp_path / "elsewhere.yaml"
    target.write_text("kept", encoding="utf-8")
    (data_dir / CHECKLISTS_FOLDER / "linked.yaml").symlink_to(target)

    post_json(configured, "/api/data/clear", CONFIRM)

    assert target.read_text(encoding="utf-8") == "kept"


@needs_symlinks
def test_a_clear_keeps_the_files_of_a_linked_checklists_folder(
    client: TestClient, tmp_path: Path, data_dir: Path
) -> None:
    elsewhere = tmp_path / "elsewhere"
    elsewhere.mkdir()
    (elsewhere / "kept.yaml").write_text("kept", encoding="utf-8")
    (data_dir / CHECKLISTS_FOLDER).symlink_to(elsewhere, target_is_directory=True)

    post_json(client, "/api/data/clear", CONFIRM)

    assert (elsewhere / "kept.yaml").is_file()


def fail_to_write() -> None:
    raise sqlite3.OperationalError("disk I/O error")


def test_a_clear_the_store_cannot_take_answers_503(
    configured: TestClient, store: Store, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(store, "clear", fail_to_write)

    response = post_json(configured, "/api/data/clear", CONFIRM)

    assert response.status_code == 503


def test_a_clear_the_store_cannot_take_keeps_recomputing(
    configured: TestClient, state: UiState, store: Store, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(store, "clear", fail_to_write)

    post_json(configured, "/api/data/clear", CONFIRM)

    assert state.coordinator is not None


def test_a_clear_deletes_the_checklists_in_subfolders(
    configured: TestClient, data_dir: Path
) -> None:
    subfolder = data_dir / CHECKLISTS_FOLDER / "team"
    subfolder.mkdir()
    (subfolder / "oauth_consent.yaml").write_text("kept?", encoding="utf-8")

    post_json(configured, "/api/data/clear", CONFIRM)

    assert not (subfolder / "oauth_consent.yaml").exists()


def test_a_clear_removes_the_emptied_subfolders(configured: TestClient, data_dir: Path) -> None:
    subfolder = data_dir / CHECKLISTS_FOLDER / "team" / "travel"
    subfolder.mkdir(parents=True)
    (subfolder / "impossible_travel.yaml").write_text("kept?", encoding="utf-8")

    post_json(configured, "/api/data/clear", CONFIRM)

    assert list((data_dir / CHECKLISTS_FOLDER).iterdir()) == []


@needs_symlinks
def test_a_clear_keeps_the_files_of_a_linked_subfolder(
    configured: TestClient, tmp_path: Path, data_dir: Path
) -> None:
    elsewhere = tmp_path / "elsewhere"
    elsewhere.mkdir()
    (elsewhere / "kept.yaml").write_text("kept", encoding="utf-8")
    (data_dir / CHECKLISTS_FOLDER / "linked").symlink_to(elsewhere, target_is_directory=True)

    post_json(configured, "/api/data/clear", CONFIRM)

    assert (elsewhere / "kept.yaml").is_file()


@needs_symlinks
def test_a_clear_removes_the_link_to_a_linked_subfolder(
    configured: TestClient, tmp_path: Path, data_dir: Path
) -> None:
    elsewhere = tmp_path / "elsewhere"
    elsewhere.mkdir()
    (data_dir / CHECKLISTS_FOLDER / "linked").symlink_to(elsewhere, target_is_directory=True)

    post_json(configured, "/api/data/clear", CONFIRM)

    assert list((data_dir / CHECKLISTS_FOLDER).iterdir()) == []


# Uploads and clears take turns


@pytest.fixture
def slow_trace_load(monkeypatch: pytest.MonkeyPatch) -> tuple[threading.Event, threading.Event]:
    """Hold the first trace file's parse until `release` is set; `entered` says it began."""
    entered = threading.Event()
    release = threading.Event()

    def load_slowly(*args: object, **kwargs: object) -> object:
        if not entered.is_set():
            entered.set()
            release.wait(timeout=10)
        return load_spans(*args, **kwargs)  # pyright: ignore[reportArgumentType]

    monkeypatch.setattr(ui_uploads, "load_spans", load_slowly)
    return entered, release


def test_a_clear_during_a_trace_upload_waits_for_it(
    client: TestClient, store: Store, slow_trace_load: tuple[threading.Event, threading.Event]
) -> None:
    entered, release = slow_trace_load
    with ThreadPoolExecutor(2) as pool:
        uploading = pool.submit(upload_file, client, "traces", SECOND_TRACES)
        entered.wait(timeout=10)
        clearing = pool.submit(post_json, client, "/api/data/clear", CONFIRM)
        wait([clearing], timeout=OVERLAP_SECONDS)
        release.set()
        uploading.result()
        clearing.result()

    assert (store.read_counts().span_count, store.read_trace_family()) == (0, None)


def test_an_upload_of_the_other_format_during_a_trace_upload_is_refused(
    client: TestClient, slow_trace_load: tuple[threading.Event, threading.Event]
) -> None:
    entered, release = slow_trace_load
    with ThreadPoolExecutor(2) as pool:
        uploading = pool.submit(upload_file, client, "traces", DEMO_TRACES)
        entered.wait(timeout=10)
        other = pool.submit(upload_file, client, "traces", LANGFUSE_TRACES)
        wait([other], timeout=OVERLAP_SECONDS)
        release.set()
        uploading.result()

    assert other.result().status_code == 422
