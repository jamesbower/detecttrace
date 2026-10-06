import importlib.util
import shutil
from pathlib import Path

import pytest
from builders import otlp_document, otlp_span, span_hex, write_jsonl

from detecttrace.checklist import ChecklistFileError, load_checklists
from detecttrace.conventions import TOOL_CALL_RESULT
from detecttrace.serve import ui_uploads
from detecttrace.serve.store import Store
from detecttrace.serve.ui_uploads import (
    CHECKLIST_SUFFIXES,
    TRACE_SUFFIXES,
    VERDICT_SUFFIXES,
    UploadRefused,
    save_checklist_file,
    store_trace_file,
    store_verdict_file,
    to_safe_upload_name,
)
from detecttrace.summary import summarize_issues
from detecttrace.traces import load_spans
from detecttrace.verdicts import read_verdicts

DEMO_DATA = Path(__file__).parents[2] / "src" / "detecttrace" / "demo_data"
FIXTURES = Path(__file__).parent.parent / "fixtures"
DEMO_TRACES = DEMO_DATA / "traces" / "traces.jsonl.gz"
DEMO_VERDICTS = DEMO_DATA / "verdicts.csv"
DEMO_CHECKLISTS = DEMO_DATA / "checklists"
SINGLE_DOCUMENT_TRACES = FIXTURES / "formats" / "single_document" / "traces" / "traces.json.gz"
ZSTD_TRACES = FIXTURES / "formats" / "zstd" / "traces" / "traces.jsonl.zst"
LANGFUSE_TRACES = FIXTURES / "formats" / "langfuse" / "traces" / "api_page.json"
TRUNCATED_TRACES = (
    FIXTURES / "edge" / "broken_files" / "truncated_last_line" / "traces" / "traces.jsonl"
)
NEEDS_ZSTD = pytest.mark.skipif(
    importlib.util.find_spec("zstandard") is None, reason="zstandard is not installed"
)


# Upload names


@pytest.mark.parametrize(
    ("name", "suffixes", "expected"),
    [
        ("../../x.csv", VERDICT_SUFFIXES, "x.csv"),
        ("a\\b\\c.jsonl.gz", TRACE_SUFFIXES, "c.jsonl.gz"),
        ("my file (1).CSV", VERDICT_SUFFIXES, "my_file__1_.CSV"),
        ("travel.yml", CHECKLIST_SUFFIXES, "travel.yml"),
        ("a" * 300 + ".jsonl.gz", TRACE_SUFFIXES, "a" * 119 + ".jsonl.gz"),
    ],
)
def test_safe_upload_name_keeps_a_safe_base_name(
    name: str, suffixes: tuple[str, ...], expected: str
) -> None:
    assert to_safe_upload_name(name, suffixes) == expected


@pytest.mark.parametrize("name", ["", "..", ".csv", "x.txt", "folder/", "x.csv.txt"])
def test_safe_upload_name_refuses_a_name_without_a_usable_stem_and_suffix(name: str) -> None:
    with pytest.raises(UploadRefused):
        to_safe_upload_name(name, VERDICT_SUFFIXES)


def test_safe_upload_name_refusal_names_the_allowed_suffixes() -> None:
    with pytest.raises(UploadRefused, match=r"\.yaml, \.yml"):
        to_safe_upload_name("x.txt", CHECKLIST_SUFFIXES)


# Traces


@pytest.mark.parametrize(
    ("path", "expected"),
    [
        (DEMO_TRACES, "584 spans added."),
        (SINGLE_DOCUMENT_TRACES, "2,129 spans added."),
        pytest.param(ZSTD_TRACES, "584 spans added.", marks=NEEDS_ZSTD),
        (LANGFUSE_TRACES, "20 spans added."),
    ],
)
def test_trace_upload_reports_the_spans_added(app_store: Store, path: Path, expected: str) -> None:
    report = store_trace_file(app_store, path)

    assert report.stored_text == expected


def test_trace_upload_stores_the_spans(app_store: Store) -> None:
    store_trace_file(app_store, DEMO_TRACES)

    assert app_store.read_counts().span_count == 584


@pytest.mark.parametrize(
    ("path", "expected"), [(DEMO_TRACES, "otlp"), (LANGFUSE_TRACES, "langfuse")]
)
def test_first_trace_upload_records_the_trace_family(
    app_store: Store, path: Path, expected: str
) -> None:
    store_trace_file(app_store, path)

    assert app_store.read_trace_family() == expected


