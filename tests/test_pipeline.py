from pathlib import Path

from builders import RUN_CONFIG, RUN_VERDICTS, write_run_folder

from detecttrace.model import IssueKind
from detecttrace.pipeline import RunResult, run_check
from detecttrace.runconfig import load_run_config
from detecttrace.summary import JoinCoverage


def _run(config_path: Path) -> RunResult:
    return run_check(load_run_config(config_path), config_path)


def test_case_count_is_the_number_of_joined_cases(tmp_path: Path) -> None:
    config_path = write_run_folder(tmp_path, case_ids=("DT-1", "DT-2", "DT-3", "DT-8"))

    result = _run(config_path)

    assert result.case_count == 3


def test_coverage_counts_distinct_case_ids_on_each_side(tmp_path: Path) -> None:
    verdicts = RUN_VERDICTS + "DT-1,impossible_travel,TP\nDT-9,impossible_travel,FP\n"
    config_path = write_run_folder(
        tmp_path, case_ids=("DT-1", "DT-2", "DT-3", "DT-7", "DT-8"), verdicts=verdicts
    )

    result = _run(config_path)

    assert result.coverage == JoinCoverage(
        verdicts_matched=3, verdicts_total=4, traces_matched=3, traces_total=5
    )


def test_results_carry_the_coverage(tmp_path: Path) -> None:
    config_path = write_run_folder(tmp_path, case_ids=("DT-1", "DT-2", "DT-3", "DT-8"))

    result = _run(config_path)

    assert result.results["totals"] == {
        "cases": 3,
        "classes": 1,
        "period": {"first_week": "1970-W01", "last_week": "1970-W01"},
        "versions": ["v1"],
        "coverage": {
            "verdicts_matched": 3,
            "verdicts_total": 3,
            "traces_matched": 3,
            "traces_total": 4,
        },
    }


def test_source_paths_are_relative_to_the_configuration_folder(tmp_path: Path) -> None:
    config_path = write_run_folder(tmp_path)

    result = _run(config_path)

    assert result.results["source"] == {
        "traces": "traces",
        "verdicts": "verdicts.csv",
        "checklists": "checklists",
    }


def test_source_keeps_a_path_outside_the_configuration_folder_as_given(tmp_path: Path) -> None:
    write_run_folder(tmp_path / "data")
    config = RUN_CONFIG.replace("{path: traces}", "{path: ../data/traces}")
    config_path = write_run_folder(tmp_path / "project", config=config)

    result = _run(config_path)

    assert result.results["source"] == {
        "traces": "../data/traces",
        "verdicts": "verdicts.csv",
        "checklists": "checklists",
    }


def test_source_has_no_checklists_when_none_are_configured(tmp_path: Path) -> None:
    config_path = write_run_folder(
        tmp_path, config=RUN_CONFIG.replace("checklists: checklists\n", "")
    )

    result = _run(config_path)

    assert result.results["source"] == {
        "traces": "traces",
        "verdicts": "verdicts.csv",
        "checklists": None,
    }


def test_issues_from_every_stage_are_returned(tmp_path: Path) -> None:
    config_path = write_run_folder(tmp_path, verdicts=RUN_VERDICTS + "DT-9,impossible_travel,FP\n")
    (tmp_path / "traces" / "bad.jsonl").write_text("not json\n", encoding="utf-8")

    result = _run(config_path)

    assert [issue.kind for issue in result.issues] == [
        IssueKind.INVALID_LINE,
        IssueKind.VERDICT_WITHOUT_ROOT,
    ]


def test_summary_lines_name_the_configuration_file(tmp_path: Path) -> None:
    config_path = write_run_folder(tmp_path, verdicts=RUN_VERDICTS + "DT-9,impossible_travel,FP\n")
    renamed = config_path.rename(tmp_path / "prod.yaml")

    result = _run(renamed)

    assert result.summary[0].message == (
        "1 verdict has no matching trace. "
        "Check mapping.case_id in prod.yaml and that the traces cover the same cases."
    )
