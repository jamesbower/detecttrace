"""Uploading files and confirming the configuration in `detecttrace ui` gives `check`'s results.

For the demo and each golden fixture the app's form can express, every trace file, the
verdict CSV and every checklist is uploaded through the app's routes, in the loader's order.
The configuration is then confirmed with a label choice for each label the proposal leaves
unmapped, mapped as the fixture's detecttrace.yaml maps it, and a field edit for each mapping
field the proposal sets otherwise. The worker runs once, and its results are compared with
`run_check` on the fixture's configuration.

The only differences allowed, each asserted by its own test or listed below with its reason:

- `source` names the uploads ("uploaded traces", "uploaded verdicts") instead of files, and
  the app adds a `served` block, which holds no case back.
- Data notes are compared by kind and count. Their severity, message and hint are the same
  in every fixture, and so are their example subjects: an upload names its file by its base
  name, as `check` names a file in the trace folder. Only an example's detail may differ: the
  store keeps issues ordered by (kind, subject, detail), so its example is the smallest
  detail where `check` gives the first one met (formats/langfuse's repeated spans).
- NOTE_KIND_CHANGES: notes about a trace file that the upload reports on its card instead.
- SKIPPED_FIXTURES: configurations the form can't produce, or traces the app refuses; a
  test proves the list is exactly the fixtures the form can't express.
"""

from collections.abc import Iterator, Mapping
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import pytest
from fastapi.testclient import TestClient

from detecttrace.pipeline import run_check
from detecttrace.runconfig import RunConfig, UiConfig, load_run_config, load_ui_config
from detecttrace.serve.store import Store
from detecttrace.serve.ui import UiState, create_ui_app
from detecttrace.serve.ui_config import CONFIG_NAME
from detecttrace.serve.ui_recompute import UiRecompute
from detecttrace.serve.ui_server import DATABASE_NAME
from detecttrace.serve.ui_uploads import CHECKLIST_SUFFIXES
from detecttrace.verdicts import read_verdicts
from serve.test_parity import (
    DEMO_FOLDER,
    FIXTURE_FOLDERS,
    FIXTURE_ROOT,
    NEEDS_ZSTD,
    REFUSED_FIXTURES,
    as_mapping,
    capture_error,
    change_note_kinds,
    find_quoted,
    find_tool_results,
    normalize,
    read_trace_documents,
    to_name,
)
from serve.ui_support import ORIGIN, PORT, UPLOAD_HEADERS, FakeClock, InlineExecutor, post_json

SKIPPED_FIXTURES = {
    "formats/console_exporter": (
        "console exporter output is not a trace format the app reads; the upload is refused, "
        "so there are no results to compare"
    ),
    "edge/attributes/different_versions_on_descendants": (
        "prompt_version_lookup: descendant; the form edits only attribute keys"
    ),
    "edge/attributes/version_on_child_span_descendant_lookup": (
        "prompt_version_lookup: descendant; the form edits only attribute keys"
    ),
    "edge/attributes/empty_string": (
        "every agent verdict is an empty string, so the proposal counts the verdict field as "
        "missing and saving is refused until it is found, where check takes the key as given"
    ),
}
# Old kind -> new kind, or None when the upload reports it on its card instead of a note.
NOTE_KIND_CHANGES: dict[str, dict[str, str | None]] = {
    # The upload refuses an empty file, saying it is empty; nothing is stored from it.
    "edge/broken_files/empty_file": {"empty_file": None},
    # The store drops an identical copy of a span; the upload's text counts it as dropped.
    "edge/loading/same_span_in_two_files": {"duplicate_span": None},
}
# What the demo's analysts wrote that the proposal can't place: Benign, FP and TP it maps.
DEMO_LABEL_CHOICES = {"Closed - Benign": "benign", "Malicious": "true_positive"}
EMPTY_FILE_FOLDER = FIXTURE_ROOT / "edge" / "broken_files" / "empty_file"
REPEATED_SPAN_FOLDER = FIXTURE_ROOT / "edge" / "loading" / "same_span_in_two_files"
MISSING_COLUMN_FOLDER = FIXTURE_ROOT / "verdicts" / "missing_required_column"
EMPTY_FILE_REFUSAL = (
    "traces-2026-08-04T10-00-03.000.jsonl is not a trace file we can read. 1 trace file is "
    "empty. Check that the exporter writes to the trace folder, or remove empty files."
)
CONFIG_FILE = "detecttrace.yaml"
UPLOADED_SOURCE = {"traces": "uploaded traces", "verdicts": "uploaded verdicts"}


@dataclass(frozen=True)
class Confirmed:
    """What uploading a fixture and confirming its configuration gave."""

    run_config: RunConfig  # the fixture's configuration
    analyst_labels: list[str]  # the analysts' labels in the input, as the proposal lists them
    agent_labels: list[str]  # the agent's labels that no analyst uses
    saved: UiConfig | None  # the configuration the app saved; None when it saved none
    labels: dict[str, dict[str, object]]  # the label choices sent with the confirmation
    results: dict[str, object]  # the snapshot's results; empty when there is none
    upload_replies: list[dict[str, object]]  # every upload's reply body, in upload order