def test_same_trace_file_uploaded_twice_counts_duplicates(app_store: Store) -> None:
    store_trace_file(app_store, DEMO_TRACES)

    report = store_trace_file(app_store, DEMO_TRACES)

    assert report.stored_text == "0 spans added; 584 duplicates dropped."


def test_changed_span_uploaded_again_is_counted_as_a_conflict(
    app_store: Store, tmp_path: Path
) -> None:
    first = write_jsonl(tmp_path / "a.jsonl", [otlp_document([otlp_span(span_hex(1))])])
    second = write_jsonl(
        tmp_path / "b.jsonl", [otlp_document([otlp_span(span_hex(1), name="renamed")])]
    )
    store_trace_file(app_store, first)

    report = store_trace_file(app_store, second)

    assert report.stored_text == "0 spans added; 1 conflicting span not stored."


def test_trace_upload_does_not_store_tool_results(app_store: Store, tmp_path: Path) -> None:
    span = otlp_span(span_hex(1), attributes={TOOL_CALL_RESULT: "secret output"})
    path = write_jsonl(tmp_path / "traces.jsonl", [otlp_document([span])])

    store_trace_file(app_store, path)

    stored = app_store.read_inputs().spans
    assert [TOOL_CALL_RESULT in span.attributes for span in stored] == [False]


def test_langfuse_upload_into_an_otlp_store_is_refused(app_store: Store) -> None:
    store_trace_file(app_store, DEMO_TRACES)

    with pytest.raises(
        UploadRefused, match=r"holds OTLP traces\. Clear the data to switch to Langfuse"
    ):
        store_trace_file(app_store, LANGFUSE_TRACES)


def test_refused_langfuse_upload_leaves_the_store_unchanged(app_store: Store) -> None:
    store_trace_file(app_store, DEMO_TRACES)
    before = app_store.read_counts()

    with pytest.raises(UploadRefused):
        store_trace_file(app_store, LANGFUSE_TRACES)

    assert app_store.read_counts() == before


def test_file_with_no_trace_format_is_refused(app_store: Store, tmp_path: Path) -> None:
    path = tmp_path / "notes.jsonl"
    path.write_text("not json\n", encoding="utf-8")

    with pytest.raises(
        UploadRefused, match=r"notes\.jsonl is not a trace file we can read\. 1 trace"
    ):
        store_trace_file(app_store, path)


def test_trace_upload_problems_are_what_check_reports(app_store: Store) -> None:
    _, issues = load_spans(TRUNCATED_TRACES)

    report = store_trace_file(app_store, TRUNCATED_TRACES)

    assert report.problems == tuple(summarize_issues(issues))


def test_trace_upload_stores_the_file_issues(app_store: Store) -> None:
    _, issues = load_spans(TRUNCATED_TRACES)

    store_trace_file(app_store, TRUNCATED_TRACES)

    assert [stored.issue for stored in app_store.read_inputs().issues] == issues


# Verdicts


def test_verdict_upload_reports_the_verdicts_added(app_store: Store) -> None:
    report = store_verdict_file(app_store, DEMO_VERDICTS)

    assert report.stored_text == "201 verdicts added."


def test_verdict_upload_stores_the_rows(app_store: Store) -> None:
    store_verdict_file(app_store, DEMO_VERDICTS)

    assert app_store.read_counts().verdict_count == 201


def test_verdict_upload_with_one_changed_label_reports_one_replaced(
    app_store: Store, tmp_path: Path
) -> None:
    changed = tmp_path / "verdicts.csv"
    changed.write_text(
        DEMO_VERDICTS.read_text(encoding="utf-8").replace(
            "DT-IT-0002,impossible_travel,FP,", "DT-IT-0002,impossible_travel,TP,"
        ),
        encoding="utf-8",
    )
    store_verdict_file(app_store, DEMO_VERDICTS)

    report = store_verdict_file(app_store, changed)

    assert report.stored_text == "0 verdicts added; 1 replaced; 200 unchanged."


def test_verdict_upload_problems_are_what_check_reports(app_store: Store, tmp_path: Path) -> None:
    path = tmp_path / "verdicts.csv"
    path.write_text("case_id,alert_class,verdict\nDT-1,phishing,\n", encoding="utf-8")
    _, issues = read_verdicts(path)

    report = store_verdict_file(app_store, path)

    assert report.problems == tuple(summarize_issues(issues))


