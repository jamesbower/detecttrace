import gc
import shutil
from pathlib import Path

import generate
import pytest
from builders import RUN_CONFIG, RUN_VERDICTS, run_trace, write_jsonl, write_run_folder

from detecttrace import pipeline
from detecttrace.dashboard import render_dashboard
from detecttrace.model import IssueKind
from detecttrace.otlp import TraceFileError
from detecttrace.pipeline import RunResult, run_check
from detecttrace.results import write_results_json
from detecttrace.runconfig import load_run_config
from detecttrace.summary import JoinCoverage

DEMO_DIR = generate.REPO_ROOT / "src" / "detecttrace" / "demo_data"


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
            "verdicts_low": False,
            "traces_low": False,
        },
    }


def test_source_paths_are_relative_to_the_configuration_folder(tmp_path: Path) -> None:
    config_path = write_run_folder(tmp_path)

    result = _run(config_path)

    assert result.results["source"] == {
        "traces": "traces",
        "verdicts": "verdicts.csv",
        "checklists": "checklists",
        "config": "detecttrace.yaml",
    }


def test_source_records_only_the_name_of_a_path_outside_the_configuration_folder(
    tmp_path: Path,
) -> None:
    write_run_folder(tmp_path / "data")
    config = RUN_CONFIG.replace("{path: traces}", "{path: ../data/traces}")
    config_path = write_run_folder(tmp_path / "project", config=config)

    result = _run(config_path)

    assert result.results["source"] == {
        "traces": "traces",
        "verdicts": "verdicts.csv",
        "checklists": "checklists",
        "config": "detecttrace.yaml",
    }


def test_source_records_only_the_name_of_an_absolute_path_outside_the_folder(
    tmp_path: Path,
) -> None:
    write_run_folder(tmp_path / "data")
    config = RUN_CONFIG.replace(
        "{path: verdicts.csv}", f"{{path: '{tmp_path / 'data' / 'verdicts.csv'}'}}"
    )
    config_path = write_run_folder(tmp_path / "project", config=config)

    result = _run(config_path)

    assert result.results["source"] == {
        "traces": "traces",
        "verdicts": "verdicts.csv",
        "checklists": "checklists",
        "config": "detecttrace.yaml",
    }


def test_source_records_an_absolute_path_inside_the_folder_relative_to_it(
    tmp_path: Path,
) -> None:
    config = RUN_CONFIG.replace("{path: traces}", f"{{path: '{tmp_path / 'traces'}'}}")
    config_path = write_run_folder(tmp_path, config=config)

    result = _run(config_path)

    assert result.results["source"] == {
        "traces": "traces",
        "verdicts": "verdicts.csv",
        "checklists": "checklists",
        "config": "detecttrace.yaml",
    }


def test_source_records_a_path_that_leaves_and_reenters_the_folder_relative_to_it(
    tmp_path: Path,
) -> None:
    config = RUN_CONFIG.replace("{path: traces}", "{path: ../project/traces}")
    config_path = write_run_folder(tmp_path / "project", config=config)

    result = _run(config_path)

    assert result.results["source"] == {
        "traces": "traces",
        "verdicts": "verdicts.csv",
        "checklists": "checklists",
        "config": "detecttrace.yaml",
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
        "config": "detecttrace.yaml",
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

    assert result.summary[0].hint == (
        "Check mapping.case_id in prod.yaml and that the traces cover the same cases."
    )


def _write_surrogate_run(tmp_path: Path) -> Path:
    config_path = write_run_folder(
        tmp_path, verdicts="case_id,alert_class,verdict\nDT-\ufffd,impossible_travel,TP\n"
    )
    document = run_trace(1, "DT-\ud800")
    tool = document["resourceSpans"][0]["scopeSpans"][0]["spans"][1]
    tool["attributes"].append(
        {"key": "gen_ai.tool.call.arguments", "value": {"stringValue": '{"user":"\ud800"}'}}
    )
    # json.dumps escapes the lone surrogate as "\ud800", as a hostile exporter could.
    write_jsonl(tmp_path / "traces" / "batch.jsonl", [document])
    return config_path


def test_a_lone_surrogate_in_a_case_id_still_joins_its_verdict(tmp_path: Path) -> None:
    result = _run(_write_surrogate_run(tmp_path))

    assert result.case_count == 1


def test_results_with_lone_surrogates_in_the_input_can_be_written(tmp_path: Path) -> None:
    result = _run(_write_surrogate_run(tmp_path))

    write_results_json(result.results, tmp_path / "out.json")

    assert "DT-\ufffd" in (tmp_path / "out.json").read_text(encoding="utf-8")


def test_garbage_collector_is_on_after_a_run(tmp_path: Path) -> None:
    config_path = write_run_folder(tmp_path)

    _run(config_path)

    assert gc.isenabled()


def test_garbage_collector_is_on_after_loading_fails(tmp_path: Path) -> None:
    config_path = write_run_folder(tmp_path)
    shutil.rmtree(tmp_path / "traces")

    with pytest.raises(TraceFileError):
        _run(config_path)

    assert gc.isenabled()


def test_pausing_the_garbage_collector_leaves_the_demo_results_unchanged(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    config_path = DEMO_DIR / "detecttrace.yaml"
    paused = _run(config_path).results
    monkeypatch.setattr(pipeline.gc, "disable", lambda: None)

    unpaused = _run(config_path).results

    assert unpaused == paused


# Very long labels

HUGE = "x" * (1 << 20)


def _write_huge_version_run(tmp_path: Path) -> Path:
    config_path = write_run_folder(
        tmp_path,
        case_ids=("DT-1",),
        verdicts="case_id,alert_class,verdict\nDT-1,impossible_travel,TP\n",
    )
    document = run_trace(1, "DT-1")
    root = document["resourceSpans"][0]["scopeSpans"][0]["spans"][0]
    version = next(a for a in root["attributes"] if a["key"] == "detecttrace.prompt_version")
    version["value"] = {"stringValue": HUGE}
    write_jsonl(tmp_path / "traces" / "batch.jsonl", [document])
    return config_path


def test_a_one_megabyte_version_keeps_the_page_small(tmp_path: Path) -> None:
    result = _run(_write_huge_version_run(tmp_path))

    html = render_dashboard(result.results)

    assert len(html.encode()) < 1 << 20


def test_a_one_megabyte_version_is_reported(tmp_path: Path) -> None:
    result = _run(_write_huge_version_run(tmp_path))

    assert [
        issue.detail for issue in result.issues if issue.kind is IssueKind.INVALID_ATTRIBUTE
    ] == ["detecttrace.prompt_version is longer than 200 characters; shortened"]


def test_a_one_megabyte_case_id_on_both_sides_still_joins(tmp_path: Path) -> None:
    config_path = write_run_folder(
        tmp_path,
        case_ids=(HUGE,),
        verdicts=f"case_id,alert_class,verdict\n{HUGE},impossible_travel,TP\n",
    )

    result = _run(config_path)

    assert result.case_count == 1
