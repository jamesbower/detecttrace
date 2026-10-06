import os
import shutil
import sys
import time
from collections.abc import Iterator
from pathlib import Path

import pytest
from builders import agent_span
from page_data import read_view

from detecttrace.checklist import ChecklistFileError
from detecttrace.init_proposal import propose_init
from detecttrace.model import Verdict, VerdictRow
from detecttrace.serve.recompute import compute_snapshot
from detecttrace.serve.store import Store
from detecttrace.serve.ui_config import (
    CHECKLISTS_FOLDER,
    CONFIG_NAME,
    UI_TRACES_SOURCE,
    UiConfigError,
    build_proposal_content,
    to_recompute_settings,
    write_ui_config,
)
from detecttrace.traces import load_spans
from detecttrace.verdicts import read_verdicts

DEMO_DIR = Path(__file__).parent.parent.parent / "src" / "detecttrace" / "demo_data"
EDITED_KEY = "detecttrace.alert_class"
needs_permissions = pytest.mark.skipif(
    sys.platform == "win32" or os.geteuid() == 0,
    reason="needs POSIX permissions that the current user cannot bypass",
)


@pytest.fixture
def data_dir(tmp_path: Path) -> Path:
    folder = tmp_path / "data"
    folder.mkdir()
    return folder


@pytest.fixture
def store(tmp_path: Path) -> Iterator[Store]:
    opened = Store.open(tmp_path / "detecttrace.db")
    yield opened
    opened.close()


@pytest.fixture
def demo_store(store: Store) -> Store:
    spans, span_issues = load_spans(DEMO_DIR / "traces")
    store.add_spans(spans, span_issues)
    verdict_rows, _ = read_verdicts(DEMO_DIR / "verdicts.csv")
    store.put_verdicts(verdict_rows, "test")
    store.set_trace_family("otlp")
    return store


@pytest.fixture
def no_case_id_store(store: Store) -> Store:
    store.add_spans([agent_span("a1")], [])
    store.put_verdicts([VerdictRow("DT-1", "impossible_travel", "TP", 2)], "test")
    store.set_trace_family("otlp")
    return store


def _field(content: dict[str, object], name: str) -> dict[str, object]:
    fields = content["fields"]
    assert isinstance(fields, list)
    return next(field for field in fields if field["name"] == name)


# The proposal


def test_the_proposal_finds_the_case_id_init_finds(demo_store: Store, data_dir: Path) -> None:
    spans, _ = load_spans(DEMO_DIR / "traces")
    verdict_rows, _ = read_verdicts(DEMO_DIR / "verdicts.csv")
    expected = propose_init(spans, "otlp_jsonl", verdict_rows).mapping.case_id.value

    content = build_proposal_content(demo_store, data_dir, {}, {})

    assert _field(content, "case_id")["value"] == expected


def test_the_proposal_suggests_the_agent_runs_attribute_keys(
    demo_store: Store, data_dir: Path
) -> None:
    content = build_proposal_content(demo_store, data_dir, {}, {})

    assert "detecttrace.case_id" in content["suggestions"]  # type: ignore[operator]


def test_an_edited_field_is_proposed_as_given(demo_store: Store, data_dir: Path) -> None:
    content = build_proposal_content(demo_store, data_dir, {"prompt_version": EDITED_KEY}, {})

    assert _field(content, "prompt_version")["value"] == EDITED_KEY


def test_an_edited_field_reads_set_by_you(demo_store: Store, data_dir: Path) -> None:
    content = build_proposal_content(demo_store, data_dir, {"prompt_version": EDITED_KEY}, {})

    assert "set by you" in str(_field(content, "prompt_version")["share_text"])


def test_the_proposal_counts_the_agent_runs(demo_store: Store, data_dir: Path) -> None:
    content = build_proposal_content(demo_store, data_dir, {}, {})

    assert content["agent_run_count_text"] == "201 agent runs found."


def test_a_complete_proposal_has_no_missing_text(demo_store: Store, data_dir: Path) -> None:
    content = build_proposal_content(demo_store, data_dir, {}, {})

    assert content["missing_text"] is None


