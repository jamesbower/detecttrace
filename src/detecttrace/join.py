"""Join trace cases with analyst verdicts (PRD §7.3 joining rules)."""

from collections import defaultdict

from detecttrace.config import Config, normalize_label
from detecttrace.model import (
    UNKNOWN_VERSION,
    Case,
    Issue,
    IssueKind,
    TraceCase,
    Verdict,
    VerdictRow,
)


def join_cases(
    trace_cases: list[TraceCase], verdict_rows: list[VerdictRow], config: Config
) -> tuple[list[Case], list[Issue]]:
    """Return one case per case ID found in both inputs, sorted by case ID."""
    issues: list[Issue] = []
    traces_by_case = {case.case_id: case for case in trace_cases}
    rows_by_case: dict[str, list[VerdictRow]] = defaultdict(list)
    for row in verdict_rows:
        rows_by_case[row.case_id].append(row)
    for case_id in sorted(traces_by_case.keys() - rows_by_case.keys()):
        issues.append(Issue(IssueKind.ROOT_WITHOUT_VERDICT, case_id))
    for case_id in sorted(rows_by_case.keys() - traces_by_case.keys()):
        issues.append(Issue(IssueKind.VERDICT_WITHOUT_ROOT, case_id))
    cases = [
        _join_one(traces_by_case[case_id], rows_by_case[case_id], config, issues)
        for case_id in sorted(traces_by_case.keys() & rows_by_case.keys())
    ]
    return cases, issues


def _join_one(
    trace_case: TraceCase, rows: list[VerdictRow], config: Config, issues: list[Issue]
) -> Case:
    row = rows[0]
    if trace_case.alert_class is not None and normalize_label(
        trace_case.alert_class
    ) != normalize_label(row.alert_class):
        issues.append(
            Issue(
                IssueKind.ALERT_CLASS_CONFLICT,
                row.case_id,
                f"CSV '{row.alert_class}', trace '{trace_case.alert_class}'; using the CSV value",
            )
        )
    classes_by_key: dict[str, str] = {}
    for r in rows:
        classes_by_key.setdefault(normalize_label(r.alert_class), r.alert_class)
    if len(classes_by_key) > 1:
        listed = ", ".join(f"'{alert_class}'" for alert_class in classes_by_key.values())
        issues.append(
            Issue(
                IssueKind.ALERT_CLASS_CONFLICT,
                row.case_id,
                f"CSV rows give {listed}; using the first row's '{row.alert_class}'",
            )
        )
    analyst_verdict = _resolve_analyst_verdict(rows, config, issues)
    agent_verdict = None
    if trace_case.agent_label is None:
        issues.append(Issue(IssueKind.MISSING_AGENT_VERDICT, row.case_id))
    else:
        agent_verdict = config.to_agent_verdict(trace_case.agent_label)
        if agent_verdict is None:
            issues.append(
                Issue(IssueKind.UNMAPPED_AGENT_LABEL, row.case_id, trace_case.agent_label)
            )
    return Case(
        case_id=row.case_id,
        alert_class=row.alert_class,
        prompt_version=trace_case.prompt_version or UNKNOWN_VERSION,
        analyst_verdict=analyst_verdict,
        agent_verdict=agent_verdict,
        start_ns=trace_case.start_ns,
        tool_calls=trace_case.tool_calls,
        is_incomplete_trace=trace_case.is_incomplete_trace,
    )


def _resolve_analyst_verdict(
    rows: list[VerdictRow], config: Config, issues: list[Issue]
) -> Verdict | None:
    """One verdict per case. Rows that disagree leave the ground truth unclear, so none is picked."""
    first = rows[0]
    unmapped_by_key: dict[str, str] = {}
    for r in rows:
        if config.to_analyst_verdict(r.label) is None:
            unmapped_by_key.setdefault(normalize_label(r.label), r.label)
    for label in unmapped_by_key.values():
        issues.append(Issue(IssueKind.UNMAPPED_ANALYST_LABEL, first.case_id, label))
    if len(rows) > 1:
        lines = ", ".join(f"line {r.line_number}: {r.alert_class}/{r.label}" for r in rows)
        # Same verdict means the same mapped verdict ("TP" and "Malicious"), or the same
        # normalized text when a label is unmapped.
        keys = {
            config.to_analyst_verdict(r.label) or f"unmapped:{normalize_label(r.label)}"
            for r in rows
        }
        if len(keys) > 1:
            issues.append(Issue(IssueKind.CONFLICTING_ANALYST_VERDICT, first.case_id, lines))
            return None
        issues.append(Issue(IssueKind.DUPLICATE_VERDICT, first.case_id, lines))
    return config.to_analyst_verdict(first.label)
