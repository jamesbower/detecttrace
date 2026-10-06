from collections.abc import Callable, Iterator
from concurrent.futures import Future
from pathlib import Path

import httpx
import pytest
from fastapi.testclient import TestClient

from detecttrace.dashboard_view import NoteView
from detecttrace.serve.recompute import RecomputeCoordinator, RecomputeOutcome
from detecttrace.serve.store import Snapshot, Store
from detecttrace.serve.ui import UiState, create_ui_app
from detecttrace.serve.ui_config import CHECKLISTS_FOLDER, CONFIG_NAME

PORT = 8765
ORIGIN = f"http://127.0.0.1:{PORT}"
UPLOAD_HEADERS = {
    "X-DetectTrace": "1",
    "Origin": ORIGIN,
    "Content-Type": "application/octet-stream",
}
DEMO_DATA = Path(__file__).parents[2] / "src" / "detecttrace" / "demo_data"
FIXTURES = Path(__file__).parent.parent / "fixtures"
DEMO_TRACES = DEMO_DATA / "traces" / "traces.jsonl.gz"
DEMO_VERDICTS = DEMO_DATA / "verdicts.csv"
DEMO_CHECKLIST = DEMO_DATA / "checklists" / "impossible_travel.yaml"
LANGFUSE_TRACES = FIXTURES / "formats" / "langfuse" / "traces" / "api_page.json"
TRUNCATED_TRACES = (
    FIXTURES / "edge" / "broken_files" / "truncated_last_line" / "traces" / "traces.jsonl"
)
NOTE_VIEW_KEYS = set(NoteView.__dataclass_fields__)
# Each route with the demo file it takes and the text a fresh data folder answers with.
DEMO_UPLOADS = [
    pytest.param("traces", DEMO_TRACES, "584 spans added.", id="traces"),
    pytest.param("verdicts", DEMO_VERDICTS, "201 verdicts added.", id="verdicts"),
    pytest.param(
        "checklists",
        DEMO_CHECKLIST,
        "Checklist saved for alert class 'impossible_travel'.",
        id="checklists",
    ),
]
DEBOUNCE = 5.0
Upload = Callable[..., httpx.Response]
CURRENT_RESULTS = '{"served": {"held_back_cases": 0}}'


class FakeClock:
    def __init__(self) -> None:
        self.now = 1_000.0

    def __call__(self) -> float:
        return self.now


def submit_nothing() -> Future[RecomputeOutcome]:
    """A recompute that never finishes, so the coordinator reports a run in progress."""
    return Future()


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
def state(store: Store, data_dir: Path) -> UiState:
    return UiState(store=store, data_dir=data_dir)


@pytest.fixture
def client(state: UiState) -> Iterator[TestClient]:
    with TestClient(create_ui_app(port=PORT, state=state), base_url=ORIGIN) as test_client:
        yield test_client


@pytest.fixture
def upload(client: TestClient) -> Upload:
    def send(kind: str, path: Path, name: str | None = None) -> httpx.Response:
        return client.post(
            f"/api/upload/{kind}",
            params={"name": name or path.name},
            content=path.read_bytes(),
            headers=UPLOAD_HEADERS,
        )

    return send


# Storing


@pytest.mark.parametrize(("kind", "path", "stored_text"), DEMO_UPLOADS)
def test_an_upload_answers_with_what_it_stored(
    upload: Upload, kind: str, path: Path, stored_text: str
) -> None:
    response = upload(kind, path)

    assert response.json() == {"stored_text": stored_text, "problems": []}


def test_uploaded_traces_are_stored(upload: Upload, store: Store) -> None:
    upload("traces", DEMO_TRACES)

    assert store.read_counts().span_count == 584


def test_uploaded_verdicts_are_stored(upload: Upload, store: Store) -> None:
    upload("verdicts", DEMO_VERDICTS)

    assert store.read_counts().verdict_count == 201


def test_an_uploaded_checklist_is_saved_in_the_checklists_folder(
    upload: Upload, data_dir: Path
) -> None:
    upload("checklists", DEMO_CHECKLIST)

    assert [path.name for path in (data_dir / CHECKLISTS_FOLDER).iterdir()] == [
        "impossible_travel.yaml"
    ]


