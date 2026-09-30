"""One full run: read the inputs named by the configuration and build the results object."""

import gc
import os
from dataclasses import dataclass
from pathlib import Path

from detecttrace.cases import build_trace_cases
from detecttrace.checklist import ChecklistFileError, load_checklists
from detecttrace.join import join_cases
from detecttrace.metrics import MetricsReport, compute_metrics
from detecttrace.model import Issue, IssueKind
from detecttrace.results import build_results
from detecttrace.runconfig import RunConfig
from detecttrace.summary import JoinCoverage, SummaryLine, summarize_issues
from detecttrace.traces import load_spans
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
    recorded in the results are relative to its folder, or just a name when outside it.
    """
    # Checklists are small and read first, so a typo in one fails before a long trace read.
    checklists = {} if config.checklists is None else load_checklists(config.checklists)
    if config.checklists is not None and not checklists:
        # Otherwise a wrong folder would score every class as having no checklist, silently.
        raise ChecklistFileError(
            f"No checklist files (*.yaml, *.yml) found under {config.checklists}. "
            f"Check checklists in {config_path.name}."
        )
    # Loading allocates millions of small objects that all live until the run ends, so the
    # collector's repeated scans of them find nothing to free; pausing it cut the
    # 50,000-case run by about a third for a few percent more peak memory.
    was_enabled = gc.isenabled()
    gc.disable()
    try:
        spans, issues = load_spans(config.traces.path, format=config.traces.format)
        trace_cases, case_issues = build_trace_cases(spans, config.mapping)
    finally:
        if was_enabled:
            gc.enable()
    issues.extend(case_issues)
    if (
        config.traces.format == "langfuse"
        and trace_cases
        and not any(case.tool_calls for case in trace_cases)
    ):
        # The Langfuse SDK's default span filter can drop tool spans before they reach
        # Langfuse, which would show as low evidence completeness with no other sign.
        issues.append(
            Issue(
                IssueKind.LANGFUSE_NO_TOOL_CALLS,
                config.traces.path.name,
                f"{len(trace_cases):,} agent run(s), no tool calls",
            )
        )
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
        # Only the name: fix hints on the page point at the file without revealing its folder.
        "config": config_path.name,
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
    # Results get shared, so a path outside the folder is cut to its name: an absolute or
    # `../` path could reveal a user name or folder layout. normpath, not resolve(), so a
    # symlinked folder keeps the name the user wrote.
    normalized = Path(os.path.normpath(path))
    try:
        return normalized.relative_to(os.path.normpath(folder)).as_posix()
    except ValueError:
        return normalized.name