def test_a_proposal_without_a_case_id_says_what_to_choose(
    no_case_id_store: Store, data_dir: Path
) -> None:
    content = build_proposal_content(no_case_id_store, data_dir, {}, {})

    assert content["missing_text"] == (
        "Before saving, choose attributes for Case ID and Agent verdict."
    )


def test_a_field_reads_as_a_summary_line(demo_store: Store, data_dir: Path) -> None:
    content = build_proposal_content(demo_store, data_dir, {}, {})

    assert _field(content, "case_id")["summary_text"] == "Case ID: detecttrace.case_id"


def test_a_missing_field_reads_as_not_set_in_its_summary_line(
    no_case_id_store: Store, data_dir: Path
) -> None:
    content = build_proposal_content(no_case_id_store, data_dir, {}, {})

    assert _field(content, "case_id")["summary_text"] == "Case ID: Not set"


def test_the_proposal_names_a_missing_value(demo_store: Store, data_dir: Path) -> None:
    content = build_proposal_content(demo_store, data_dir, {}, {})

    assert content["not_set_text"] == "Not set"


def test_the_proposal_names_a_label_left_unmapped(demo_store: Store, data_dir: Path) -> None:
    content = build_proposal_content(demo_store, data_dir, {}, {})

    assert content["not_mapped_text"] == "Not mapped"


def test_the_proposal_explains_the_label_step(demo_store: Store, data_dir: Path) -> None:
    content = build_proposal_content(demo_store, data_dir, {}, {})

    assert content["labels_help_text"] == (
        "Your verdict file uses labels DetectTrace doesn't recognise. Choose the verdict each "
        "one means; rows left unmapped are reported in the data notes and not scored."
    )


def test_the_proposal_names_each_verdict_choice(demo_store: Store, data_dir: Path) -> None:
    content = build_proposal_content(demo_store, data_dir, {}, {})

    assert content["verdict_choice_labels"] == [
        {"value": "true_positive", "label": "True positive"},
        {"value": "false_positive", "label": "False positive"},
        {"value": "benign", "label": "Benign"},
    ]


# The proposal after a save


def test_the_proposal_keeps_a_saved_field_edit(demo_store: Store, data_dir: Path) -> None:
    write_ui_config(demo_store, data_dir, {"prompt_version": EDITED_KEY}, {})

    content = build_proposal_content(demo_store, data_dir, {}, {})

    assert _field(content, "prompt_version")["value"] == EDITED_KEY


def test_the_proposal_keeps_a_saved_label_as_written(demo_store: Store, data_dir: Path) -> None:
    write_ui_config(demo_store, data_dir, {}, {"label_map": {"Malicious": Verdict.BENIGN}})

    content = build_proposal_content(demo_store, data_dir, {}, {})

    assert content["label_map"]["Malicious"] == "benign"  # type: ignore[index]


def test_an_edit_wins_over_the_saved_label(demo_store: Store, data_dir: Path) -> None:
    write_ui_config(demo_store, data_dir, {}, {"label_map": {"Malicious": Verdict.BENIGN}})

    content = build_proposal_content(
        demo_store, data_dir, {}, {"label_map": {"Malicious": Verdict.TRUE_POSITIVE}}
    )

    assert content["label_map"]["Malicious"] == "true_positive"  # type: ignore[index]


def test_an_edit_wins_over_the_saved_field(demo_store: Store, data_dir: Path) -> None:
    write_ui_config(demo_store, data_dir, {"prompt_version": EDITED_KEY}, {})

    content = build_proposal_content(
        demo_store, data_dir, {"prompt_version": "detecttrace.prompt_version"}, {}
    )

    assert _field(content, "prompt_version")["value"] == "detecttrace.prompt_version"


def test_a_saved_hand_mapping_is_a_user_label(demo_store: Store, data_dir: Path) -> None:
    write_ui_config(demo_store, data_dir, {}, {"label_map": {"Malicious": Verdict.BENIGN}})

    content = build_proposal_content(demo_store, data_dir, {}, {})

    assert content["user_labels"] == {
        "label_map": {"Malicious": "benign"},
        "agent_label_map": {},
    }