def test_problems_have_the_shape_of_the_view_notes(upload: Upload) -> None:
    response = upload("traces", TRUNCATED_TRACES)

    assert [set(note) for note in response.json()["problems"]] == [NOTE_VIEW_KEYS]


def test_a_problem_reads_as_the_view_note_does(upload: Upload) -> None:
    response = upload("traces", TRUNCATED_TRACES)

    assert response.json()["problems"][0]["count_text"] == "1"


# Refusals


def test_a_refused_upload_answers_422(upload: Upload) -> None:
    upload("traces", DEMO_TRACES)

    response = upload("traces", LANGFUSE_TRACES)

    assert response.status_code == 422


def test_a_refused_upload_says_why(upload: Upload) -> None:
    upload("traces", DEMO_TRACES)

    response = upload("traces", LANGFUSE_TRACES)

    assert response.json() == {
        "code": 3,
        "message": "This data folder holds OTLP traces. Clear the data to switch to Langfuse.",
    }


def test_a_refused_upload_leaves_the_store_unchanged(upload: Upload, store: Store) -> None:
    upload("traces", DEMO_TRACES)
    before = store.read_counts()

    upload("traces", LANGFUSE_TRACES)

    assert store.read_counts() == before


def test_a_bad_checklist_is_refused(upload: Upload, tmp_path: Path) -> None:
    bad = tmp_path / "bad.yaml"
    bad.write_text("alert_class: impossible_travel\n", encoding="utf-8")

    response = upload("checklists", bad)

    assert response.status_code == 422


def test_a_bad_checklist_saves_nothing(upload: Upload, tmp_path: Path, data_dir: Path) -> None:
    bad = tmp_path / "bad.yaml"
    bad.write_text("alert_class: impossible_travel\n", encoding="utf-8")

    upload("checklists", bad)

    assert list((data_dir / CHECKLISTS_FOLDER).glob("*")) == []


@pytest.mark.parametrize(("kind", "path", "stored_text"), DEMO_UPLOADS)
def test_a_name_with_folders_writes_nothing_outside_the_data_folder(
    upload: Upload, tmp_path: Path, kind: str, path: Path, stored_text: str
) -> None:
    upload(kind, path, name=f"../../{path.name}")

    assert {entry.relative_to(tmp_path).parts[0] for entry in tmp_path.rglob("*")} == {"data"}


def test_a_name_with_folders_is_stored_under_its_base_name(
    upload: Upload, tmp_path: Path, store: Store
) -> None:
    upload("traces", TRUNCATED_TRACES, name="../../x.jsonl")

    assert {stored.issue.subject for stored in store.read_inputs().issues} == {"x.jsonl"}


def test_an_upload_without_a_name_answers_400(client: TestClient) -> None:
    response = client.post("/api/upload/verdicts", content=b"", headers=UPLOAD_HEADERS)

    assert response.status_code == 400


@pytest.mark.parametrize(
    ("kind", "name"),
    [
        pytest.param("traces", "traces.csv", id="traces"),
        pytest.param("verdicts", "verdicts.yaml", id="verdicts"),
        pytest.param("checklists", "travel.jsonl", id="checklists"),
    ],
)
def test_an_upload_with_the_wrong_suffix_answers_422(
    client: TestClient, kind: str, name: str
) -> None:
    response = client.post(
        f"/api/upload/{kind}", params={"name": name}, content=b"x", headers=UPLOAD_HEADERS
    )

    assert response.status_code == 422


@pytest.mark.parametrize("kind", ["traces", "verdicts", "checklists"])
def test_an_upload_of_another_media_type_answers_415(client: TestClient, kind: str) -> None:
    response = client.post(
        f"/api/upload/{kind}",
        params={"name": "x.csv"},
        content=b"x",
        headers={**UPLOAD_HEADERS, "Content-Type": "text/csv"},
    )

    assert response.status_code == 415


def test_an_upload_without_the_write_header_answers_403(client: TestClient) -> None:
    response = client.post(
        "/api/upload/verdicts",
        params={"name": "verdicts.csv"},
        content=DEMO_VERDICTS.read_bytes(),
        headers={"Origin": ORIGIN, "Content-Type": "application/octet-stream"},
    )

    assert response.status_code == 403


