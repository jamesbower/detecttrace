"""One full run: read the inputs named by the configuration and build the results object."""

from dataclasses import dataclass
from pathlib import Path

from detecttrace.cases import build_trace_cases
from detecttrace.checklist import load_checklists
from detecttrace.join import join_cases
from detecttrace.metrics import MetricsReport, compute_metrics
from detecttrace.model import Issue
from detecttrace.otlp import load_spans
from detecttrace.results import build_results
from detecttrace.runconfig import RunConfig
from detecttrace.summary import JoinCoverage, SummaryLine, summarize_issues
from detecttrace.verdicts import read_verdicts


@dataclass(frozen=True, slots=True)
class RunResult:
    results: dict[str, object]
    issues: list[Issue]
    case_count: int  # joined cases; 0 means nothing could be scored
    report: MetricsReport
    coverage: JoinCoverage
    summary: list[SummaryLine]


def run_check(config: RunConfig, config_path: Path) -> RunResult:
    """Run every stage on the inputs `config` names. Unusable input raises InputFileError.

    `config_path` is the file `config` was loaded from: fix hints name it, and the paths
    recorded in the results are shown relative to its folder, as the user wrote them.
    """
    # Checklists are small and read first, so a typo in one fails before a long trace read.
    checklists = {} if config.checklists is None else load_checklists(config.checklists)
    spans, issues = load_spans(config.traces.path)
    trace_cases, case_issues = build_trace_cases(spans, config.mapping)
    issues.extend(case_issues)
    verdict_rows, verdict_issues = read_verdicts(config.verdicts.path)
    issues.extend(verdict_issues)
    cases, join_issues = join_cases(trace_cases, verdict_rows, config.to_config())
    issues.extend(join_issues)
    report, metric_issues = compute_metrics(cases, checklists)
    issues.extend(metric_issues)

    verdict_case_ids = {row.case_id for row in verdict_rows}
    coverage = JoinCoverage(
        verdicts_matched=len(cases),
        verdicts_total=len(verdict_case_ids),
        traces_matched=len(cases),
        traces_total=len(trace_cases),
    )
    summary = summarize_issues(issues, config_path.name)
    folder = config_path.absolute().parent
    source = {
        "traces": _to_source(config.traces.path, folder),
        "verdicts": _to_source(config.verdicts.path, folder),
        "checklists": None if config.checklists is None else _to_source(config.checklists, folder),
    }
    results = build_results(
        report,
        cases,
        checklists,
        summary,
        coverage,
        source,
        config.dashboard.max_detail_cases,
    )
    return RunResult(results, issues, len(cases), report, coverage, summary)


def _to_source(path: Path, folder: Path) -> str:
    # Paths were joined onto the folder without normalizing, so this recovers what the user
    # wrote (`../data` included) and keeps a user's home folder out of shared results.
    try:
        return path.relative_to(folder).as_posix()
    except ValueError:
        return path.as_posix()