def test_non_utf8_verdict_file_is_refused(app_store: Store, tmp_path: Path) -> None:
    path = tmp_path / "verdicts.csv"
    path.write_bytes(b"case_id,alert_class,verdict\nDT-1,phishing,\xff\n")

    with pytest.raises(UploadRefused, match="is not UTF-8"):
        store_verdict_file(app_store, path)


# Checklists


def test_checklist_is_saved_under_its_alert_class(tmp_path: Path) -> None:
    folder = tmp_path / "checklists"

    save_checklist_file(DEMO_CHECKLISTS / "oauth_consent.yaml", folder)

    assert (folder / "oauth_consent.yaml").read_text(encoding="utf-8") == (
        DEMO_CHECKLISTS / "oauth_consent.yaml"
    ).read_text(encoding="utf-8")


def test_checklist_upload_reports_the_alert_class(tmp_path: Path) -> None:
    report = save_checklist_file(DEMO_CHECKLISTS / "oauth_consent.yaml", tmp_path)

    assert report.stored_text == "Checklist saved for alert class 'oauth_consent'."


def test_checklist_for_a_saved_class_replaces_the_earlier_file(tmp_path: Path) -> None:
    folder = tmp_path / "checklists"
    folder.mkdir()
    shutil.copy(DEMO_CHECKLISTS / "impossible_travel.yaml", folder / "travel.yml")

    save_checklist_file(DEMO_CHECKLISTS / "impossible_travel.yaml", folder)

    assert sorted(path.name for path in folder.iterdir()) == ["impossible_travel.yaml"]


def test_checklist_replacing_an_earlier_one_says_so(tmp_path: Path) -> None:
    save_checklist_file(DEMO_CHECKLISTS / "oauth_consent.yaml", tmp_path)

    report = save_checklist_file(DEMO_CHECKLISTS / "oauth_consent.yaml", tmp_path)

    assert report.stored_text == (
        "Checklist saved for alert class 'oauth_consent'. It replaces the earlier one."
    )


def test_invalid_checklist_is_refused_with_the_loader_message(tmp_path: Path) -> None:
    path = tmp_path / "bad.yaml"
    path.write_text("alert_class: [\n", encoding="utf-8")
    with pytest.raises(ChecklistFileError) as loader_error:
        load_checklists(path)

    with pytest.raises(UploadRefused) as refusal:
        save_checklist_file(path, tmp_path / "checklists")

    assert str(refusal.value) == str(loader_error.value)


def test_invalid_checklist_writes_no_file(tmp_path: Path) -> None:
    path = tmp_path / "bad.yaml"
    path.write_text("alert_class: [\n", encoding="utf-8")
    folder = tmp_path / "checklists"
    folder.mkdir()

    with pytest.raises(UploadRefused):
        save_checklist_file(path, folder)

    assert list(folder.iterdir()) == []


def test_new_alert_class_at_the_checklist_cap_is_refused(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(ui_uploads, "MAX_CHECKLISTS", 1)
    save_checklist_file(DEMO_CHECKLISTS / "impossible_travel.yaml", tmp_path)

    with pytest.raises(UploadRefused, match="already holds 1 checklists"):
        save_checklist_file(DEMO_CHECKLISTS / "oauth_consent.yaml", tmp_path)


def test_saved_alert_class_at_the_checklist_cap_is_replaced(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(ui_uploads, "MAX_CHECKLISTS", 1)
    save_checklist_file(DEMO_CHECKLISTS / "impossible_travel.yaml", tmp_path)

    report = save_checklist_file(DEMO_CHECKLISTS / "impossible_travel.yaml", tmp_path)

    assert report.stored_text.endswith("It replaces the earlier one.")


def test_checklist_whose_file_name_holds_another_class_is_refused(tmp_path: Path) -> None:
    other = tmp_path / "upload.yaml"
    other.write_text("alert_class: a/b\nitems:\n- id: x\n  tool: t\n", encoding="utf-8")
    folder = tmp_path / "checklists"
    folder.mkdir()
    (folder / "a_b.yaml").write_text(
        "alert_class: a_b\nitems:\n- id: x\n  tool: t\n", encoding="utf-8"
    )

    with pytest.raises(UploadRefused, match=r"a_b\.yaml already holds the checklist"):
        save_checklist_file(other, folder)