# The recompute


@pytest.mark.parametrize(("kind", "path", "stored_text"), DEMO_UPLOADS)
def test_an_upload_that_stores_something_starts_a_recompute(
    upload: Upload,
    state: UiState,
    store: Store,
    kind: str,
    path: Path,
    stored_text: str,
) -> None:
    store.write_snapshot(Snapshot(store.generation(), 1, "<p>old</p>", CURRENT_RESULTS))
    clock = FakeClock()
    coordinator = RecomputeCoordinator(submit_nothing, store, clock, DEBOUNCE)
    state.coordinator = coordinator
    upload(kind, path)
    clock.now += DEBOUNCE

    coordinator.tick()

    assert coordinator.status.is_running


# The state route


def read_state(client: TestClient) -> dict[str, object]:
    return client.get("/api/ui/state").json()


def test_the_state_of_a_new_data_folder(client: TestClient) -> None:
    assert read_state(client) == {
        "is_configured": False,
        "can_configure": False,
        "has_results": False,
        "span_count_text": "0 spans stored.",
        "verdict_count_text": "0 verdicts stored.",
        "trace_family_text": None,
        "checklist_classes": [],
        "checklist_error_text": None,
    }


def test_the_state_after_traces_and_verdicts(client: TestClient, upload: Upload) -> None:
    upload("traces", DEMO_TRACES)
    upload("traces", DEMO_DATA / "traces" / "traces-2026-08-18T00-57-52.000.jsonl.gz")
    upload("traces", DEMO_DATA / "traces" / "traces-2026-09-03T09-30-00.000.jsonl.gz")
    upload("verdicts", DEMO_VERDICTS)

    assert read_state(client) == {
        "is_configured": False,
        "can_configure": True,
        "has_results": False,
        "span_count_text": "2,129 spans stored.",
        "verdict_count_text": "201 verdicts stored.",
        "trace_family_text": "OTLP traces",
        "checklist_classes": [],
        "checklist_error_text": None,
    }


def test_traces_alone_cannot_be_configured(client: TestClient, upload: Upload) -> None:
    upload("traces", DEMO_TRACES)

    assert read_state(client)["can_configure"] is False


def test_the_state_names_langfuse_traces(client: TestClient, upload: Upload) -> None:
    upload("traces", LANGFUSE_TRACES)

    assert read_state(client)["trace_family_text"] == "Langfuse traces"


def test_the_state_counts_one_verdict_in_the_singular(
    client: TestClient, upload: Upload, tmp_path: Path
) -> None:
    one = tmp_path / "one.csv"
    one.write_text("case_id,alert_class,verdict\nDT-1,impossible_travel,TP\n", encoding="utf-8")
    upload("verdicts", one)

    assert read_state(client)["verdict_count_text"] == "1 verdict stored."


def test_the_state_lists_the_saved_checklists(client: TestClient, upload: Upload) -> None:
    upload("checklists", DEMO_DATA / "checklists" / "oauth_consent.yaml")
    upload("checklists", DEMO_CHECKLIST)

    assert read_state(client)["checklist_classes"] == ["impossible_travel", "oauth_consent"]


def test_the_state_reports_an_unreadable_checklists_folder(
    client: TestClient, data_dir: Path
) -> None:
    folder = data_dir / CHECKLISTS_FOLDER
    folder.mkdir()
    (folder / "broken.yaml").write_text("items: [\n", encoding="utf-8")

    assert read_state(client)["checklist_error_text"] is not None


def test_the_state_is_configured_once_the_configuration_exists(
    client: TestClient, data_dir: Path
) -> None:
    (data_dir / CONFIG_NAME).write_text("{}\n", encoding="utf-8")

    assert read_state(client)["is_configured"] is True


def test_the_state_has_results_once_a_snapshot_exists(client: TestClient, store: Store) -> None:
    store.write_snapshot(Snapshot(0, 1, "<p>page</p>", CURRENT_RESULTS))

    assert read_state(client)["has_results"] is True
