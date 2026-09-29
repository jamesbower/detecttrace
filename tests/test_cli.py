import json
import os
import subprocess
import sys
from pathlib import Path
from typing import Any

import pytest
from builders import RUN_CONFIG, RUN_VERDICTS, write_run_folder
from typer.testing import CliRunner, Result

from detecttrace import __version__, cli, pipeline

EXTRA_VERDICTS = "DT-7,impossible_travel,TP\nDT-8,impossible_travel,FP\nDT-9,impossible_travel,FP\nDT-10,impossible_travel,FP\n"
DEMO_DATA = Path(cli.__file__).parent / cli.DEMO_FOLDER
INTERNAL_ERROR = "detecttrace: internal error (RuntimeError). Please report it.\n"


def _invoke(*args: str, env: dict[str, str] | None = None) -> Result:
    return CliRunner().invoke(cli.app, list(args), env=env)


def _check(config_path: Path, *args: str) -> Result:
    return _invoke("check", "--config", str(config_path), *args)


def _raise_runtime_error(*_args: object) -> None:
    raise RuntimeError("boom")


def test_check_on_a_valid_folder_exits_0(tmp_path: Path) -> None:
    result = _check(write_run_folder(tmp_path))

    assert result.exit_code == 0


def test_check_writes_the_results_next_to_the_configured_output(tmp_path: Path) -> None:
    _check(write_run_folder(tmp_path))

    assert (
        json.loads((tmp_path / "dashboard.json").read_text(encoding="utf-8"))["totals"]["cases"]
        == 3
    )


def test_check_names_the_written_path(tmp_path: Path) -> None:
    result = _check(write_run_folder(tmp_path))

    assert f"Results written to {tmp_path / 'dashboard.json'}." in result.stdout


def test_check_prints_one_line_per_class(tmp_path: Path) -> None:
    result = _check(write_run_folder(tmp_path))

    assert "impossible_travel: 3 cases; versions: v1" in result.stdout


def test_check_ends_with_the_self_reported_note(tmp_path: Path) -> None:
    result = _check(write_run_folder(tmp_path))

    assert result.stdout.endswith("Self-reported. Not verified by DetectTrace.\n")


def test_check_writes_to_out_when_given(tmp_path: Path) -> None:
    config_path = write_run_folder(tmp_path / "run")

    _check(config_path, "--out", str(tmp_path / "custom.json"))

    assert (tmp_path / "custom.json").is_file()


