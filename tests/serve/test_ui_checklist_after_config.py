import sqlite3
from collections.abc import Iterator
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from detecttrace.runconfig import load_ui_config
from detecttrace.serve.store import Store
from detecttrace.serve.ui import UiState, create_ui_app
from detecttrace.serve.ui_config import CHECKLISTS_FOLDER, CONFIG_NAME
from detecttrace.serve.ui_recompute import UiRecompute
from serve.ui_support import (
    DEMO_CHECKLIST,
    DEMO_TRACES,
    DEMO_VERDICTS,
    NO_EDITS,
    ORIGIN,
    PORT,
    FakeClock,
    InlineExecutor,
    post_json,
    upload_file,
)

DEMO_ITEM_IDS = [
    "signin_history",
    "signin_query",
    "mfa_check",
    "ip_reputation",
    "location_history",
]
REPLACEMENT_CHECKLIST = (
    "alert_class: impossible_travel\nitems:\n- id: mfa_only\n  tool: check_mfa_status\n"
)
CONFIRM = {"confirm": True}
# The coordinator's default wait after an upload, as for any other upload.
DEBOUNCE_SECONDS = 5.0


@pytest.fixture
def data_dir(tmp_path: Path) -> Path:
    folder = tmp_path / "data"
    # As run_ui leaves it: the checklists folder always exists.
    (folder / CHECKLISTS_FOLDER).mkdir(parents=True)
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
def uploaded(client: TestClient) -> TestClient:
    upload_file(client, "traces", DEMO_TRACES)
    upload_file(client, "verdicts", DEMO_VERDICTS)
    return client


@pytest.fixture
def configured(uploaded: TestClient, state: UiState, clock: FakeClock) -> TestClient:
    """A configuration confirmed before any checklist, with its dashboard computed."""
    post_json(uploaded, "/api/config", NO_EDITS).raise_for_status()
    run_recompute(state, clock)
    return uploaded


@pytest.fixture
def configured_with_checklist(uploaded: TestClient, state: UiState, clock: FakeClock) -> TestClient:
    upload_file(uploaded, "checklists", DEMO_CHECKLIST).raise_for_status()
    post_json(uploaded, "/api/config", NO_EDITS).raise_for_status()
    run_recompute(state, clock)
    return uploaded


@pytest.fixture
def replacement(tmp_path: Path) -> Path:
    path = tmp_path / "travel.yaml"
    path.write_text(REPLACEMENT_CHECKLIST, encoding="utf-8")
    return path


def run_recompute(state: UiState, clock: FakeClock) -> None:
    """Past the upload's wait, one tick starts the run, which the inline executor finishes;
    the next saves it."""
    clock.now += DEBOUNCE_SECONDS
    state.tick()
    state.tick()


def read_item_ids(client: TestClient, alert_class: str) -> list[str]:
    classes = client.get("/api/results.json").json()["classes"]
    by_class = {entry["alert_class"]: entry["checklist_item_ids"] for entry in classes}
    return by_class[alert_class]


def write_broken_checklist(data_dir: Path) -> None:
    (data_dir / CHECKLISTS_FOLDER / "broken.yaml").write_text("items: [\n", encoding="utf-8")


# A checklist uploaded after the configuration is confirmed


def test_a_checklist_uploaded_after_confirming_is_used(
    configured: TestClient, state: UiState, clock: FakeClock
) -> None:
    upload_file(configured, "checklists", DEMO_CHECKLIST)

    run_recompute(state, clock)

    assert read_item_ids(configured, "impossible_travel") == DEMO_ITEM_IDS


def test_a_checklist_replaced_after_confirming_is_used(
    configured_with_checklist: TestClient, state: UiState, clock: FakeClock, replacement: Path
) -> None:
    upload_file(configured_with_checklist, "checklists", replacement)

    run_recompute(state, clock)

    assert read_item_ids(configured_with_checklist, "impossible_travel") == ["mfa_only"]


def test_a_checklist_uploaded_before_confirming_is_used(
    configured_with_checklist: TestClient,
) -> None:
    assert read_item_ids(configured_with_checklist, "impossible_travel") == DEMO_ITEM_IDS


def test_a_checklist_uploaded_after_a_clear_and_a_new_configuration_is_used(
    configured_with_checklist: TestClient, state: UiState, clock: FakeClock
) -> None:
    post_json(configured_with_checklist, "/api/data/clear", CONFIRM).raise_for_status()
    upload_file(configured_with_checklist, "traces", DEMO_TRACES)
    upload_file(configured_with_checklist, "verdicts", DEMO_VERDICTS)
    post_json(configured_with_checklist, "/api/config", NO_EDITS).raise_for_status()
    run_recompute(state, clock)
    upload_file(configured_with_checklist, "checklists", DEMO_CHECKLIST)

    run_recompute(state, clock)

    assert read_item_ids(configured_with_checklist, "impossible_travel") == DEMO_ITEM_IDS


def test_the_saved_file_names_the_checklists_folder(configured: TestClient, data_dir: Path) -> None:
    config = load_ui_config(data_dir / CONFIG_NAME)

    assert config.checklists == data_dir.absolute() / CHECKLISTS_FOLDER


# A checklist the folder can't load with


def test_a_checklist_the_folder_cannot_load_with_is_refused(
    configured: TestClient, data_dir: Path
) -> None:
    write_broken_checklist(data_dir)

    response = upload_file(configured, "checklists", DEMO_CHECKLIST)

    assert response.status_code == 422


def test_a_refused_checklist_answers_with_the_loader_message(
    configured: TestClient, data_dir: Path
) -> None:
    write_broken_checklist(data_dir)

    response = upload_file(configured, "checklists", DEMO_CHECKLIST)

    assert "broken.yaml" in response.json()["message"]


def test_a_refused_new_checklist_is_not_kept(configured: TestClient, data_dir: Path) -> None:
    write_broken_checklist(data_dir)

    upload_file(configured, "checklists", DEMO_CHECKLIST)

    assert sorted(path.name for path in (data_dir / CHECKLISTS_FOLDER).iterdir()) == ["broken.yaml"]


def test_a_refused_replacement_keeps_the_previous_checklist(
    configured_with_checklist: TestClient, data_dir: Path, replacement: Path
) -> None:
    write_broken_checklist(data_dir)

    upload_file(configured_with_checklist, "checklists", replacement)

    saved = data_dir / CHECKLISTS_FOLDER / "impossible_travel.yaml"
    assert saved.read_bytes() == DEMO_CHECKLIST.read_bytes()


def test_a_refused_checklist_keeps_the_running_settings(
    configured: TestClient,
    state: UiState,
    clock: FakeClock,
    data_dir: Path,
    executor: InlineExecutor,
) -> None:
    write_broken_checklist(data_dir)
    upload_file(configured, "checklists", DEMO_CHECKLIST)

    run_recompute(state, clock)

    _, settings, _ = executor.calls[-1]
    assert settings.checklists == {}


def fail_to_write() -> None:
    raise sqlite3.OperationalError("disk I/O error")


def test_a_checklist_the_store_cannot_record_keeps_the_previous_checklist(
    configured_with_checklist: TestClient,
    store: Store,
    data_dir: Path,
    replacement: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(store, "mark_changed", fail_to_write)

    upload_file(configured_with_checklist, "checklists", replacement)

    saved = data_dir / CHECKLISTS_FOLDER / "impossible_travel.yaml"
    assert saved.read_bytes() == DEMO_CHECKLIST.read_bytes()