def test_a_label_mapped_automatically_is_no_user_label(demo_store: Store, data_dir: Path) -> None:
    write_ui_config(demo_store, data_dir, {}, {"label_map": {"Malicious": Verdict.BENIGN}})

    content = build_proposal_content(demo_store, data_dir, {}, {})

    assert "TP" not in content["user_labels"]["label_map"]  # type: ignore[index]


def test_a_broken_saved_configuration_is_refused(demo_store: Store, data_dir: Path) -> None:
    (data_dir / CONFIG_NAME).write_text("mapping: [\n", encoding="utf-8")

    with pytest.raises(UiConfigError, match=r"Fix detecttrace\.yaml"):
        build_proposal_content(demo_store, data_dir, {}, {})


def test_saving_again_keeps_the_saved_label(demo_store: Store, data_dir: Path) -> None:
    write_ui_config(demo_store, data_dir, {}, {"label_map": {"Malicious": Verdict.BENIGN}})

    config = write_ui_config(demo_store, data_dir, {}, {})

    assert config.label_map["malicious"] == Verdict.BENIGN


def test_saving_again_writes_the_saved_label_as_written(demo_store: Store, data_dir: Path) -> None:
    write_ui_config(demo_store, data_dir, {}, {"label_map": {"Malicious": Verdict.BENIGN}})

    write_ui_config(demo_store, data_dir, {}, {})

    assert '"Malicious": "benign"' in (data_dir / CONFIG_NAME).read_text(encoding="utf-8")


@pytest.mark.parametrize(
    ("fields", "labels"),
    [
        ({"case": "x.case"}, {}),
        ({"case_id": " "}, {}),
        ({}, {"labels": {"TP": Verdict.TRUE_POSITIVE}}),
    ],
    ids=["unknown field", "empty key", "unknown label map"],
)
def test_an_edit_that_cannot_apply_raises(
    demo_store: Store,
    data_dir: Path,
    fields: dict[str, str],
    labels: dict[str, dict[str, Verdict]],
) -> None:
    with pytest.raises(UiConfigError):
        build_proposal_content(demo_store, data_dir, fields, labels)


def test_an_empty_store_raises(store: Store, data_dir: Path) -> None:
    with pytest.raises(UiConfigError, match="Upload traces and verdicts first"):
        build_proposal_content(store, data_dir, {}, {})


# Saving


def test_saving_writes_the_configuration_file(demo_store: Store, data_dir: Path) -> None:
    write_ui_config(demo_store, data_dir, {}, {})

    assert (data_dir / CONFIG_NAME).is_file()


def test_a_saved_field_edit_loads_back(demo_store: Store, data_dir: Path) -> None:
    config = write_ui_config(demo_store, data_dir, {"case_id": "detecttrace.verdict"}, {})

    assert config.mapping.case_id == "detecttrace.verdict"


def test_a_saved_label_edit_loads_back(demo_store: Store, data_dir: Path) -> None:
    config = write_ui_config(
        demo_store, data_dir, {}, {"label_map": {"Malicious": Verdict.TRUE_POSITIVE}}
    )

    assert config.label_map["malicious"] == Verdict.TRUE_POSITIVE


@pytest.mark.parametrize("text", ["traces:", "--set", "check reports"])
def test_the_saved_file_is_written_for_the_app(
    demo_store: Store, data_dir: Path, text: str
) -> None:
    write_ui_config(demo_store, data_dir, {"case_id": "detecttrace.verdict"}, {})

    assert text not in (data_dir / CONFIG_NAME).read_text(encoding="utf-8")


def test_saving_names_the_checklists_folder_when_it_holds_one(
    demo_store: Store, data_dir: Path
) -> None:
    shutil.copytree(DEMO_DIR / "checklists", data_dir / CHECKLISTS_FOLDER)

    config = write_ui_config(demo_store, data_dir, {}, {})

    assert config.checklists == data_dir.absolute() / CHECKLISTS_FOLDER