def test_check_reads_detecttrace_yaml_in_the_working_folder_by_default(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    write_run_folder(tmp_path)
    monkeypatch.chdir(tmp_path)

    result = _invoke("check")

    assert result.exit_code == 0


def test_missing_config_exits_1(tmp_path: Path) -> None:
    result = _check(tmp_path / "nope.yaml")

    assert result.exit_code == 1


def test_missing_config_names_the_file(tmp_path: Path) -> None:
    result = _check(tmp_path / "nope.yaml")

    assert "nope.yaml" in result.stderr


def test_errors_reach_stderr_even_when_quiet(tmp_path: Path) -> None:
    result = _check(tmp_path / "nope.yaml", "--quiet")

    assert "nope.yaml" in result.stderr


def test_missing_trace_path_exits_1(tmp_path: Path) -> None:
    config_path = write_run_folder(
        tmp_path, config=RUN_CONFIG.replace("{path: traces}", "{path: gone}")
    )

    result = _check(config_path)

    assert result.exit_code == 1


def test_trace_folder_without_trace_files_exits_1(tmp_path: Path) -> None:
    config_path = write_run_folder(tmp_path)
    (tmp_path / "traces" / "batch.jsonl").unlink()

    result = _check(config_path)

    assert result.exit_code == 1


def test_one_unreadable_trace_file_among_readable_ones_exits_0(tmp_path: Path) -> None:
    config_path = write_run_folder(tmp_path)
    (tmp_path / "traces" / "bad.jsonl").write_text("not json\n", encoding="utf-8")

    result = _check(config_path)

    assert result.exit_code == 0


def test_one_unreadable_trace_file_is_reported(tmp_path: Path) -> None:
    config_path = write_run_folder(tmp_path)
    (tmp_path / "traces" / "bad.jsonl").write_text("not json\n", encoding="utf-8")

    result = _check(config_path)

    assert "1 trace line is not valid JSON and was skipped." in result.stdout


def test_missing_verdict_file_exits_1(tmp_path: Path) -> None:
    config_path = write_run_folder(tmp_path)
    (tmp_path / "verdicts.csv").unlink()

    result = _check(config_path)

    assert result.exit_code == 1


def test_verdict_file_without_required_columns_exits_1(tmp_path: Path) -> None:
    config_path = write_run_folder(tmp_path, verdicts="case_id,label\nDT-1,TP\n")

    result = _check(config_path)

    assert result.exit_code == 1


def test_low_join_coverage_exits_0(tmp_path: Path) -> None:
    config_path = write_run_folder(tmp_path, verdicts=RUN_VERDICTS + EXTRA_VERDICTS)

    result = _check(config_path)

    assert result.exit_code == 0


def test_low_join_coverage_prints_the_warning(tmp_path: Path) -> None:
    config_path = write_run_folder(tmp_path, verdicts=RUN_VERDICTS + EXTRA_VERDICTS)

    result = _check(config_path)

    assert "WARNING: 3 of 7 verdicts matched a trace (42%)." in result.stdout


def test_low_join_coverage_prints_the_hint_after_the_warning(tmp_path: Path) -> None:
    config_path = write_run_folder(tmp_path, verdicts=RUN_VERDICTS + EXTRA_VERDICTS)

    result = _check(config_path)

    assert (
        "so the results may be misleading. Check mapping.case_id in detecttrace.yaml.\n"
        in result.stdout
    )


def test_a_summary_line_prints_its_hint_after_the_message(tmp_path: Path) -> None:
    config_path = write_run_folder(tmp_path, verdicts=RUN_VERDICTS + "DT-9,impossible_travel,FP\n")

    result = _check(config_path)

    assert (
        "1 verdict has no matching trace. Check mapping.case_id in detecttrace.yaml and that "
        "the traces cover the same cases.\n" in result.stdout
    )


def test_invalid_checklist_exits_1(tmp_path: Path) -> None:
    config_path = write_run_folder(tmp_path)
    (tmp_path / "checklists" / "impossible_travel.yaml").write_text("items: [", encoding="utf-8")

    result = _check(config_path)

    assert result.exit_code == 1


def test_a_checklist_folder_without_checklist_files_exits_1(tmp_path: Path) -> None:
    config_path = write_run_folder(tmp_path)
    (tmp_path / "checklists" / "impossible_travel.yaml").unlink()

    result = _check(config_path)

    assert result.exit_code == 1


def test_a_checklist_folder_without_checklist_files_says_what_is_missing(tmp_path: Path) -> None:
    config_path = write_run_folder(tmp_path)
    (tmp_path / "checklists" / "impossible_travel.yaml").unlink()

    result = _check(config_path)

    assert (
        f"No checklist files (*.yaml, *.yml) found under {tmp_path / 'checklists'}. "
        "Check checklists in detecttrace.yaml." in result.stderr
    )


def test_checklists_are_read_before_the_traces(tmp_path: Path) -> None:
    config_path = write_run_folder(
        tmp_path, config=RUN_CONFIG.replace("{path: traces}", "{path: gone}")
    )
    (tmp_path / "checklists" / "impossible_travel.yaml").write_text("items: [", encoding="utf-8")

    result = _check(config_path)

    assert "impossible_travel.yaml" in result.stderr


HEADER_ONLY = "case_id,alert_class,verdict\n"


def test_a_verdict_file_without_rows_exits_1(tmp_path: Path) -> None:
    result = _check(write_run_folder(tmp_path, verdicts=HEADER_ONLY))

    assert result.exit_code == 1


def test_a_verdict_file_without_rows_says_so(tmp_path: Path) -> None:
    result = _check(write_run_folder(tmp_path, verdicts=HEADER_ONLY))

    assert "Error: The verdict file has no rows. Nothing was written.\n" in result.stderr


def test_a_verdict_file_without_rows_gives_no_mapping_hint(tmp_path: Path) -> None:
    result = _check(write_run_folder(tmp_path, verdicts=HEADER_ONLY))

    assert "mapping" not in result.stderr


def test_a_verdict_file_with_only_unreadable_rows_prints_the_summary(tmp_path: Path) -> None:
    result = _check(write_run_folder(tmp_path, verdicts=HEADER_ONLY + ",,\n"))

    assert "1 verdict row could not be read and was skipped." in result.stderr


def test_no_joined_case_exits_1(tmp_path: Path) -> None:
    config_path = write_run_folder(tmp_path, case_ids=("DT-7", "DT-8"))

    result = _check(config_path)

    assert result.exit_code == 1


def test_no_joined_case_says_no_case_could_be_scored(tmp_path: Path) -> None:
    config_path = write_run_folder(tmp_path, case_ids=("DT-7", "DT-8"))

    result = _check(config_path)

    assert "No case could be scored" in result.stderr


def test_no_joined_case_prints_the_summary(tmp_path: Path) -> None:
    config_path = write_run_folder(tmp_path, case_ids=("DT-7", "DT-8"))

    result = _check(config_path)

    assert "WARNING: 0 of 3 verdicts matched a trace (0%)." in result.stderr


def test_no_joined_case_writes_nothing(tmp_path: Path) -> None:
    config_path = write_run_folder(tmp_path, case_ids=("DT-7", "DT-8"))

    _check(config_path)

    assert not (tmp_path / "dashboard.json").exists()


def test_invalid_input_without_strict_exits_0(tmp_path: Path) -> None:
    config_path = write_run_folder(tmp_path)
    (tmp_path / "traces" / "bad.jsonl").write_text("not json\n", encoding="utf-8")

    result = _check(config_path)

    assert result.exit_code == 0


def test_invalid_input_with_strict_exits_1(tmp_path: Path) -> None:
    config_path = write_run_folder(tmp_path)
    (tmp_path / "traces" / "bad.jsonl").write_text("not json\n", encoding="utf-8")

    result = _check(config_path, "--strict")

    assert result.exit_code == 1


def test_warnings_only_with_strict_exits_0(tmp_path: Path) -> None:
    config_path = write_run_folder(tmp_path, verdicts=RUN_VERDICTS + EXTRA_VERDICTS)

    result = _check(config_path, "--strict")

    assert result.exit_code == 0


def test_unexpected_exception_exits_2(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(pipeline, "compute_metrics", _raise_runtime_error)

    result = _check(write_run_folder(tmp_path))

    assert result.exit_code == 2


def test_unexpected_exception_prints_one_line_without_traceback(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(pipeline, "compute_metrics", _raise_runtime_error)

    result = _check(write_run_folder(tmp_path))

    assert result.stderr == INTERNAL_ERROR


def test_unexpected_exception_prints_the_traceback_in_debug_mode(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(pipeline, "compute_metrics", _raise_runtime_error)
    config_path = write_run_folder(tmp_path)

    result = _invoke("check", "--config", str(config_path), env={"DETECTTRACE_DEBUG": "1"})

    assert "Traceback (most recent call last)" in result.stderr


def test_quiet_success_prints_nothing(tmp_path: Path) -> None:
    result = _check(write_run_folder(tmp_path), "--quiet")

    assert result.stdout == ""


def test_out_into_a_missing_folder_exits_1(tmp_path: Path) -> None:
    config_path = write_run_folder(tmp_path)

    result = _check(config_path, "--out", str(tmp_path / "missing" / "results.json"))

    assert result.exit_code == 1


def test_out_into_a_missing_folder_names_the_folder(tmp_path: Path) -> None:
    config_path = write_run_folder(tmp_path)

    result = _check(config_path, "--out", str(tmp_path / "missing" / "results.json"))

    assert f"Output folder not found: {tmp_path / 'missing'}." in result.stderr


def test_out_into_a_missing_folder_does_not_create_it(tmp_path: Path) -> None:
    config_path = write_run_folder(tmp_path)

    _check(config_path, "--out", str(tmp_path / "missing" / "results.json"))

    assert not (tmp_path / "missing").exists()


def test_demo_writes_to_the_working_folder_by_default(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.chdir(tmp_path)

    _invoke("demo", "--quiet")

    assert (tmp_path / "detecttrace-demo.json").is_file()


def test_demo_with_the_default_output_exits_0(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.chdir(tmp_path)

    result = _invoke("demo", "--quiet")

    assert result.exit_code == 0


def test_demo_writes_nothing_into_the_package(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    before = sorted(DEMO_DATA.rglob("*"))
    monkeypatch.chdir(tmp_path)

    _invoke("demo", "--quiet")

    assert sorted(DEMO_DATA.rglob("*")) == before


def test_demo_with_out_and_quiet_prints_nothing(tmp_path: Path) -> None:
    result = _invoke("demo", "--out", str(tmp_path / "demo.json"), "--quiet")

    assert result.stdout == ""


def test_config_caps_the_detail_cases(tmp_path: Path) -> None:
    config = RUN_CONFIG + "dashboard: {max_detail_cases: 1}\n"
    config_path = write_run_folder(tmp_path, config=config)

    _check(config_path)

    assert (
        len(json.loads((tmp_path / "dashboard.json").read_text(encoding="utf-8"))["case_detail"])
        == 1
    )


def test_a_fully_matched_check_prints_the_coverage(tmp_path: Path) -> None:
    result = _check(write_run_folder(tmp_path))

    assert "3 of 3 verdicts matched a trace (100%).\n" in result.stdout


def test_demo_without_bundled_data_exits_1(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(cli, "DEMO_FOLDER", "no_such_demo_data")

    result = _invoke("demo")

    assert result.exit_code == 1


def test_demo_without_bundled_data_says_it_is_missing(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(cli, "DEMO_FOLDER", "no_such_demo_data")

    result = _invoke("demo")

    assert "demo data is missing from this installation" in result.stderr


def test_version_prints_the_package_version() -> None:
    result = _invoke("--version")

    assert result.stdout == f"detecttrace {__version__}\n"


def test_python_m_detecttrace_prints_the_version() -> None:
    completed = subprocess.run(
        [sys.executable, "-m", "detecttrace", "--version"],
        capture_output=True,
        text=True,
        check=False,
    )

    assert completed.stdout == f"detecttrace {__version__}\n"


# Untrusted text on the terminal

OSC_52 = "\x1b]52;c;ZXZpbA==\x07"


def _check_with_label(tmp_path: Path, label: str) -> Result:
    verdicts = RUN_VERDICTS.replace("DT-1,impossible_travel,TP", f"DT-1,impossible_travel,{label}")
    return _check(write_run_folder(tmp_path, verdicts=verdicts))


def test_a_label_with_an_erase_sequence_is_printed_escaped(tmp_path: Path) -> None:
    result = _check_with_label(tmp_path, "X\x1b[2K")

    assert "the label 'X\\x1b[2K'" in result.stdout


def test_a_label_with_an_osc_52_sequence_is_printed_escaped(tmp_path: Path) -> None:
    result = _check_with_label(tmp_path, OSC_52)

    assert "the label '\\x1b]52;c;ZXZpbA==\\x07'" in result.stdout


def test_no_raw_escape_character_reaches_stdout(tmp_path: Path) -> None:
    result = _check_with_label(tmp_path, OSC_52)

    assert "\x1b" not in result.stdout


def test_a_1_mb_agent_label_is_shortened_on_the_terminal(tmp_path: Path) -> None:
    config_path = write_run_folder(tmp_path)
    traces = tmp_path / "traces" / "batch.jsonl"
    text = traces.read_text(encoding="utf-8")
    traces.write_text(text.replace('"Benign"', '"' + "x" * 1_000_000 + '"'), encoding="utf-8")

    result = _check(config_path)

    assert len(result.stdout) < 5_000


def test_a_class_name_with_an_escape_sequence_is_printed_escaped(tmp_path: Path) -> None:
    verdicts = RUN_VERDICTS.replace("impossible_travel", "travel\x1b[2K")

    result = _check(write_run_folder(tmp_path, verdicts=verdicts))

    assert "travel\\x1b[2K: 3 cases" in result.stdout


def test_an_output_path_with_an_escape_sequence_is_printed_escaped(tmp_path: Path) -> None:
    config_path = write_run_folder(tmp_path)

    result = _check(config_path, "--out", str(tmp_path / "gone\x1b[2K" / "out.json"))

    assert "gone\\x1b[2K" in result.stderr


def test_a_version_with_an_escape_sequence_is_printed_escaped(tmp_path: Path) -> None:
    config_path = write_run_folder(tmp_path)
    traces = tmp_path / "traces" / "batch.jsonl"
    text = traces.read_text(encoding="utf-8")
    traces.write_text(text.replace('"v1"', json.dumps("v\x1b[2K")), encoding="utf-8")

    result = _check(config_path)

    assert "versions: v\\x1b[2K" in result.stdout


def test_a_100_kb_class_name_is_shortened_on_the_terminal(tmp_path: Path) -> None:
    # Just under the CSV reader's field limit, so the name reaches the per-class line.
    verdicts = RUN_VERDICTS.replace("impossible_travel", "x" * 100_000)

    result = _check(write_run_folder(tmp_path, verdicts=verdicts))

    assert len(result.stdout) < 5_000


# Replacing an existing output file

NOT_OURS = "{} exists and wasn't written by detecttrace; delete it or choose another path."


def test_out_naming_a_file_from_another_tool_exits_1(tmp_path: Path) -> None:
    config_path = write_run_folder(tmp_path)
    (tmp_path / "mine.json").write_text("my data", encoding="utf-8")

    result = _check(config_path, "--out", str(tmp_path / "mine.json"))

    assert result.exit_code == 1


def test_out_naming_a_file_from_another_tool_says_why(tmp_path: Path) -> None:
    config_path = write_run_folder(tmp_path)
    (tmp_path / "mine.json").write_text("my data", encoding="utf-8")

    result = _check(config_path, "--out", str(tmp_path / "mine.json"))

    assert NOT_OURS.format(tmp_path / "mine.json") in result.stderr


def test_out_naming_a_file_from_another_tool_leaves_it_alone(tmp_path: Path) -> None:
    config_path = write_run_folder(tmp_path)
    (tmp_path / "mine.json").write_text("my data", encoding="utf-8")

    _check(config_path, "--out", str(tmp_path / "mine.json"))

    assert (tmp_path / "mine.json").read_text(encoding="utf-8") == "my data"


def test_the_configured_output_is_protected_too(tmp_path: Path) -> None:
    config_path = write_run_folder(tmp_path)
    (tmp_path / "dashboard.json").write_text("{}", encoding="utf-8")

    result = _check(config_path)

    assert NOT_OURS.format(tmp_path / "dashboard.json") in result.stderr


def test_an_existing_folder_at_the_output_path_is_protected(tmp_path: Path) -> None:
    config_path = write_run_folder(tmp_path)
    (tmp_path / "dashboard.json").mkdir()

    result = _check(config_path)

    assert result.exit_code == 1


def test_a_folder_at_the_output_path_is_named_as_a_folder(tmp_path: Path) -> None:
    config_path = write_run_folder(tmp_path)
    (tmp_path / "dashboard.json").mkdir()

    result = _check(config_path)

    assert f"{tmp_path / 'dashboard.json'} is a folder; choose another path." in result.stderr


def test_a_broken_link_at_the_output_path_exits_1(tmp_path: Path) -> None:
    config_path = write_run_folder(tmp_path)
    (tmp_path / "dashboard.json").symlink_to(tmp_path / "gone.json")

    result = _check(config_path)

    assert result.exit_code == 1


def test_a_broken_link_at_the_output_path_is_named_as_one(tmp_path: Path) -> None:
    config_path = write_run_folder(tmp_path)
    (tmp_path / "dashboard.json").symlink_to(tmp_path / "gone.json")

    result = _check(config_path)

    assert (
        f"{tmp_path / 'dashboard.json'} is a broken link; delete it or choose another path."
        in result.stderr
    )


def test_a_broken_link_at_the_output_path_is_left_in_place(tmp_path: Path) -> None:
    config_path = write_run_folder(tmp_path)
    (tmp_path / "dashboard.json").symlink_to(tmp_path / "gone.json")

    _check(config_path)

    assert (tmp_path / "dashboard.json").is_symlink()


@pytest.mark.skipif(
    sys.platform == "win32" or os.geteuid() == 0, reason="needs POSIX permissions as non-root"
)
def test_an_unreadable_output_file_is_reported_as_unreadable(tmp_path: Path) -> None:
    config_path = write_run_folder(tmp_path)
    (tmp_path / "dashboard.json").write_text("{}", encoding="utf-8")
    (tmp_path / "dashboard.json").chmod(0)

    result = _check(config_path)

    assert (
        f"Could not read {tmp_path / 'dashboard.json'} to check it before replacing it: "
        "Permission denied." in result.stderr
    )


def test_the_output_is_checked_again_just_before_writing(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    config_path = write_run_folder(tmp_path)
    target = tmp_path / "dashboard.json"
    real_run_check = cli.run_check

    def run_then_create_a_file(*args: Any) -> pipeline.RunResult:
        result = real_run_check(*args)
        target.write_text("my data", encoding="utf-8")
        return result

    monkeypatch.setattr(cli, "run_check", run_then_create_a_file)

    _check(config_path)

    assert target.read_text(encoding="utf-8") == "my data"


def test_the_output_is_checked_before_the_run(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(pipeline, "compute_metrics", _raise_runtime_error)
    config_path = write_run_folder(tmp_path)
    (tmp_path / "dashboard.json").write_text("{}", encoding="utf-8")

    result = _check(config_path)

    assert result.exit_code == 1


def test_an_earlier_results_file_is_replaced(tmp_path: Path) -> None:
    config_path = write_run_folder(tmp_path)
    _check(config_path)

    result = _check(config_path)

    assert result.exit_code == 0


def test_demo_protects_a_file_from_another_tool(tmp_path: Path) -> None:
    (tmp_path / "mine.json").write_text("my data", encoding="utf-8")

    result = _invoke("demo", "--out", str(tmp_path / "mine.json"))

    assert result.exit_code == 1


# Examples with details


def test_an_example_shows_its_subject_and_detail(tmp_path: Path) -> None:
    config_path = write_run_folder(tmp_path)
    (tmp_path / "traces" / "bad.jsonl").write_text("not json\n", encoding="utf-8")

    result = _check(config_path)

    assert "\n    bad.jsonl: line 1\n" in result.stdout


def test_an_example_detail_is_escaped(tmp_path: Path) -> None:
    config_path = write_run_folder(tmp_path)
    (tmp_path / "traces" / "bad.jsonl").write_text("{}\n", encoding="utf-8")
    (tmp_path / "traces" / "b\x1bd.jsonl").write_text("not json\n", encoding="utf-8")

    result = _check(config_path)

    assert "\n    b\\x1bd.jsonl: line 1\n" in result.stdout


def test_an_example_for_a_label_shows_only_the_subject(tmp_path: Path) -> None:
    result = _check_with_label(tmp_path, "Escalated")

    assert "\n    DT-1\n" in result.stdout


# --quiet with --strict


def test_quiet_strict_with_invalid_input_prints_the_invalid_input_lines(tmp_path: Path) -> None:
    config_path = write_run_folder(tmp_path)
    (tmp_path / "traces" / "bad.jsonl").write_text("not json\n", encoding="utf-8")

    result = _check(config_path, "--quiet", "--strict")

    assert "1 trace line is not valid JSON and was skipped." in result.stderr


def test_quiet_strict_with_invalid_input_leaves_out_warnings(tmp_path: Path) -> None:
    config_path = write_run_folder(tmp_path, verdicts=RUN_VERDICTS + "DT-9,impossible_travel,FP\n")
    (tmp_path / "traces" / "bad.jsonl").write_text("not json\n", encoding="utf-8")

    result = _check(config_path, "--quiet", "--strict")

    assert "verdict has no matching trace" not in result.stderr


def test_quiet_strict_with_invalid_input_prints_nothing_to_stdout(tmp_path: Path) -> None:
    config_path = write_run_folder(tmp_path)
    (tmp_path / "traces" / "bad.jsonl").write_text("not json\n", encoding="utf-8")

    result = _check(config_path, "--quiet", "--strict")

    assert result.stdout == ""


# Class lines


def _eight_version_folder(tmp_path: Path, versions: tuple[str, ...]) -> Path:
    case_ids = tuple(f"DT-{number}" for number in range(1, len(versions) + 1))
    verdicts = "case_id,alert_class,verdict\n" + "".join(
        f"{case_id},impossible_travel,TP\n" for case_id in case_ids
    )
    config_path = write_run_folder(tmp_path, case_ids=case_ids, verdicts=verdicts)
    traces = tmp_path / "traces" / "batch.jsonl"
    lines = traces.read_text(encoding="utf-8").splitlines(keepends=True)
    traces.write_text(
        "".join(
            line.replace('"v1"', f'"{version}"')
            for line, version in zip(lines, versions, strict=True)
        ),
        encoding="utf-8",
    )
    return config_path


def test_one_pooled_version_is_counted_in_the_singular(tmp_path: Path) -> None:
    config_path = _eight_version_folder(tmp_path, ("v1", "v2", "v3", "v4", "v5", "v6", "v7"))

    result = _check(config_path)

    assert "v6, 1 other version\n" in result.stdout


def test_several_pooled_versions_are_counted_in_the_plural(tmp_path: Path) -> None:
    config_path = _eight_version_folder(tmp_path, ("v1", "v2", "v3", "v4", "v5", "v6", "v7", "v8"))

    result = _check(config_path)

    assert "v6, 2 other versions\n" in result.stdout


# Usage errors


def test_an_unknown_option_exits_1() -> None:
    assert _invoke("--bogus").exit_code == 1


def test_an_unknown_command_option_exits_1(tmp_path: Path) -> None:
    assert _invoke("check", "--bogus").exit_code == 1


def test_an_unknown_command_exits_1() -> None:
    assert _invoke("bogus").exit_code == 1


def test_no_command_exits_1() -> None:
    assert _invoke().exit_code == 1


def test_an_unknown_option_names_the_option() -> None:
    assert "No such option" in _invoke("--bogus").output


def test_help_still_exits_0() -> None:
    assert _invoke("--help").exit_code == 0


def test_an_option_without_its_value_exits_1() -> None:
    assert _invoke("check", "--config").exit_code == 1


def test_version_exits_0() -> None:
    assert _invoke("--version").exit_code == 0


# Nothing written


def test_no_joined_case_says_nothing_was_written(tmp_path: Path) -> None:
    config_path = write_run_folder(tmp_path, case_ids=("DT-7", "DT-8"))

    result = _check(config_path)

    assert "Nothing was written." in result.stderr


def test_unusable_input_says_nothing_was_written(tmp_path: Path) -> None:
    config_path = write_run_folder(tmp_path)
    (tmp_path / "verdicts.csv").unlink()

    result = _check(config_path)

    assert "Nothing was written." in result.stderr


def test_a_failed_write_says_nothing_was_written(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(cli, "write_results_json", _raise_permission_error)

    result = _check(write_run_folder(tmp_path))

    assert "Nothing was written." in result.stderr


def test_a_failed_write_exits_1(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(cli, "write_results_json", _raise_permission_error)

    result = _check(write_run_folder(tmp_path))

    assert result.exit_code == 1


def _raise_permission_error(*_args: object) -> None:
    raise PermissionError(13, "Permission denied")
