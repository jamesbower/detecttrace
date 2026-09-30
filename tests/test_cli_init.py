import shutil
from pathlib import Path
from typing import Any

import pytest
import yaml
from builders import langfuse_row, otlp_span, run_trace, span_hex, write_jsonl
from typer.testing import CliRunner, Result

from detecttrace import cli
from detecttrace.init_writer import RoundTripError
from detecttrace.model import Issue, IssueKind
from detecttrace.summary import summarize_issues
from detecttrace.verdicts import read_verdicts

DEMO_DATA = Path(cli.__file__).parent / cli.DEMO_FOLDER
FIXTURES = Path(__file__).parent / "fixtures"
REAL_API_PAGES = [f"v2_all_fields_page{number}.json" for number in (1, 2, 3, 4)]
NO_TERMINAL = "init needs a terminal to ask questions. Run init in a terminal, or use --yes."
VERDICTS = "case_id,alert_class,verdict\nDT-1,impossible_travel,TP\nDT-2,impossible_travel,FP\n"
ESCAPE = "\x1b[31m"
# Answers to the six mapping prompts that keep every proposal.
ACCEPT_MAPPING = "\n" * 6


def _invoke(*args: str, input: str | None = None) -> Result:
    return CliRunner().invoke(cli.app, list(args), input=input)


def _write_inputs(
    folder: Path,
    *,
    case_ids: tuple[str, ...] = ("DT-1", "DT-2"),
    verdicts: str = VERDICTS,
    documents: list[dict[str, Any]] | None = None,
) -> tuple[Path, Path]:
    """Write traces/ and verdicts.csv under `folder`; return their paths."""
    (folder / "traces").mkdir(parents=True)
    if documents is None:
        documents = [run_trace(number, case_id) for number, case_id in enumerate(case_ids, 1)]
    write_jsonl(folder / "traces" / "batch.jsonl", documents)
    (folder / "verdicts.csv").write_text(verdicts, encoding="utf-8")
    return folder / "traces", folder / "verdicts.csv"


def _init(folder: Path, *args: str, input: str | None = None) -> Result:
    traces, verdicts = folder / "traces", folder / "verdicts.csv"
    return _invoke(
        "init",
        "--traces",
        str(traces),
        "--verdicts",
        str(verdicts),
        "--config",
        str(folder / "detecttrace.yaml"),
        *args,
        input=input,
    )


def _list_files(folder: Path) -> list[str]:
    return sorted(path.relative_to(folder).as_posix() for path in folder.rglob("*"))