@dataclass(frozen=True)
class Parity:
    confirmed: Confirmed
    expected: dict[str, object]  # check's results, with the allowed differences applied
    tool_results: list[str]  # every gen_ai.tool.call.result value in the uploaded traces


def to_param(folder: Path) -> object:
    name = to_name(folder)
    marks = [NEEDS_ZSTD] if "zstd" in name else []
    if name in SKIPPED_FIXTURES:
        marks.append(pytest.mark.skip(reason=SKIPPED_FIXTURES[name]))
    return pytest.param(folder, id=name, marks=marks)


CANDIDATES = [
    folder for folder in [DEMO_FOLDER, *FIXTURE_FOLDERS] if to_name(folder) not in REFUSED_FIXTURES
]
COMPARED = [to_param(folder) for folder in CANDIDATES]


@pytest.fixture(scope="module", params=COMPARED)
def parity(request: pytest.FixtureRequest, tmp_path_factory: pytest.TempPathFactory) -> Parity:
    folder: Path = request.param
    confirmed = upload_and_confirm(folder, tmp_path_factory.mktemp("ui-parity"))
    check = run_check(confirmed.run_config, folder / CONFIG_FILE).results
    expected = change_note_kinds(check, NOTE_KIND_CHANGES.get(to_name(folder), {}))
    documents = read_trace_documents(confirmed.run_config.traces.path)
    tool_results = sorted({value for text in documents for value in find_tool_results(text)})
    return Parity(confirmed, expected, tool_results)


def test_ui_results_match_check(parity: Parity) -> None:
    assert normalize(parity.confirmed.results) == normalize(parity.expected)


def test_ui_results_match_check_notes_apart_from_their_examples(parity: Parity) -> None:
    assert to_note_texts(parity.confirmed.results) == to_note_texts(parity.expected)


def test_ui_source_names_the_uploads(parity: Parity) -> None:
    assert parity.confirmed.results["source"] == {
        **as_mapping(parity.expected["source"]),
        **UPLOADED_SOURCE,
    }


def test_ui_results_hold_no_case_back(parity: Parity) -> None:
    assert as_mapping(parity.confirmed.results["served"])["held_back_cases"] == 0


def test_ui_results_hold_no_tool_result(parity: Parity) -> None:
    assert find_quoted(parity.tool_results, parity.confirmed.results) == []


def test_the_saved_configuration_reads_every_field_as_the_fixtures_does(parity: Parity) -> None:
    saved = parity.confirmed.saved
    assert saved is not None and saved.mapping == parity.confirmed.run_config.mapping


def test_the_demo_needs_a_choice_for_two_labels(tmp_path: Path) -> None:
    confirmed = upload_and_confirm(DEMO_FOLDER, tmp_path)

    assert confirmed.labels == {"label_map": DEMO_LABEL_CHOICES, "agent_label_map": {}}


def test_skipped_fixtures_are_those_the_form_cannot_express(
    tmp_path_factory: pytest.TempPathFactory,
) -> None:
    inexpressible = [
        to_name(folder)
        for folder in CANDIDATES
        if not is_expressible(upload_and_confirm(folder, tmp_path_factory.mktemp("express")))
    ]
    assert sorted(inexpressible) == sorted(SKIPPED_FIXTURES)


def test_an_empty_trace_file_is_refused_on_upload(tmp_path: Path) -> None:
    confirmed = upload_and_confirm(EMPTY_FILE_FOLDER, tmp_path)

    assert confirmed.upload_replies[0] == {"code": 3, "message": EMPTY_FILE_REFUSAL}


def test_a_repeated_span_is_counted_as_dropped_on_upload(tmp_path: Path) -> None:
    confirmed = upload_and_confirm(REPEATED_SPAN_FOLDER, tmp_path)

    assert confirmed.upload_replies[1]["stored_text"] == "1 span added; 1 duplicate dropped."


def test_a_verdict_file_missing_a_column_is_refused_with_checks_message(tmp_path: Path) -> None:
    run_config = load_run_config(MISSING_COLUMN_FOLDER / CONFIG_FILE)
    check_error = capture_error(lambda: read_verdicts(run_config.verdicts.path))
    with open_app(tmp_path) as (client, _):
        reply = upload(client, "verdicts", run_config.verdicts.path)

    assert reply == {
        "code": 3,
        "message": check_error.replace(str(run_config.verdicts.path), "verdicts.csv"),
    }


