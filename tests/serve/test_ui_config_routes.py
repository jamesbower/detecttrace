import sqlite3
from collections.abc import Iterator
from pathlib import Path

import pytest
from builders import agent_span
from fastapi.testclient import TestClient

from detecttrace.init_writer import FIELD_NAMES
from detecttrace.model import VerdictRow
from detecttrace.runconfig import load_ui_config
from detecttrace.serve.store import Store
from detecttrace.serve.ui import SAVED_TEXT, UiState, create_ui_app
from detecttrace.serve.ui_config import CHECKLISTS_FOLDER, CONFIG_NAME
from detecttrace.serve.ui_recompute import UiRecompute
from serve.ui_support import (
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

EDITED_KEY = "detecttrace.alert_class"
PROMPT_EDIT = {"fields": {"prompt_version": EDITED_KEY}, "labels": {}}
MALICIOUS_BENIGN = {"label_map": {"Malicious": "benign"}}
CONFIG_ROUTES = ["/api/config/proposal", "/api/config"]
BAD_EDITS = [
    pytest.param({"fields": {"case": "x.case"}, "labels": {}}, id="unknown field"),
    pytest.param({"fields": {"case_id": " "}, "labels": {}}, id="empty key"),
    pytest.param({"fields": {}, "labels": {"labels": {}}}, id="unknown label map"),
    pytest.param({"fields": {}, "labels": {"label_map": {"TP": "maybe"}}}, id="unknown verdict"),
    pytest.param({"fields": {}}, id="no labels"),
    pytest.param({**NO_EDITS, "sets": []}, id="extra key"),
    pytest.param({"fields": {"case_id": 1}, "labels": {}}, id="key not text"),
    pytest.param([], id="not an object"),
]


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
def state(store: Store, data_dir: Path, executor: InlineExecutor) -> UiState:
    recompute = UiRecompute(data_dir / "detecttrace.db", lambda: executor)
    return UiState(store=store, data_dir=data_dir, recompute=recompute, clock=FakeClock())


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
def no_case_id(client: TestClient, store: Store) -> TestClient:
    store.add_spans([agent_span("a1")], [])
    store.put_verdicts([VerdictRow("DT-1", "impossible_travel", "TP", 2)], "test")
    store.set_trace_family("otlp")
    return client


def run_recompute(state: UiState) -> None:
    """One tick starts the run, which the inline executor finishes; the next saves it."""
    state.tick()
    state.tick()


def save_and_recompute(client: TestClient, state: UiState, body: object) -> int:
    post_json(client, "/api/config", body).raise_for_status()
    run_recompute(state)
    return client.get("/api/status").json()["generation"]


def write_broken_checklist(data_dir: Path) -> None:
    folder = data_dir / CHECKLISTS_FOLDER
    folder.mkdir()
    (folder / "broken.yaml").write_text("items: [\n", encoding="utf-8")


# The proposal


def test_the_proposal_waits_for_verdicts(client: TestClient) -> None:
    upload_file(client, "traces", DEMO_TRACES)

    response = post_json(client, "/api/config/proposal", NO_EDITS)

    assert response.status_code == 409


def test_the_proposal_before_verdicts_says_what_to_upload(client: TestClient) -> None:
    upload_file(client, "traces", DEMO_TRACES)

    response = post_json(client, "/api/config/proposal", NO_EDITS)

    assert response.json() == {"code": 9, "message": "Upload traces and verdicts first."}


def test_the_proposal_lists_every_field(uploaded: TestClient) -> None:
    response = post_json(uploaded, "/api/config/proposal", NO_EDITS)

    assert [field["name"] for field in response.json()["fields"]] == list(FIELD_NAMES)


def test_the_proposal_applies_a_field_edit(uploaded: TestClient) -> None:
    response = post_json(uploaded, "/api/config/proposal", PROMPT_EDIT)

    fields = {field["name"]: field["value"] for field in response.json()["fields"]}
    assert fields["prompt_version"] == EDITED_KEY


def test_the_proposal_writes_nothing(uploaded: TestClient, data_dir: Path) -> None:
    post_json(uploaded, "/api/config/proposal", NO_EDITS)

    assert not (data_dir / CONFIG_NAME).exists()


def test_the_proposal_after_a_save_shows_the_saved_field(uploaded: TestClient) -> None:
    post_json(uploaded, "/api/config", PROMPT_EDIT)

    response = post_json(uploaded, "/api/config/proposal", NO_EDITS)

    fields = {field["name"]: field["value"] for field in response.json()["fields"]}
    assert fields["prompt_version"] == EDITED_KEY


def test_the_proposal_after_a_save_shows_the_saved_label(uploaded: TestClient) -> None:
    post_json(uploaded, "/api/config", {"fields": {}, "labels": MALICIOUS_BENIGN})

    response = post_json(uploaded, "/api/config/proposal", NO_EDITS)

    assert response.json()["label_map"]["Malicious"] == "benign"


def test_saving_again_without_edits_keeps_the_saved_field(
    uploaded: TestClient, data_dir: Path
) -> None:
    post_json(uploaded, "/api/config", PROMPT_EDIT)

    post_json(uploaded, "/api/config", NO_EDITS)

    assert load_ui_config(data_dir / CONFIG_NAME).mapping.prompt_version == EDITED_KEY


def test_saving_again_without_edits_keeps_the_saved_label(
    uploaded: TestClient, data_dir: Path
) -> None:
    post_json(uploaded, "/api/config", {"fields": {}, "labels": MALICIOUS_BENIGN})

    post_json(uploaded, "/api/config", NO_EDITS)

    assert load_ui_config(data_dir / CONFIG_NAME).label_map["malicious"] == "benign"


def test_a_broken_saved_configuration_is_refused(uploaded: TestClient, data_dir: Path) -> None:
    (data_dir / CONFIG_NAME).write_text("mapping: [\n", encoding="utf-8")

    response = post_json(uploaded, "/api/config/proposal", NO_EDITS)

    assert response.status_code == 422


def test_a_broken_saved_configuration_names_no_server_folder(
    uploaded: TestClient, data_dir: Path
) -> None:
    (data_dir / CONFIG_NAME).write_text("mapping: [\n", encoding="utf-8")

    response = post_json(uploaded, "/api/config/proposal", NO_EDITS)

    assert str(data_dir.absolute()) not in response.json()["message"]


# Bad requests, on both routes


@pytest.mark.parametrize("route", CONFIG_ROUTES)
@pytest.mark.parametrize("body", BAD_EDITS)
def test_a_bad_edit_is_refused(uploaded: TestClient, route: str, body: object) -> None:
    response = post_json(uploaded, route, body)

    assert response.status_code == 422


@pytest.mark.parametrize("route", CONFIG_ROUTES)
def test_a_body_that_is_not_json_is_refused(uploaded: TestClient, route: str) -> None:
    response = uploaded.post(
        route, content=b"{fields", headers={**WRITE_HEADERS, "Content-Type": "application/json"}
    )

    assert response.status_code == 422


@pytest.mark.parametrize("route", CONFIG_ROUTES)
def test_a_bad_edit_answers_with_a_status_body(uploaded: TestClient, route: str) -> None:
    response = post_json(uploaded, route, {"fields": {"case": "x"}, "labels": {}})

    assert set(response.json()) == {"code", "message"}


@pytest.mark.parametrize("route", CONFIG_ROUTES)
def test_a_body_that_is_not_json_typed_is_refused(uploaded: TestClient, route: str) -> None:
    response = uploaded.post(
        route, content=b"{}", headers={**WRITE_HEADERS, "Content-Type": "text/plain"}
    )

    assert response.status_code == 415


@pytest.mark.parametrize("route", CONFIG_ROUTES)
def test_a_request_without_the_write_header_is_refused(uploaded: TestClient, route: str) -> None:
    response = uploaded.post(route, json=NO_EDITS, headers={"Origin": ORIGIN})

    assert response.status_code == 403


# Saving


def test_saving_answers_with_the_saved_text(uploaded: TestClient) -> None:
    response = post_json(uploaded, "/api/config", NO_EDITS)

    assert response.json() == {"saved_text": SAVED_TEXT}


def test_saving_writes_the_configuration_file(uploaded: TestClient, data_dir: Path) -> None:
    post_json(uploaded, "/api/config", NO_EDITS)

    assert (data_dir / CONFIG_NAME).is_file()


def test_saving_writes_the_field_edit(uploaded: TestClient, data_dir: Path) -> None:
    post_json(uploaded, "/api/config", PROMPT_EDIT)

    assert EDITED_KEY in (data_dir / CONFIG_NAME).read_text(encoding="utf-8")


def test_saving_before_any_upload_is_refused(client: TestClient) -> None:
    response = post_json(client, "/api/config", NO_EDITS)

    assert response.status_code == 422


def test_saving_with_a_required_field_missing_is_refused(no_case_id: TestClient) -> None:
    response = post_json(no_case_id, "/api/config", NO_EDITS)

    assert response.status_code == 422


def test_saving_with_a_required_field_missing_writes_nothing(
    no_case_id: TestClient, data_dir: Path
) -> None:
    post_json(no_case_id, "/api/config", NO_EDITS)

    assert not (data_dir / CONFIG_NAME).exists()


def test_saving_with_a_broken_checklist_is_refused(uploaded: TestClient, data_dir: Path) -> None:
    write_broken_checklist(data_dir)

    response = post_json(uploaded, "/api/config", NO_EDITS)

    assert "broken.yaml" in response.json()["message"]


def test_saving_with_a_broken_checklist_leaves_no_file(
    uploaded: TestClient, data_dir: Path
) -> None:
    write_broken_checklist(data_dir)

    post_json(uploaded, "/api/config", NO_EDITS)

    assert not (data_dir / CONFIG_NAME).exists()


def test_saving_with_a_broken_checklist_keeps_the_previous_file(
    uploaded: TestClient, data_dir: Path
) -> None:
    post_json(uploaded, "/api/config", NO_EDITS)
    previous = (data_dir / CONFIG_NAME).read_text(encoding="utf-8")
    write_broken_checklist(data_dir)

    post_json(uploaded, "/api/config", PROMPT_EDIT)

    assert (data_dir / CONFIG_NAME).read_text(encoding="utf-8") == previous


def test_saving_with_a_broken_checklist_starts_no_recompute(
    uploaded: TestClient, state: UiState, data_dir: Path, executor: InlineExecutor
) -> None:
    write_broken_checklist(data_dir)
    post_json(uploaded, "/api/config", NO_EDITS)

    run_recompute(state)

    assert executor.count == 0


def fail_to_write() -> None:
    raise sqlite3.OperationalError("disk I/O error")


def test_a_save_the_store_cannot_record_answers_503(
    uploaded: TestClient, store: Store, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(store, "mark_changed", fail_to_write)

    response = post_json(uploaded, "/api/config", NO_EDITS)

    assert response.status_code == 503


def test_a_first_save_the_store_cannot_record_leaves_no_file(
    uploaded: TestClient, store: Store, data_dir: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(store, "mark_changed", fail_to_write)

    post_json(uploaded, "/api/config", NO_EDITS)

    assert not (data_dir / CONFIG_NAME).exists()


def test_a_save_the_store_cannot_record_keeps_the_previous_file(
    uploaded: TestClient, store: Store, data_dir: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    post_json(uploaded, "/api/config", NO_EDITS)
    previous = (data_dir / CONFIG_NAME).read_text(encoding="utf-8")
    monkeypatch.setattr(store, "mark_changed", fail_to_write)

    post_json(uploaded, "/api/config", PROMPT_EDIT)

    assert (data_dir / CONFIG_NAME).read_text(encoding="utf-8") == previous


# The recompute


def test_nothing_is_computed_before_the_first_save(
    uploaded: TestClient, state: UiState, executor: InlineExecutor
) -> None:
    run_recompute(state)

    assert executor.count == 0


def test_the_status_has_no_snapshot_before_the_first_save(
    uploaded: TestClient, state: UiState
) -> None:
    run_recompute(state)

    assert uploaded.get("/api/status").json()["generation"] == 0


def test_saving_computes_the_dashboard(uploaded: TestClient, state: UiState, store: Store) -> None:
    post_json(uploaded, "/api/config", NO_EDITS)

    run_recompute(state)

    assert store.read_snapshot() is not None


def test_saving_advances_the_status_generation(uploaded: TestClient, state: UiState) -> None:
    generation = save_and_recompute(uploaded, state, NO_EDITS)

    assert generation > 0


def test_the_computed_page_is_the_dashboard(uploaded: TestClient, state: UiState) -> None:
    save_and_recompute(uploaded, state, NO_EDITS)

    assert uploaded.get("/api/results.json").json()["source"]["traces"] == "uploaded traces"


def test_a_second_save_recomputes_without_an_upload(uploaded: TestClient, state: UiState) -> None:
    first = save_and_recompute(uploaded, state, NO_EDITS)

    second = save_and_recompute(uploaded, state, PROMPT_EDIT)

    assert second > first


def test_a_second_save_runs_the_worker_again(
    uploaded: TestClient, state: UiState, executor: InlineExecutor
) -> None:
    save_and_recompute(uploaded, state, NO_EDITS)

    save_and_recompute(uploaded, state, PROMPT_EDIT)

    assert executor.count == 2


def test_a_second_save_recomputes_with_the_new_settings(
    uploaded: TestClient, state: UiState, executor: InlineExecutor
) -> None:
    save_and_recompute(uploaded, state, NO_EDITS)

    save_and_recompute(uploaded, state, PROMPT_EDIT)

    _, settings, _ = executor.calls[-1]
    assert settings.config.mapping.prompt_version == EDITED_KEY