def test_saving_without_checklists_names_the_checklists_folder(
    demo_store: Store, data_dir: Path
) -> None:
    (data_dir / CHECKLISTS_FOLDER).mkdir()

    config = write_ui_config(demo_store, data_dir, {}, {})

    assert config.checklists == data_dir.absolute() / CHECKLISTS_FOLDER


def test_saving_with_a_required_field_missing_raises(
    no_case_id_store: Store, data_dir: Path
) -> None:
    with pytest.raises(UiConfigError, match="Case ID"):
        write_ui_config(no_case_id_store, data_dir, {}, {})


def test_saving_with_a_required_field_missing_writes_nothing(
    no_case_id_store: Store, data_dir: Path
) -> None:
    with pytest.raises(UiConfigError):
        write_ui_config(no_case_id_store, data_dir, {}, {})

    assert list(data_dir.iterdir()) == []


# Recompute settings


def test_the_app_holds_no_case_back(demo_store: Store, data_dir: Path) -> None:
    config = write_ui_config(demo_store, data_dir, {}, {})

    settings = to_recompute_settings(config, data_dir / CONFIG_NAME)

    assert settings.settle_seconds is None


def test_the_app_names_its_traces_uploaded(demo_store: Store, data_dir: Path) -> None:
    config = write_ui_config(demo_store, data_dir, {}, {})

    settings = to_recompute_settings(config, data_dir / CONFIG_NAME)

    assert settings.traces_source == UI_TRACES_SOURCE


def test_the_app_loads_uploaded_checklists(demo_store: Store, data_dir: Path) -> None:
    shutil.copytree(DEMO_DIR / "checklists", data_dir / CHECKLISTS_FOLDER)
    config = write_ui_config(demo_store, data_dir, {}, {})

    settings = to_recompute_settings(config, data_dir / CONFIG_NAME)

    assert sorted(settings.checklists) == ["impossible_travel", "oauth_consent"]


def test_the_app_without_checklists_loads_none(demo_store: Store, data_dir: Path) -> None:
    config = write_ui_config(demo_store, data_dir, {}, {})

    settings = to_recompute_settings(config, data_dir / CONFIG_NAME)

    assert settings.checklists == {}


def test_the_app_with_an_empty_checklists_folder_names_no_checklist_source(
    demo_store: Store, data_dir: Path
) -> None:
    (data_dir / CHECKLISTS_FOLDER).mkdir()
    config = write_ui_config(demo_store, data_dir, {}, {})

    settings = to_recompute_settings(config, data_dir / CONFIG_NAME)

    assert settings.checklist_source is None


def test_a_hand_named_empty_checklists_folder_is_refused(demo_store: Store, data_dir: Path) -> None:
    (data_dir / "other").mkdir()
    config = write_ui_config(demo_store, data_dir, {}, {})
    config = config.model_copy(update={"checklists": data_dir / "other"})

    with pytest.raises(ChecklistFileError):
        to_recompute_settings(config, data_dir / CONFIG_NAME)


@pytest.fixture
def unreadable_checklists(data_dir: Path) -> Iterator[Path]:
    folder = data_dir / CHECKLISTS_FOLDER
    folder.mkdir()
    folder.chmod(0)
    yield folder
    folder.chmod(0o700)


@needs_permissions
def test_an_unreadable_app_checklists_folder_is_refused(
    demo_store: Store, data_dir: Path, unreadable_checklists: Path
) -> None:
    config = write_ui_config(demo_store, data_dir, {}, {})

    with pytest.raises(ChecklistFileError, match="cannot be read"):
        to_recompute_settings(config, data_dir / CONFIG_NAME)


def test_the_app_renders_its_snapshot_as_the_ui_page(
    demo_store: Store, data_dir: Path, tmp_path: Path
) -> None:
    config = write_ui_config(demo_store, data_dir, {}, {})
    settings = to_recompute_settings(config, data_dir / CONFIG_NAME)

    outcome = compute_snapshot(tmp_path / "detecttrace.db", settings, time.time_ns())

    assert read_view(outcome.snapshot.html)["mode"] == "ui"
