import json
import subprocess
import sys
from pathlib import Path

import pytest
from builders import RUN_CONFIG, RUN_VERDICTS, write_run_folder
from typer.testing import CliRunner, Result

from detecttrace import __version__, cli, pipeline

EXTRA_VERDICTS = "DT-7,impossible_travel,TP\nDT-8,impossible_travel,FP\nDT-9,impossible_travel,FP\nDT-10,impossible_travel,FP\n"
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


def test_summary_examples_are_indented_under_their_line(tmp_path: Path) -> None:
    config_path = write_run_folder(tmp_path)
    (tmp_path / "traces" / "bad.jsonl").write_text("not json\n", encoding="utf-8")

    result = _check(config_path)

    assert "\n    bad.jsonl\n" in result.stdout


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


def test_invalid_checklist_exits_1(tmp_path: Path) -> None:
    config_path = write_run_folder(tmp_path)
    (tmp_path / "checklists" / "impossible_travel.yaml").write_text("items: [", encoding="utf-8")

    result = _check(config_path)

    assert result.exit_code == 1


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