@pytest.fixture
def interactive(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(cli, "_is_interactive", lambda: True)


@pytest.fixture
def demo_copy(tmp_path: Path) -> Path:
    shutil.copytree(DEMO_DATA / "traces", tmp_path / "traces")
    shutil.copy(DEMO_DATA / "verdicts.csv", tmp_path / "verdicts.csv")
    return tmp_path


def _write_langfuse_without_tools(folder: Path) -> None:
    (folder / "traces").mkdir()
    agent: dict[str, object] = {
        "detecttrace.case_id": "DT-1",
        "detecttrace.alert_class": "impossible_travel",
        "detecttrace.verdict": "TP",
    }
    row = langfuse_row(span_hex(1), type="AGENT", name="invoke_agent triage", attributes=agent)
    write_jsonl(folder / "traces" / "rows.jsonl", [row])
    (folder / "verdicts.csv").write_text(
        "case_id,alert_class,verdict\nDT-1,impossible_travel,TP\n", encoding="utf-8"
    )


def _run_trace_with_attribute(number: int, case_id: str, key: str, value: str) -> dict[str, Any]:
    document = run_trace(number, case_id)
    root = document["resourceSpans"][0]["scopeSpans"][0]["spans"][0]
    root["attributes"].append({"key": key, "value": {"stringValue": value}})
    return document


def _run_trace_with_renamed_attribute(
    number: int, case_id: str, old: str, new: str
) -> dict[str, Any]:
    document = run_trace(number, case_id)
    root = document["resourceSpans"][0]["scopeSpans"][0]["spans"][0]
    for attribute in root["attributes"]:
        if attribute["key"] == old:
            attribute["key"] = new
    return document


def _copy_real_api_pages(folder: Path) -> None:
    shutil.copytree(
        FIXTURES / "langfuse_real",
        folder / "traces",
        ignore=lambda _folder, names: [name for name in names if name not in REAL_API_PAGES],
    )


def _run_trace_without_case_id() -> dict[str, Any]:
    document = run_trace(1, "DT-1")
    del document["resourceSpans"][0]["scopeSpans"][0]["spans"][0]["attributes"][1]
    return document


# --yes and --dry-run


def test_yes_dry_run_prints_a_configuration_that_loads(demo_copy: Path) -> None:
    result = _init(demo_copy, "--yes", "--dry-run")

    config = next(yaml.safe_load_all(result.stdout))
    assert config["traces"] == {"path": "traces", "format": "otlp_jsonl"}


def test_yes_dry_run_prints_the_example_checklist_as_a_second_document(demo_copy: Path) -> None:
    result = _init(demo_copy, "--yes", "--dry-run")

    checklist = list(yaml.safe_load_all(result.stdout))[1]
    assert checklist["alert_class"] == "impossible_travel"


def test_yes_dry_run_names_the_checklist_path_before_it(demo_copy: Path) -> None:
    result = _init(demo_copy, "--yes", "--dry-run")

    assert "--- # checklists/impossible_travel.yaml.example\n" in result.stdout


def test_yes_dry_run_writes_nothing(demo_copy: Path) -> None:
    before = _list_files(demo_copy)

    _init(demo_copy, "--yes", "--dry-run")

    assert _list_files(demo_copy) == before


def test_yes_dry_run_exits_0(demo_copy: Path) -> None:
    result = _init(demo_copy, "--yes", "--dry-run")

    assert result.exit_code == 0


def test_yes_dry_run_keeps_the_found_summary_off_stdout(demo_copy: Path) -> None:
    result = _init(demo_copy, "--yes", "--dry-run")

    assert "Traces: otlp_jsonl, 201 agent runs." not in result.stdout


def test_found_summary_shows_each_field_with_its_coverage(demo_copy: Path) -> None:
    result = _init(demo_copy, "--yes", "--dry-run")

    assert (
        "  case_id: detecttrace.case_id, found on 201 of 201 agent runs (detecttrace attribute)"
        in result.stderr
    )


def test_found_summary_shows_the_detected_format(demo_copy: Path) -> None:
    result = _init(demo_copy, "--yes", "--dry-run")

    assert "Traces: otlp_jsonl, 201 agent runs. Verdicts: 201 rows." in result.stderr


def test_found_summary_lists_tool_names_per_alert_class(tmp_path: Path) -> None:
    _write_inputs(tmp_path)

    result = _init(tmp_path, "--yes", "--dry-run")

    assert "  impossible_travel (2 cases): get_signin_logs" in result.stderr


def test_found_summary_counts_every_tool_past_the_listed_ones(tmp_path: Path) -> None:
    document = run_trace(1, "DT-1")
    spans = document["resourceSpans"][0]["scopeSpans"][0]["spans"]
    root = spans[0]
    spans += [
        otlp_span(
            f"{index + 16:016x}",
            root["spanId"],
            trace_id=root["traceId"],
            name=f"execute_tool tool{index:03d}",
            attributes={"gen_ai.operation.name": "execute_tool"},
        )
        for index in range(210)
    ]
    _write_inputs(tmp_path, documents=[document])

    result = _init(tmp_path, "--yes", "--dry-run")

    assert "and 201 more" in result.stderr


def test_found_summary_shows_mapped_labels(tmp_path: Path) -> None:
    _write_inputs(tmp_path)

    result = _init(tmp_path, "--yes", "--dry-run")

    assert "  TP: true_positive" in result.stderr


def test_found_summary_shows_unmapped_labels(demo_copy: Path) -> None:
    result = _init(demo_copy, "--yes", "--dry-run")

    assert "  Malicious: not mapped" in result.stderr


def test_found_summary_shows_a_label_mapped_with_set(demo_copy: Path) -> None:
    result = _init(demo_copy, "--yes", "--dry-run", "--set", "label_map.Malicious=true_positive")

    assert "  Malicious: true_positive" in result.stderr


def test_found_summary_does_not_call_a_label_mapped_with_set_unmapped(demo_copy: Path) -> None:
    result = _init(demo_copy, "--yes", "--dry-run", "--set", "label_map.Malicious=true_positive")

    assert "  Malicious: not mapped" not in result.stderr


def test_yes_warns_about_labels_left_unmapped(demo_copy: Path) -> None:
    result = _init(demo_copy, "--yes", "--dry-run")

    assert "Warning: 2 labels are not mapped." in result.stderr


def test_quiet_leaves_out_the_found_summary(demo_copy: Path) -> None:
    result = _init(demo_copy, "--yes", "--dry-run", "--quiet")

    assert result.stderr == ""


# Writing


def test_yes_writes_the_configuration(demo_copy: Path) -> None:
    _init(demo_copy, "--yes")

    assert (demo_copy / "detecttrace.yaml").is_file()


def test_yes_writes_the_example_checklist(demo_copy: Path) -> None:
    _init(demo_copy, "--yes")

    assert (demo_copy / "checklists" / "impossible_travel.yaml.example").is_file()


def test_yes_says_what_to_do_next(demo_copy: Path) -> None:
    result = _init(demo_copy, "--yes")

    assert "rename it to impossible_travel.yaml, then run detecttrace check" in result.stdout


def test_an_existing_configuration_without_force_exits_1(demo_copy: Path) -> None:
    _init(demo_copy, "--yes")

    result = _init(demo_copy, "--yes")

    assert result.exit_code == 1


def test_an_existing_configuration_without_force_is_named(demo_copy: Path) -> None:
    _init(demo_copy, "--yes")

    result = _init(demo_copy, "--yes")

    assert f"{demo_copy / 'detecttrace.yaml'} exists" in result.stderr


def test_an_existing_example_checklist_without_force_is_named(demo_copy: Path) -> None:
    _init(demo_copy, "--yes")
    (demo_copy / "detecttrace.yaml").unlink()

    result = _init(demo_copy, "--yes")

    assert f"{demo_copy / 'checklists' / 'impossible_travel.yaml.example'} exists" in result.stderr


def test_an_existing_example_checklist_is_kept_without_force(demo_copy: Path) -> None:
    example = demo_copy / "checklists" / "impossible_travel.yaml.example"
    example.parent.mkdir()
    example.write_text("mine\n", encoding="utf-8")

    _init(demo_copy, "--yes")

    assert example.read_text(encoding="utf-8") == "mine\n"


def test_force_overwrites_an_existing_configuration(demo_copy: Path) -> None:
    (demo_copy / "detecttrace.yaml").write_text("mine\n", encoding="utf-8")

    _init(demo_copy, "--yes", "--force")

    assert (demo_copy / "detecttrace.yaml").read_text(encoding="utf-8").startswith("# ")


def test_a_missing_configuration_folder_exits_1(demo_copy: Path) -> None:
    result = _invoke(
        "init",
        "--traces",
        str(demo_copy / "traces"),
        "--verdicts",
        str(demo_copy / "verdicts.csv"),
        "--config",
        str(demo_copy / "gone" / "detecttrace.yaml"),
        "--yes",
    )

    assert result.exit_code == 1


# No terminal


def test_no_terminal_without_yes_exits_1(tmp_path: Path) -> None:
    _write_inputs(tmp_path)

    result = _init(tmp_path)

    assert result.exit_code == 1


def test_no_terminal_without_yes_says_how_to_fix_it(tmp_path: Path) -> None:
    _write_inputs(tmp_path)

    result = _init(tmp_path)

    assert result.stderr == f"Error: {NO_TERMINAL}\n"


# Interactive


def test_interactive_accept_all_writes_the_proposal(tmp_path: Path, interactive: None) -> None:
    _write_inputs(tmp_path)

    _init(tmp_path, input=ACCEPT_MAPPING + "y\n")

    config = yaml.safe_load((tmp_path / "detecttrace.yaml").read_text(encoding="utf-8"))
    assert config["label_map"] == {"FP": "false_positive", "TP": "true_positive"}


def test_interactive_asks_before_writing(tmp_path: Path, interactive: None) -> None:
    _write_inputs(tmp_path)

    result = _init(tmp_path, input=ACCEPT_MAPPING + "y\n")

    assert f"Write {tmp_path / 'detecttrace.yaml'}? [Y/n]" in result.stderr


def test_interactive_replaces_the_case_id(tmp_path: Path, interactive: None) -> None:
    _write_inputs(tmp_path)

    _init(tmp_path, input="soc.case\n" + "\n" * 5 + "y\n")

    config = yaml.safe_load((tmp_path / "detecttrace.yaml").read_text(encoding="utf-8"))
    assert config["mapping"]["case_id"] == "soc.case"


def test_interactive_asks_again_for_a_missing_required_field(
    tmp_path: Path, interactive: None
) -> None:
    _write_inputs(tmp_path, documents=[_run_trace_without_case_id()])

    _init(tmp_path, input="\nsoc.case\n" + "\n" * 5 + "y\n")

    config = yaml.safe_load((tmp_path / "detecttrace.yaml").read_text(encoding="utf-8"))
    assert config["mapping"]["case_id"] == "soc.case"


def test_interactive_asks_again_after_an_unknown_verdict(tmp_path: Path, interactive: None) -> None:
    _write_inputs(tmp_path, verdicts=VERDICTS + "DT-3,impossible_travel,Closed - Benign\n")

    _init(tmp_path, input=ACCEPT_MAPPING + "maybe\nbenign\ny\n")

    config = yaml.safe_load((tmp_path / "detecttrace.yaml").read_text(encoding="utf-8"))
    assert config["label_map"]["Closed - Benign"] == "benign"


def test_interactive_maps_an_unknown_label(tmp_path: Path, interactive: None) -> None:
    _write_inputs(tmp_path, verdicts=VERDICTS + "DT-3,impossible_travel,Closed - Benign\n")

    _init(tmp_path, input=ACCEPT_MAPPING + "benign\ny\n")

    config = yaml.safe_load((tmp_path / "detecttrace.yaml").read_text(encoding="utf-8"))
    assert config["label_map"]["Closed - Benign"] == "benign"


def test_interactive_can_leave_a_label_unmapped(tmp_path: Path, interactive: None) -> None:
    _write_inputs(tmp_path, verdicts=VERDICTS + "DT-3,impossible_travel,Closed - Benign\n")

    _init(tmp_path, input=ACCEPT_MAPPING + "\ny\n")

    config = yaml.safe_load((tmp_path / "detecttrace.yaml").read_text(encoding="utf-8"))
    assert "Closed - Benign" not in config["label_map"]


def test_interactive_decline_writes_nothing(tmp_path: Path, interactive: None) -> None:
    _write_inputs(tmp_path)
    before = _list_files(tmp_path)

    _init(tmp_path, input=ACCEPT_MAPPING + "n\n")

    assert _list_files(tmp_path) == before


def test_interactive_decline_exits_0(tmp_path: Path, interactive: None) -> None:
    _write_inputs(tmp_path)

    result = _init(tmp_path, input=ACCEPT_MAPPING + "n\n")

    assert result.exit_code == 0


def test_interactive_end_of_input_exits_1(tmp_path: Path, interactive: None) -> None:
    _write_inputs(tmp_path)

    result = _init(tmp_path, input="\n")

    assert result.exit_code == 1


def test_interactive_end_of_input_at_the_final_question_exits_1(
    tmp_path: Path, interactive: None
) -> None:
    _write_inputs(tmp_path)

    result = _init(tmp_path, input=ACCEPT_MAPPING)

    assert result.exit_code == 1


def test_interactive_end_of_input_at_the_final_question_says_nothing_was_written(
    tmp_path: Path, interactive: None
) -> None:
    _write_inputs(tmp_path)

    result = _init(tmp_path, input=ACCEPT_MAPPING)

    assert result.stderr.endswith("\nStopped. Nothing was written.\n")


def test_interactive_shows_a_field_not_found_as_not_found(
    tmp_path: Path, interactive: None
) -> None:
    documents = [
        _run_trace_with_renamed_attribute(1, "DT-1", "detecttrace.alert_class", "soc.kind")
    ]
    _write_inputs(tmp_path, documents=documents)

    result = _init(tmp_path, input=ACCEPT_MAPPING + "n\n")

    assert "alert_class [not found]:" in result.stderr


def test_interactive_case_id_entered_at_the_prompt_gives_the_example_checklist(
    tmp_path: Path, interactive: None
) -> None:
    documents = [_run_trace_with_renamed_attribute(1, "DT-1", "detecttrace.case_id", "soc.ticket")]
    _write_inputs(tmp_path, documents=documents)

    _init(tmp_path, input="soc.ticket\n" + "\n" * 5 + "y\n")

    assert (tmp_path / "checklists" / "impossible_travel.yaml.example").is_file()


# Required fields


def test_yes_with_a_missing_case_id_exits_1(tmp_path: Path) -> None:
    _write_inputs(tmp_path, documents=[_run_trace_without_case_id()])

    result = _init(tmp_path, "--yes", "--dry-run")

    assert result.exit_code == 1


def test_yes_with_a_missing_case_id_names_it_and_the_fix(tmp_path: Path) -> None:
    _write_inputs(tmp_path, documents=[_run_trace_without_case_id()])

    result = _init(tmp_path, "--yes", "--dry-run")

    assert "case_id: set it with --set mapping.case_id=<attribute>" in result.stderr


def test_set_satisfies_a_missing_case_id(tmp_path: Path) -> None:
    _write_inputs(tmp_path, documents=[_run_trace_without_case_id()])

    result = _init(tmp_path, "--yes", "--dry-run", "--set", "mapping.case_id=soc.case")

    assert result.exit_code == 0


def test_yes_without_any_mappable_label_names_it(tmp_path: Path) -> None:
    _write_inputs(tmp_path, verdicts="case_id,alert_class,verdict\nDT-1,impossible_travel,Bad\n")

    result = _init(tmp_path, "--yes", "--dry-run")

    assert "verdict labels: no verdict label could be mapped" in result.stderr


def test_an_unknown_set_key_exits_1(tmp_path: Path) -> None:
    _write_inputs(tmp_path)

    result = _init(tmp_path, "--yes", "--dry-run", "--set", "mapping.nope=x")

    assert result.exit_code == 1


def test_a_set_value_is_written(tmp_path: Path) -> None:
    _write_inputs(tmp_path)

    result = _init(tmp_path, "--yes", "--dry-run", "--set", "output=report.html")

    assert next(yaml.safe_load_all(result.stdout))["output"] == "report.html"


# Mapping set with --set


def test_a_set_case_id_gives_the_example_checklist(tmp_path: Path) -> None:
    documents = [_run_trace_with_renamed_attribute(1, "DT-1", "detecttrace.case_id", "soc.ticket")]
    _write_inputs(tmp_path, documents=documents)

    result = _init(tmp_path, "--yes", "--dry-run", "--set", "mapping.case_id=soc.ticket")

    assert list(yaml.safe_load_all(result.stdout))[1]["alert_class"] == "impossible_travel"


def test_a_set_case_id_gives_the_orphan_counts(tmp_path: Path) -> None:
    documents = [
        _run_trace_with_attribute(1, "DT-1", "soc.ticket", "X-1"),
        _run_trace_with_attribute(2, "DT-2", "soc.ticket", "X-2"),
    ]
    _write_inputs(tmp_path, documents=documents)

    result = _init(tmp_path, "--yes", "--dry-run", "--set", "mapping.case_id=soc.ticket")

    assert "Traces without a verdict: 2 (X-1, X-2)" in result.stderr


def test_a_set_field_shows_its_coverage(tmp_path: Path) -> None:
    documents = [
        _run_trace_with_renamed_attribute(1, "DT-1", "detecttrace.alert_class", "soc.kind"),
        _run_trace_with_renamed_attribute(2, "DT-2", "detecttrace.alert_class", "other.kind"),
    ]
    _write_inputs(tmp_path, documents=documents)

    result = _init(tmp_path, "--yes", "--dry-run", "--set", "mapping.alert_class=soc.kind")

    assert '#   alert_class: "soc.kind", found on 1 of 2 agent runs (set by you)' in result.stdout


def test_a_set_mapping_keeps_the_labels_set_with_set(tmp_path: Path) -> None:
    documents = [_run_trace_with_attribute(1, "DT-1", "soc.ticket", "DT-1")]
    _write_inputs(tmp_path, documents=documents, verdicts=VERDICTS + "DT-3,phishing,Bad\n")

    result = _init(
        tmp_path,
        "--yes",
        "--dry-run",
        "--set",
        "label_map.Bad=benign",
        "--set",
        "mapping.case_id=soc.ticket",
    )

    assert next(yaml.safe_load_all(result.stdout))["label_map"]["Bad"] == "benign"


# Checklists folder set with --set


def test_dry_run_with_a_checklists_folder_outside_exits_0(
    demo_copy: Path, tmp_path_factory: pytest.TempPathFactory
) -> None:
    elsewhere = tmp_path_factory.mktemp("elsewhere")

    result = _init(demo_copy, "--yes", "--dry-run", "--set", f"checklists={elsewhere}")

    assert result.exit_code == 0


def test_dry_run_names_a_checklist_outside_by_its_path(
    demo_copy: Path, tmp_path_factory: pytest.TempPathFactory
) -> None:
    elsewhere = tmp_path_factory.mktemp("elsewhere")

    result = _init(demo_copy, "--yes", "--dry-run", "--set", f"checklists={elsewhere}")

    assert f"--- # {elsewhere.as_posix()}/impossible_travel.yaml.example\n" in result.stdout


def test_yes_writes_the_example_checklist_into_a_folder_outside(
    demo_copy: Path, tmp_path_factory: pytest.TempPathFactory
) -> None:
    elsewhere = tmp_path_factory.mktemp("elsewhere")

    _init(demo_copy, "--yes", "--set", f"checklists={elsewhere}")

    assert (elsewhere / "impossible_travel.yaml.example").is_file()


# Input problems


def test_console_exporter_output_exits_1(tmp_path: Path) -> None:
    result = _invoke(
        "init",
        "--traces",
        str(FIXTURES / "console_exporter" / "console.json"),
        "--verdicts",
        str(FIXTURES / "formats" / "console_exporter" / "verdicts.csv"),
        "--config",
        str(tmp_path / "detecttrace.yaml"),
        "--yes",
    )

    assert result.exit_code == 1


def test_console_exporter_output_points_at_file_span_exporter(tmp_path: Path) -> None:
    result = _invoke(
        "init",
        "--traces",
        str(FIXTURES / "console_exporter" / "console.json"),
        "--verdicts",
        str(FIXTURES / "formats" / "console_exporter" / "verdicts.csv"),
        "--config",
        str(tmp_path / "detecttrace.yaml"),
        "--yes",
    )

    assert "FileSpanExporter" in result.stderr


def test_traces_of_no_known_format_exit_1(tmp_path: Path) -> None:
    _write_inputs(tmp_path, documents=[{"hello": "world"}])

    result = _init(tmp_path, "--yes", "--dry-run")

    assert result.exit_code == 1


def test_a_missing_trace_path_exits_1(tmp_path: Path) -> None:
    (tmp_path / "verdicts.csv").write_text(VERDICTS, encoding="utf-8")

    result = _init(tmp_path, "--yes", "--dry-run")

    assert result.exit_code == 1


def test_a_missing_trace_path_names_the_traces_option(tmp_path: Path) -> None:
    (tmp_path / "verdicts.csv").write_text(VERDICTS, encoding="utf-8")

    result = _init(tmp_path, "--yes", "--dry-run")

    assert f"Trace path not found: {tmp_path / 'traces'}. Check --traces." in result.stderr


def test_an_empty_trace_folder_names_the_traces_option(tmp_path: Path) -> None:
    (tmp_path / "traces").mkdir()
    (tmp_path / "verdicts.csv").write_text(VERDICTS, encoding="utf-8")

    result = _init(tmp_path, "--yes", "--dry-run")

    assert f"No trace files found under {tmp_path / 'traces'}. Check --traces." in result.stderr


def test_a_missing_verdict_file_names_the_verdicts_option(tmp_path: Path) -> None:
    _write_inputs(tmp_path)
    (tmp_path / "verdicts.csv").unlink()

    result = _init(tmp_path, "--yes", "--dry-run")

    assert (
        f"Verdict file not found: {tmp_path / 'verdicts.csv'}. Check --verdicts." in result.stderr
    )


def test_a_verdict_file_without_the_required_columns_exits_1(tmp_path: Path) -> None:
    _write_inputs(tmp_path, verdicts="id,label\nDT-1,TP\n")

    result = _init(tmp_path, "--yes", "--dry-run")

    assert result.exit_code == 1


def test_input_problems_name_the_configuration_file_given(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    traces, verdicts = _write_inputs(tmp_path)

    # No reading problem names the configuration yet, so one that does is added.
    def read_with_a_problem(path: Path, **kwargs: Any) -> tuple[list[Any], list[Issue]]:
        rows, issues = read_verdicts(path, **kwargs)
        return rows, [*issues, Issue(IssueKind.UNMAPPED_ANALYST_LABEL, "X")]

    monkeypatch.setattr(cli, "read_verdicts", read_with_a_problem)
    result = _invoke(
        "init",
        "--traces",
        str(traces),
        "--verdicts",
        str(verdicts),
        "--config",
        str(tmp_path / "other.yaml"),
        "--yes",
        "--dry-run",
    )

    assert "Add it to label_map in other.yaml." in result.stderr


# Trace format set with --set


def _init_otlp_subset(tmp_path: Path, trace_format: str) -> Result:
    folder = FIXTURES / "formats" / "otlp_subset"
    return _invoke(
        "init",
        "--traces",
        str(folder / "traces"),
        "--verdicts",
        str(folder / "verdicts.csv"),
        "--config",
        str(tmp_path / "detecttrace.yaml"),
        "--yes",
        "--dry-run",
        "--set",
        f"traces.format={trace_format}",
    )


def test_a_set_trace_format_that_reads_no_span_exits_1(tmp_path: Path) -> None:
    result = _init_otlp_subset(tmp_path, "langfuse")

    assert result.exit_code == 1


def test_a_set_trace_format_that_reads_no_span_reports_it_as_check_would(
    tmp_path: Path,
) -> None:
    [line] = summarize_issues([Issue(IssueKind.INVALID_LANGFUSE_DOCUMENT, "x")])

    result = _init_otlp_subset(tmp_path, "langfuse")

    assert line.terminal_hint in result.stderr


def test_a_set_trace_format_is_the_one_the_summary_names(tmp_path: Path) -> None:
    result = _init_otlp_subset(tmp_path, "otlp_json")

    assert "Traces: otlp_json, " in result.stderr


def test_a_set_trace_format_is_the_one_the_header_names(tmp_path: Path) -> None:
    result = _init_otlp_subset(tmp_path, "otlp_json")

    assert "# Found: otlp_json traces, " in result.stdout


# Orphans


def test_orphan_report_counts_traces_without_a_verdict(tmp_path: Path) -> None:
    _write_inputs(tmp_path, case_ids=("DT-1", "DT-2", "DT-3", "DT-4", "DT-5", "DT-6"))

    result = _init(tmp_path, "--yes", "--dry-run")

    assert "Traces without a verdict: 4 (DT-3, DT-4, DT-5, ...)" in result.stderr


def test_orphan_report_counts_verdicts_without_a_trace(tmp_path: Path) -> None:
    _write_inputs(tmp_path, case_ids=("DT-1",))

    result = _init(tmp_path, "--yes", "--dry-run")

    assert "Verdicts without a trace: 1 (DT-2)" in result.stderr


# Untrusted text


def test_an_escape_in_a_tool_name_prints_escaped(tmp_path: Path) -> None:
    document = run_trace(1, "DT-1")
    tool = document["resourceSpans"][0]["scopeSpans"][0]["spans"][1]
    tool["attributes"][1]["value"] = {"stringValue": f"get{ESCAPE}logs"}
    _write_inputs(tmp_path, documents=[document])

    result = _init(tmp_path, "--yes", "--dry-run")

    assert ESCAPE not in result.output


def test_an_escape_in_a_tool_name_is_shown_as_text(tmp_path: Path) -> None:
    document = run_trace(1, "DT-1")
    tool = document["resourceSpans"][0]["scopeSpans"][0]["spans"][1]
    tool["attributes"][1]["value"] = {"stringValue": f"get{ESCAPE}logs"}
    _write_inputs(tmp_path, documents=[document])

    result = _init(tmp_path, "--yes", "--dry-run")

    assert "get\\x1b[31mlogs" in result.stderr


def test_an_escape_in_a_label_prints_escaped(tmp_path: Path, interactive: None) -> None:
    _write_inputs(tmp_path, verdicts=VERDICTS + f"DT-3,impossible_travel,Bad{ESCAPE}\n")

    result = _init(tmp_path, input=ACCEPT_MAPPING + "\nn\n")

    assert ESCAPE not in result.output


# Langfuse


def test_langfuse_real_capture_dry_run_detects_langfuse(tmp_path: Path) -> None:
    _copy_real_api_pages(tmp_path)
    (tmp_path / "verdicts.csv").write_text(
        "case_id,alert_class,verdict\n"
        "CASE-9001,impossible_travel,TP\n"
        "CASE-9002,impossible_travel,TP\n"
        "CASE-9003,impossible_travel,TP\n"
        "CASE-9004,impossible_travel,TP\n",
        encoding="utf-8",
    )

    result = _init(tmp_path, "--yes", "--dry-run")

    assert next(yaml.safe_load_all(result.stdout))["traces"]["format"] == "langfuse"


def test_langfuse_without_tool_calls_prints_the_span_filter_hint(tmp_path: Path) -> None:
    _write_langfuse_without_tools(tmp_path)

    result = _init(tmp_path, "--yes", "--dry-run")

    assert "should_export_span=lambda span: True" in result.stderr


def test_langfuse_without_tool_calls_is_reported_in_checks_words(tmp_path: Path) -> None:
    _write_langfuse_without_tools(tmp_path)
    [line] = summarize_issues(
        [Issue(IssueKind.LANGFUSE_NO_TOOL_CALLS, "traces", "1 agent run(s), no tool calls")]
    )

    result = _init(tmp_path, "--yes", "--dry-run")

    assert f"{line.terminal_message} {line.terminal_hint}" in result.stderr


def test_langfuse_without_tool_calls_gives_the_span_filter_hint_once(tmp_path: Path) -> None:
    _write_langfuse_without_tools(tmp_path)

    result = _init(tmp_path, "--yes", "--dry-run")

    assert result.stderr.count("should_export_span") == 1


# End to end


def test_check_runs_on_the_written_configuration(demo_copy: Path) -> None:
    _init(demo_copy, "--yes")

    result = _invoke("check", "--config", str(demo_copy / "detecttrace.yaml"))

    assert result.exit_code == 0


def test_check_notes_the_inactive_example(demo_copy: Path) -> None:
    _init(demo_copy, "--yes")

    result = _invoke("check", "--config", str(demo_copy / "detecttrace.yaml"))

    assert "checklist 'impossible_travel.yaml.example' is inactive" in result.stdout


def test_check_measures_evidence_after_the_example_is_renamed(demo_copy: Path) -> None:
    _init(demo_copy, "--yes")
    folder = demo_copy / "checklists"
    (folder / "impossible_travel.yaml.example").rename(folder / "impossible_travel.yaml")

    _invoke(
        "check",
        "--config",
        str(demo_copy / "detecttrace.yaml"),
        "--json",
        str(demo_copy / "results.json"),
    )

    results = yaml.safe_load((demo_copy / "results.json").read_text(encoding="utf-8"))
    # Not 100%: each case calls only some of the tools the example lists.
    assert results["classes"][0]["overall"]["completeness"]["mean"] == 0.8930817610062893


def test_a_configuration_that_does_not_load_back_exits_2(
    demo_copy: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    def fail(*_args: object) -> None:
        raise RoundTripError("differs")

    monkeypatch.setattr(cli, "check_round_trip", fail)

    result = _init(demo_copy, "--yes", "--dry-run")

    assert result.exit_code == 2