def upload_and_confirm(folder: Path, data_dir: Path) -> Confirmed:
    """Upload the fixture's files, confirm its configuration and run the worker once."""
    run_config = load_run_config(folder / CONFIG_FILE)
    with open_app(data_dir) as (client, state):
        replies = [upload(client, "traces", path) for path in list_files(run_config.traces.path)]
        replies.append(upload(client, "verdicts", run_config.verdicts.path))
        checklist_files = [] if run_config.checklists is None else list_files(run_config.checklists)
        replies.extend(
            upload(client, "checklists", path)
            for path in checklist_files
            if path.name.lower().endswith(CHECKLIST_SUFFIXES)
        )
        proposal = post_json(client, "/api/config/proposal", {"fields": {}, "labels": {}})
        if proposal.status_code != 200:
            return Confirmed(run_config, [], [], None, {}, {}, replies)
        edits = to_edits(run_config, proposal.json())
        is_saved = post_json(client, "/api/config", edits).status_code == 200
        # One tick starts the run, which the inline executor finishes; the next saves it.
        state.tick()
        state.tick()
        results = client.get("/api/results.json")
    return Confirmed(
        run_config,
        list_labels(proposal.json(), "label_map", "unmapped_analyst_labels"),
        list_labels(proposal.json(), "agent_label_map", "unmapped_agent_labels"),
        load_ui_config(data_dir / CONFIG_NAME) if is_saved else None,
        edits["labels"],
        results.json() if results.status_code == 200 else {},
        replies,
    )


def is_expressible(confirmed: Confirmed) -> bool:
    """Whether the app saved a configuration that reads the input as the fixture's does."""
    saved = confirmed.saved
    return (
        saved is not None
        and saved.mapping == confirmed.run_config.mapping
        and not find_label_differences(confirmed, saved)
    )


@contextmanager
def open_app(data_dir: Path) -> Iterator[tuple[TestClient, UiState]]:
    """The app on a fresh store in `data_dir`, its recompute run in the caller's thread."""
    store = Store.open(data_dir / DATABASE_NAME)
    executor = InlineExecutor()
    recompute = UiRecompute(data_dir / DATABASE_NAME, lambda: executor)
    state = UiState(store=store, data_dir=data_dir, recompute=recompute, clock=FakeClock())
    try:
        with TestClient(create_ui_app(port=PORT, state=state), base_url=ORIGIN) as client:
            yield client, state
    finally:
        store.close()


def upload(client: TestClient, kind: str, path: Path) -> dict[str, object]:
    response = client.post(
        f"/api/upload/{kind}",
        params={"name": path.name},
        content=path.read_bytes(),
        headers=UPLOAD_HEADERS,
    )
    return response.json()


def list_files(path: Path) -> list[Path]:
    """The files at `path` in the loader's order: the file itself, or a folder's in POSIX order."""
    if path.is_file():
        return [path]
    return sorted(
        (file for file in path.rglob("*") if file.is_file() and not file.name.startswith(".")),
        key=lambda file: file.relative_to(path).as_posix(),
    )


def to_edits(run_config: RunConfig, proposal: Mapping[str, object]) -> dict[str, Any]:
    """The form's edits that give the fixture's configuration, from the proposal shown.

    A field the proposal sets otherwise gets the fixture's key; a label it leaves unmapped
    gets the fixture's verdict, when the fixture maps it.
    """
    mapping = run_config.mapping.model_dump()
    fields = {
        field["name"]: mapping[field["name"]]
        for field in as_list(proposal["fields"])
        if field["value"] != mapping[field["name"]]
    }
    analyst = {
        label: verdict.value
        for label in as_list(proposal["unmapped_analyst_labels"])
        if (verdict := run_config.to_analyst_verdict(label)) is not None
    }
    agent = {
        label: verdict.value
        for label in as_list(proposal["unmapped_agent_labels"])
        if (verdict := run_config.to_agent_verdict(label)) is not None
    }
    return {"fields": fields, "labels": {"label_map": analyst, "agent_label_map": agent}}


def list_labels(proposal: Mapping[str, object], mapped: str, unmapped: str) -> list[str]:
    """The labels of one kind in the input: the proposal maps each or lists it unmapped."""
    return [*as_mapping(proposal[mapped]), *as_list(proposal[unmapped])]


def find_label_differences(confirmed: Confirmed, saved: UiConfig) -> list[str]:
    """The labels in the input that the saved configuration reads otherwise than the fixture."""
    fixture = confirmed.run_config
    # An analyst label can be the agent's too, and the agent's labels fall back to label_map.
    return [
        label
        for label in confirmed.analyst_labels
        if fixture.to_analyst_verdict(label) != saved.to_analyst_verdict(label)
    ] + [
        label
        for label in [*confirmed.analyst_labels, *confirmed.agent_labels]
        if fixture.to_agent_verdict(label) != saved.to_agent_verdict(label)
    ]


def to_note_texts(results: Mapping[str, object]) -> list[list[object]]:
    notes = results["data_notes"]
    assert isinstance(notes, list)
    return [
        [note["severity"], note["kind"], note["count"], note["message"], note["hint"]]
        for note in notes
    ]


def as_list(value: object) -> list[Any]:
    assert isinstance(value, list)
    return value
