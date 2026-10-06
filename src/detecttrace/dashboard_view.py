"""The dashboard view model: the results object turned into display-ready values.

`build_view` does every lookup, label and number format the page needs, so the page only
loops and branches. Labels are raw text, which React escapes; text that comes from the input
(classes, versions, checklist items, paths, data notes) has its control and bidirectional
characters made visible first. Numbers are formatted without the locale, so every machine writes
the same page. Chart geometry is not computed here: the page lays out `TrendView` itself.

`to_view_json` turns the view into JSON-ready data for the React dashboard, whose TypeScript
types `scripts/generate_view_types.py` generates from these dataclasses.
"""

import math
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, fields, is_dataclass, replace
from datetime import date
from typing import Any, Literal

from detecttrace import __version__
from detecttrace.results import SCHEMA_VERSION
from detecttrace.served_page import ServedPage, WaitingCounts
from detecttrace.stats import BOOTSTRAP_RESAMPLES
from detecttrace.summary import (
    CoverageLine,
    JoinCoverage,
    SummaryLine,
    coverage_lines,
    to_message_without_count,
    to_visible_text,
)

# Raised whenever the JSON shape of the view changes, so the React app can refuse a view it
# does not know.
VIEW_VERSION = 2
GENERATOR_PREFIX = "detecttrace"
GENERATOR = f"{GENERATOR_PREFIX} {__version__}"
OFFLINE_GENERATOR_TEXT = f"Written by {GENERATOR}."
OFFLINE_FOOTER_TEXT = (
    "Everything on this page was computed on your machine, and the page makes no network requests."
)
SERVED_GENERATOR_TEXT = f"Written by {GENERATOR} on the server that runs detecttrace serve."
SERVED_FOOTER_TEXT = (
    "Every 30 seconds the page asks that server whether newer results exist, and it makes no "
    "other network requests."
)
UI_GENERATOR_TEXT = f"Written by {GENERATOR}, running locally with detecttrace ui."
UI_FOOTER_TEXT = (
    "Every 30 seconds the page asks the detecttrace ui app on this computer whether newer results "
    "exist; files you upload go only to that app."
)
UI_NEXT_STEP_TEXT = "Upload traces and verdicts on the Data page to get started."
FEW_CASES_BELOW = 10
FEW_CASES_TEXT = "Few cases."
FEW_CASES_LEGEND_TEXT = f"Hollow marker: fewer than {FEW_CASES_BELOW} cases"
NO_CHECKLIST_TEXT = "No checklist for this class."
NO_VERSION_LABEL = "(no version)"
ALL_VERSIONS_LABEL = "All versions"
# Six page-wide version styles, plus fixed ones for the pooled group, no version and the total.
VERSION_STYLES = ("1", "2", "3", "4", "5", "6")
OTHER_STYLE = "other"
NO_VERSION_STYLE = "none"
ALL_STYLE = "all"
RANGE_DASH = "\u2013"
MINUS_SIGN = "\u2212"
MAX_LISTED_CASE_IDS = 3
TP_WITHOUT_AGENT_HINT = (
    "Check that the agent emits a verdict on every case and that its labels are mapped."
)
DROPPED_RESAMPLES_HINT = (
    "The interval for chance-corrected agreement is built from the remaining resamples; more "
    "cases with both verdicts make it steadier."
)

LIMITS = (
    (
        "Why the numbers changed.",
        "A drop after a new version can come from the prompt, a change in the alert mix, or a "
        "log source that stopped. The POC does not separate these.",
    ),
    (
        "Whether the agent's claims match the evidence.",
        "The POC checks which steps ran, not what the results showed.",
    ),
    (
        "When to act.",
        "There is no baseline and no alert. You read the numbers and choose what to do.",
    ),
)

_VERDICT_LABELS = ("true positive", "false positive", "benign")
_TRUE_POSITIVE = 0
_SEVERITY_LABELS = {"invalid_input": "Invalid input", "warning": "Warning"}
_KAPPA_NOTES = {
    "no_cases": "(no cases with both verdicts)",
    "all_same_verdict": "(all cases have the same verdict)",
    "one_side_same_verdict": "(one side always gives the same verdict)",
    "interval_not_available": "No interval (too few usable resamples)",
}
_NO_INTERVAL = "No interval"
_EMPTY_TREND_CELLS = {"agreement": "no cases with both verdicts"}
_NO_BOTH_VERDICTS = "(no cases with both verdicts)"
# The smallest interval strip still visible when an interval is very narrow.
_MIN_STRIP_WIDTH = 0.8
_STRIP_POINT_WIDTH = 1.2


@dataclass(frozen=True, slots=True)
class SourceView:
    name: str
    path: str


@dataclass(frozen=True, slots=True)
class HeaderView:
    title: str
    sources: tuple[SourceView, ...]
    cases_text: str  # "4,210 in 2 alert classes"
    period_text: str  # "2026-W27 to 2026-W38 (12 weeks, UTC)"
    versions_text: str  # page order, then "(no version)"
    low_coverage_text: str | None  # set only when a side of the join is below half
    generator_text: str  # "Written by detecttrace 0.1.0." and, when served, where
    footer_text: str  # which network requests the page makes, offline or served


@dataclass(frozen=True, slots=True)
class StripView:
    """An interval strip on a 0-100 scale: the range bar and the point for the value.

    Percentages map 0-1 onto it; kappa maps -1 to 1.
    """

    range_x: float
    range_width: float
    point_left: float  # the point's mark is drawn from here, `point_width` wide
    point_width: float


@dataclass(frozen=True, slots=True)
class MetricView:
    value: str  # "87%", "0.74" or "n/a"
    interval: str | None  # "72-91%" with an en dash; None when `note` says why there is none
    note: str | None  # why a value or interval is missing
    n_text: str  # "n = 1,030"
    few_note: str | None  # "Few cases." when n is below ten
    strip: StripView | None


@dataclass(frozen=True, slots=True)
class VersionRowView:
    label: str
    style: str
    is_all: bool
    cases_text: str
    few_note: str | None
    completeness: MetricView
    agreement: MetricView
    kappa: MetricView
    dangerous_text: str
    is_dangerous: bool
    tp_without_agent_text: str | None  # "2 true positives with no agent verdict: DT-1, DT-2"


@dataclass(frozen=True, slots=True)
class SkipColumnView:
    label: str
    style: str
    n_text: str  # "n = 820"
    few_note: str | None


@dataclass(frozen=True, slots=True)
class SkipCellView:
    rate_text: str  # "31%"
    count_text: str  # "284 of 910"
    bar_width: float  # 0-100
    few_note: str | None


@dataclass(frozen=True, slots=True)
class SkipRowView:
    item: str
    cells: tuple[SkipCellView, ...]


@dataclass(frozen=True, slots=True)
class SkipTableView:
    steps_text: str  # "5 checklist steps"
    columns: tuple[SkipColumnView, ...]
    rows: tuple[SkipRowView, ...]
    empty_text: str | None  # set, with no rows, when the class has no checklist


@dataclass(frozen=True, slots=True)
class TrendLineView:
    """One series, aligned with `TrendView.weeks`: a None value is a week without cases."""

    style: str
    label: str
    values: tuple[float | None, ...]
    counts: tuple[int, ...]
    few: tuple[bool, ...]


@dataclass(frozen=True, slots=True)
class TrendTableRowView:
    week: str
    cells: tuple[str, ...]  # one per line: "95% (n 170)", "no cases", ...


@dataclass(frozen=True, slots=True)
class TrendMetricView:
    title: str  # "Evidence completeness"
    lines: tuple[TrendLineView, ...]  # all versions first, then the version rows' order
    table_rows: tuple[TrendTableRowView, ...]
    empty_text: str | None  # set, with no lines, when the class has no checklist


@dataclass(frozen=True, slots=True)
class TrendView:
    weeks: tuple[str, ...]  # ISO weeks in label order
    period_text: str  # "2026-W27 to 2026-W38"
    completeness: TrendMetricView
    agreement: TrendMetricView
    version_first_weeks: Mapping[str, str]  # version label -> its first week
    few_legend_text: str  # "Hollow marker: fewer than 10 cases"


@dataclass(frozen=True, slots=True)
class ConfusionCellView:
    count_text: str
    heat: str  # "h-0".."h-4": the cell's share of its row, one ramp for every cell
    is_dangerous: bool
    is_flagged: bool  # a dangerous cell that holds cases


@dataclass(frozen=True, slots=True)
class ConfusionRowView:
    label: str  # "Analyst: true positive"
    cells: tuple[ConfusionCellView, ...]


@dataclass(frozen=True, slots=True)
class ConfusionView:
    n_text: str
    dangerous_text: str  # "30 dangerous false closes"
    column_labels: tuple[str, ...]  # "Agent: true positive", ...
    rows: tuple[ConfusionRowView, ...]


@dataclass(frozen=True, slots=True)
class ClassView:
    anchor: str  # a page-unique id stem, never derived from the class name
    name: str
    cases_text: str  # "2,150 cases"
    versions_text: str  # the row labels, in row order
    pooled_note: str | None
    rows: tuple[VersionRowView, ...]  # all versions, versions, pooled group, no version
    skipped: SkipTableView
    trend: TrendView
    confusion: ConfusionView


@dataclass(frozen=True, slots=True)
class ClassFilterView:
    class_index: int  # the class's index in `case_rows.strings`, as the script filters on
    anchor: str  # the matching ClassView's anchor, so a page can share the selected class
    label: str


@dataclass(frozen=True, slots=True)
class CasesView:
    total_text: str
    detail_sentence: str  # how many cases carry tool calls, with the right plural
    class_filters: tuple[ClassFilterView, ...]


@dataclass(frozen=True, slots=True)
class ExampleView:
    subject: str
    detail: str | None


@dataclass(frozen=True, slots=True)
class NoteView:
    severity: str  # "invalid_input" or "warning"
    severity_label: str
    count_text: str
    message: str  # the sentence after the count
    hint: str
    examples: tuple[ExampleView, ...]
    more_text: str | None  # "3 of 37 shown."


@dataclass(frozen=True, slots=True)
class CoverageView:
    text: str
    hint: str | None  # set only when coverage is low
    is_low: bool


@dataclass(frozen=True, slots=True)
class LimitView:
    term: str
    text: str


@dataclass(frozen=True, slots=True)
class ServedView:
    generation: int
    updated_at: str  # compared with the status route's time, so passed on as the server wrote it
    held_back_text: str | None  # "3 cases still settling are not counted yet."


@dataclass(frozen=True, slots=True)
class CountView:
    label: str
    value: str


@dataclass(frozen=True, slots=True)
class WaitingNoteView:
    message: str
    hint: str


@dataclass(frozen=True, slots=True)
class WaitingView:
    counts: tuple[CountView, ...]
    notes: tuple[WaitingNoteView, ...]
    # Set only in `detecttrace ui`, where the reader supplies the data on the Data page.
    next_step_text: str | None


@dataclass(frozen=True, slots=True)
class DashboardView:
    # "offline" for a page written to a file, "served" from `detecttrace serve`, "ui" from
    # `detecttrace ui`.
    mode: Literal["offline", "served", "ui"]
    header: HeaderView
    classes: tuple[ClassView, ...]
    cases: CasesView
    coverage: tuple[CoverageView, ...]
    notes: tuple[NoteView, ...]
    limits: tuple[LimitView, ...]
    served: ServedView | None  # set only on a page from `detecttrace serve`
    # Set only on the page `detecttrace serve` shows until a case can be scored; every other
    # section is then empty.
    waiting: WaitingView | None


@dataclass(frozen=True, slots=True)
class _Group:
    scope: str  # the trend scope: "version" or "other"
    version: str | None  # None for the pooled group and for no version
    label: str
    style: str
    metrics: Any
    skipped: Any


def build_view(results: Mapping[str, object]) -> DashboardView:
    """Build the view of a results object from `results.build_results` (or its JSON).

    Raises ValueError for a schema version this code does not know.
    """
    if results.get("schema_version") != SCHEMA_VERSION:
        raise ValueError(
            f"unsupported results schema_version {results.get('schema_version')!r}; "
            f"this version of detecttrace reads {SCHEMA_VERSION}"
        )
    # The results are plain JSON-shaped data checked by the writer's schema, not typed records;
    # Any keeps every lookup below from needing a cast.
    data: Any = results
    classes = data["classes"]
    page_versions = _order_page_versions(classes)
    styles = _assign_styles(classes, page_versions)
    has_no_version = any(None in class_data["versions"] for class_data in classes)
    totals = data["totals"]
    return DashboardView(
        mode="offline",
        header=_to_header(data, page_versions, has_no_version),
        classes=tuple(
            _to_class_view(index, class_data, styles) for index, class_data in enumerate(classes)
        ),
        cases=_to_cases_view(data),
        coverage=_to_coverage_views(totals["coverage"], data["source"].get("config")),
        notes=(
            *(to_note_view(note) for note in data["data_notes"]),
            *(note for class_data in classes for note in _to_tp_without_agent_note(class_data)),
            *(note for class_data in classes for note in _to_dropped_notes(class_data, styles)),
        ),
        limits=tuple(LimitView(term, text) for term, text in LIMITS),
        served=None,
        waiting=None,
    )


def build_served_view(
    results: Mapping[str, object],
    served: ServedPage,
    *,
    mode: Literal["served", "ui"] = "served",
) -> DashboardView:
    """The view of a results object for a page from `detecttrace serve` or `detecttrace ui`.

    Raises ValueError for a schema version this code does not know.
    """
    view = build_view(results)
    return replace(
        view,
        mode=mode,
        header=_to_served_header(view.header, mode),
        served=_to_served_view(served),
    )


def build_waiting_view(
    counts: WaitingCounts,
    notes: Sequence[SummaryLine],
    served: ServedPage,
    *,
    mode: Literal["served", "ui"] = "served",
) -> DashboardView:
    """The view `detecttrace serve` or `detecttrace ui` shows until a case can be scored.

    `notes` are the run's issue lines, which often say why nothing joined yet. The counts already
    show the cases still settling, so the served block leaves that text out.
    """
    return DashboardView(
        mode=mode,
        header=_to_served_header(
            HeaderView(
                title="DetectTrace",
                sources=(),
                cases_text="",
                period_text="",
                versions_text="",
                low_coverage_text=None,
                generator_text=OFFLINE_GENERATOR_TEXT,
                footer_text=OFFLINE_FOOTER_TEXT,
            ),
            mode,
        ),
        classes=(),
        cases=CasesView(total_text="", detail_sentence="", class_filters=()),
        coverage=(),
        notes=(),
        limits=(),
        served=replace(_to_served_view(served), held_back_text=None),
        waiting=WaitingView(
            counts=(
                CountView("Spans received", format_count(counts.span_count)),
                CountView("Cases settled", format_count(counts.case_count)),
                CountView("Cases still settling", format_count(counts.held_back_count)),
                CountView("Verdicts received", format_count(counts.verdict_count)),
            ),
            notes=tuple(
                WaitingNoteView(to_visible_text(line.message), to_visible_text(line.hint))
                for line in notes
            ),
            next_step_text=UI_NEXT_STEP_TEXT if mode == "ui" else None,
        ),
    )


def format_held_back_text(count: int) -> str | None:
    """The sentence about cases still inside the settle window, or None when there are none."""
    if count == 0:
        return None
    if count == 1:
        return "1 case still settling is not counted yet."
    return f"{format_count(count)} cases still settling are not counted yet."


def to_view_json(view: DashboardView) -> dict[str, object]:
    """The view as JSON-ready data: `view_version` first, then every field in declaration order.

    Tuples become lists and mappings keep their key order. Raises ValueError for a NaN or
    infinite number, which JSON cannot hold.
    """
    return {"view_version": VIEW_VERSION, **_to_json_object(view)}


def format_percent(value: float) -> str:
    return f"{_to_percent_number(value)}%"


def format_percent_range(low: float, high: float) -> str:
    low_text, high_text = _to_percent_number(low), _to_percent_number(high)
    # "<1" and ">99" next to an en dash read as arithmetic, so such a range is spelled out.
    if not (low_text.isdigit() and high_text.isdigit()):
        return f"{low_text}% to {high_text}%"
    return f"{low_text}{RANGE_DASH}{high_text}%"


def format_kappa(value: float) -> str:
    text = f"{value:.2f}"
    # A kappa just below zero rounds to "-0.00", which reads as a real negative value.
    if text == "-0.00":
        return "0.00"
    # Only a perfect kappa may read as one; 0.996 shown as "1.00" would claim perfect agreement.
    if abs(value) < 1 and text.endswith("1.00"):
        text = text.replace("1.00", "0.99")
    return text.replace("-", MINUS_SIGN)


def format_kappa_range(low: float, high: float) -> str:
    low_text, high_text = format_kappa(low), format_kappa(high)
    # A minus sign right after an en dash is easy to misread, so a negative end uses "to".
    separator = " to " if low_text.startswith(MINUS_SIGN) else RANGE_DASH
    return f"{low_text}{separator}{high_text}"


def format_count(count: int) -> str:
    return f"{count:,}"


def _to_json_object(view: Any) -> dict[str, object]:
    return {field.name: _to_json_value(getattr(view, field.name)) for field in fields(view)}


def _to_json_value(value: object) -> object:
    if is_dataclass(value):
        return _to_json_object(value)
    if isinstance(value, Mapping):
        return {key: _to_json_value(item) for key, item in value.items()}
    if isinstance(value, tuple | list):
        return [_to_json_value(item) for item in value]
    if isinstance(value, float) and not math.isfinite(value):
        raise ValueError(f"the dashboard view holds {value}, which JSON cannot represent")
    if value is None or isinstance(value, str | int | float):
        return value
    raise TypeError(f"the dashboard view holds a {type(value).__name__}, which has no JSON form")


def _to_percent_number(value: float) -> str:
    # Half up, not Python's half-even, so 0.125 reads 13% like a reader rounding by hand.
    whole = math.floor(value * 100 + 0.5)
    # Only an exact 1 or 0 may read as all or none; 299 of 300 is not "100%".
    if whole >= 100 and value < 1:
        return ">99"
    if whole <= 0 and value > 0:
        return "<1"
    return str(whole)


def _order_page_versions(classes: Sequence[Any]) -> list[str]:
    """Real versions by first appearance anywhere: earliest week, then class, then class order."""
    first: dict[str, tuple[str, int, int]] = {}
    for class_index, class_data in enumerate(classes):
        for position, entry in enumerate(class_data["by_version"]):
            version = entry["version"]
            if version is None:
                continue
            key = (entry["first_week"], class_index, position)
            if version not in first or key < first[version]:
                first[version] = key
    return sorted(first, key=lambda version: first[version])


def _assign_styles(classes: Sequence[Any], page_versions: Sequence[str]) -> dict[str, str]:
    """One style per shown version label for the whole page, by first appearance.

    Only labels some class shows on their own take a style; a pooled label never draws one.
    Past six labels a style is reused, never one a class showing this label already uses.
    """
    shown_by_class = [
        {version for version in class_data["shown_versions"] if version is not None}
        for class_data in classes
    ]
    styles: dict[str, str] = {}
    for version in page_versions:
        sharing = [shown for shown in shown_by_class if version in shown]
        if not sharing:
            continue
        if len(styles) < len(VERSION_STYLES):
            styles[version] = VERSION_STYLES[len(styles)]
            continue
        taken = {styles[other] for shown in sharing for other in shown if other in styles}
        # A class shows at most six versions, so a free style exists for any results file
        # this tool writes; the default only keeps a hand-edited file from failing.
        styles[version] = next(
            (style for style in VERSION_STYLES if style not in taken), VERSION_STYLES[0]
        )
    return styles


def _to_header(data: Any, page_versions: Sequence[str], has_no_version: bool) -> HeaderView:
    totals = data["totals"]
    class_count = totals["classes"]
    labels = [to_visible_text(version) for version in page_versions]
    if has_no_version:
        labels.append(NO_VERSION_LABEL)
    return HeaderView(
        title="DetectTrace",
        sources=tuple(
            SourceView(to_visible_text(name), to_visible_text(path))
            for name, path in data["source"].items()
            if path is not None
        ),
        cases_text=f"{format_count(totals['cases'])} in {format_count(class_count)} "
        + ("alert class" if class_count == 1 else "alert classes"),
        period_text=_to_period_text(totals["period"]["first_week"], totals["period"]["last_week"]),
        versions_text=", ".join(labels) if labels else "none",
        low_coverage_text=_to_low_coverage_text(totals["coverage"]),
        generator_text=OFFLINE_GENERATOR_TEXT,
        footer_text=OFFLINE_FOOTER_TEXT,
    )


def _to_served_header(header: HeaderView, mode: Literal["served", "ui"]) -> HeaderView:
    if mode == "ui":
        return replace(header, generator_text=UI_GENERATOR_TEXT, footer_text=UI_FOOTER_TEXT)
    return replace(header, generator_text=SERVED_GENERATOR_TEXT, footer_text=SERVED_FOOTER_TEXT)


def _to_period_text(first_week: str | None, last_week: str | None) -> str:
    if first_week is None or last_week is None:
        return "No cases"
    weeks = (_week_start(last_week) - _week_start(first_week)).days // 7 + 1
    return f"{first_week} to {last_week} ({weeks} {'week' if weeks == 1 else 'weeks'}, UTC)"


def _week_start(week: str) -> date:
    year, number = week.split("-W")
    return date.fromisocalendar(int(year), int(number), 1)


def _to_low_coverage_text(coverage: Any) -> str | None:
    parts = [
        line.share_text
        for line in _to_coverage_lines(coverage, "")
        if line.is_low and line.share_text is not None
    ]
    return f"Low coverage: {', '.join(parts)}." if parts else None


def _to_coverage_lines(coverage: Any, config_text: str) -> list[CoverageLine]:
    join = JoinCoverage(
        coverage["verdicts_matched"],
        coverage["verdicts_total"],
        coverage["traces_matched"],
        coverage["traces_total"],
    )
    return coverage_lines(join, config_text)


def _to_class_anchor(index: int) -> str:
    return f"class-{index}"


def _to_class_view(index: int, class_data: Any, styles: Mapping[str, str]) -> ClassView:
    has_checklist = bool(class_data["checklist_item_ids"])
    groups = _to_groups(class_data, styles)
    overall = class_data["overall"]
    rows = [_to_version_row(ALL_VERSIONS_LABEL, ALL_STYLE, overall, has_checklist, is_all=True)]
    rows.extend(
        _to_version_row(group.label, group.style, group.metrics, has_checklist, is_all=False)
        for group in groups
    )
    return ClassView(
        anchor=_to_class_anchor(index),
        name=to_visible_text(class_data["alert_class"]),
        cases_text=_plural(overall["case_count"], "case", "cases"),
        versions_text=", ".join(group.label for group in groups),
        pooled_note=_to_pooled_note(class_data["other_versions"]),
        rows=tuple(rows),
        skipped=_to_skip_table(class_data, groups),
        trend=_to_trend_view(class_data, groups, has_checklist),
        confusion=_to_confusion_view(overall),
    )


def _to_groups(class_data: Any, styles: Mapping[str, str]) -> list[_Group]:
    """The version rows in page order: shown versions, then the pooled group, then no version."""
    by_version = {entry["version"]: entry for entry in class_data["by_version"]}
    groups = [
        _Group(
            "version",
            version,
            to_visible_text(version),
            styles[version],
            by_version[version]["metrics"],
            by_version[version]["skipped"],
        )
        for version in class_data["shown_versions"]
        if version is not None
    ]
    other = class_data["other"]
    if other is not None:
        groups.append(
            _Group(
                "other",
                None,
                _to_other_label(class_data["other_versions"]),
                OTHER_STYLE,
                other["metrics"],
                other["skipped"],
            )
        )
    if None in class_data["shown_versions"]:
        entry = by_version[None]
        groups.append(
            _Group(
                "version",
                None,
                NO_VERSION_LABEL,
                NO_VERSION_STYLE,
                entry["metrics"],
                entry["skipped"],
            )
        )
    return groups


def _to_other_label(versions: Sequence[str]) -> str:
    return f"(other versions: {', '.join(to_visible_text(version) for version in versions)})"


def _to_pooled_note(versions: Sequence[str]) -> str | None:
    if not versions:
        return None
    names = ", ".join(to_visible_text(version) for version in versions)
    rest = (
        "has the fewest cases here and is pooled."
        if len(versions) == 1
        else "have the fewest cases here and are pooled as one group."
    )
    return f"At most six versions are shown per class, by number of cases. {names} {rest}"


def _to_version_row(
    label: str, style: str, metrics: Any, has_checklist: bool, *, is_all: bool
) -> VersionRowView:
    case_count = metrics["case_count"]
    dangerous = len(metrics["dangerous_false_closes"])
    without_agent = metrics["true_positives_without_agent_verdict"]
    return VersionRowView(
        label=label,
        style=style,
        is_all=is_all,
        cases_text=format_count(case_count),
        few_note=_few_note(case_count),
        completeness=_to_completeness_metric(metrics["completeness"], has_checklist, case_count),
        agreement=_to_agreement_metric(metrics["agreement"]),
        kappa=_to_kappa_metric(metrics["kappa"], metrics["agreement"]["n"]),
        dangerous_text=format_count(dangerous),
        is_dangerous=dangerous > 0,
        tp_without_agent_text=_to_tp_without_agent_text(without_agent),
    )


def _to_tp_without_agent_text(case_ids: Sequence[str]) -> str | None:
    if not case_ids:
        return None
    count = len(case_ids)
    listed = ", ".join(to_visible_text(case_id) for case_id in case_ids[:MAX_LISTED_CASE_IDS])
    rest = count - MAX_LISTED_CASE_IDS
    more = f" and {format_count(rest)} more" if rest > 0 else ""
    noun = _plural(count, "true positive", "true positives")
    return f"{noun} with no agent verdict: {listed}{more}"


def _to_completeness_metric(completeness: Any, has_checklist: bool, case_count: int) -> MetricView:
    if completeness is None:
        note = NO_CHECKLIST_TEXT if not has_checklist else "(no cases)"
        return MetricView("n/a", None, note, _n_text(case_count), _few_note(case_count), None)
    mean, interval, n = completeness["mean"], completeness["interval"], completeness["n"]
    if interval is None:
        why = "No interval (one case)" if n == 1 else "No interval (all cases equal)"
        return MetricView(format_percent(mean), None, why, _n_text(n), _few_note(n), None)
    low, high = interval["low"], interval["high"]
    return MetricView(
        format_percent(mean),
        format_percent_range(low, high),
        None,
        _n_text(n),
        _few_note(n),
        _to_strip(low, high, mean),
    )


def _to_agreement_metric(agreement: Any) -> MetricView:
    rate, interval, n = agreement["rate"], agreement["interval"], agreement["n"]
    if rate is None:
        return MetricView("n/a", None, _NO_BOTH_VERDICTS, _n_text(n), _few_note(n), None)
    if interval is None:
        return MetricView(format_percent(rate), None, "No interval", _n_text(n), _few_note(n), None)
    low, high = interval["low"], interval["high"]
    return MetricView(
        format_percent(rate),
        format_percent_range(low, high),
        None,
        _n_text(n),
        _few_note(n),
        _to_strip(low, high, rate),
    )


def _to_kappa_metric(kappa: Any, n: int) -> MetricView:
    value, interval, note = kappa["value"], kappa["interval"], kappa["note"]
    # A note this version does not know still says the interval is missing, never a bare n/a.
    why = _KAPPA_NOTES.get(note, _NO_INTERVAL) if note is not None else None
    if value is None or interval is None:
        why = why or _NO_INTERVAL
    dropped = kappa["dropped_resamples"]
    if dropped > 0:
        dropped_text = (
            f"{format_count(dropped)} of {format_count(BOOTSTRAP_RESAMPLES)} resamples dropped"
        )
        why = dropped_text if why is None else f"{why}; {dropped_text}"
    if value is None:
        return MetricView("n/a", None, why, _n_text(n), _few_note(n), None)
    if interval is None:
        return MetricView(format_kappa(value), None, why, _n_text(n), _few_note(n), None)
    low, high = interval["low"], interval["high"]
    return MetricView(
        format_kappa(value),
        format_kappa_range(low, high),
        why,
        _n_text(n),
        _few_note(n),
        _to_strip(_to_kappa_scale(low), _to_kappa_scale(high), _to_kappa_scale(value)),
    )


def _to_strip(low: float, high: float, value: float) -> StripView:
    """A strip from values already on the 0-1 scale."""
    low_x, high_x, point_x = _to_scale(low), _to_scale(high), _to_scale(value)
    return StripView(
        range_x=low_x,
        range_width=round(max(high_x - low_x, _MIN_STRIP_WIDTH), 1),
        point_left=round(point_x - _STRIP_POINT_WIDTH / 2, 1),
        point_width=_STRIP_POINT_WIDTH,
    )


def _to_kappa_scale(value: float) -> float:
    return (value + 1) / 2


def _to_scale(value: float) -> float:
    # Clamped so a hand-edited value outside the scale never draws outside the strip.
    return round(min(max(value, 0.0), 1.0) * 100, 1)


def _to_skip_table(class_data: Any, groups: Sequence[_Group]) -> SkipTableView:
    item_ids = class_data["checklist_item_ids"]
    if not item_ids:
        return SkipTableView("No checklist steps", (), (), NO_CHECKLIST_TEXT)
    columns = tuple(
        SkipColumnView(
            label=group.label,
            style=group.style,
            n_text=_n_text(group.metrics["case_count"]),
            few_note=_few_note(group.metrics["case_count"]),
        )
        for group in groups
    )
    rows = tuple(
        SkipRowView(
            item=to_visible_text(item_id),
            cells=tuple(_to_skip_cell(group.skipped[position]) for group in groups),
        )
        for position, item_id in enumerate(item_ids)
    )
    return SkipTableView(
        _plural(len(item_ids), "checklist step", "checklist steps"), columns, rows, None
    )


def _to_skip_cell(rate: Any) -> SkipCellView:
    return SkipCellView(
        rate_text=format_percent(rate["rate"]),
        count_text=f"{format_count(rate['skipped'])} of {format_count(rate['n'])}",
        bar_width=round(min(max(rate["rate"], 0.0), 1.0) * 100, 1),
        few_note=_few_note(rate["n"]),
    )


def _to_trend_view(class_data: Any, groups: Sequence[_Group], has_checklist: bool) -> TrendView:
    points = class_data["trend"]
    weeks = tuple(sorted({point["week"] for point in points}))
    by_key = {(point["scope"], point["version"], point["week"]): point for point in points}
    series: list[tuple[str, str | None, str, str]] = [("all", None, ALL_STYLE, ALL_VERSIONS_LABEL)]
    series.extend((group.scope, group.version, group.style, group.label) for group in groups)
    first_week = {entry["version"]: entry["first_week"] for entry in class_data["by_version"]}
    return TrendView(
        weeks=weeks,
        period_text=f"{weeks[0]} to {weeks[-1]}" if weeks else "No cases",
        completeness=_to_trend_metric(
            "Evidence completeness", "completeness", weeks, series, by_key
        )
        if has_checklist
        else TrendMetricView("Evidence completeness", (), (), NO_CHECKLIST_TEXT),
        agreement=_to_trend_metric("Verdict agreement", "agreement", weeks, series, by_key),
        version_first_weeks={
            to_visible_text(version): first_week[version]
            for version in class_data["shown_versions"]
            if version is not None
        },
        few_legend_text=FEW_CASES_LEGEND_TEXT,
    )


def _to_trend_metric(
    title: str,
    field: str,
    weeks: Sequence[str],
    series: Sequence[tuple[str, str | None, str, str]],
    by_key: Mapping[tuple[str, str | None, str], Any],
) -> TrendMetricView:
    lines = []
    cells_by_line = []
    for scope, version, style, label in series:
        found = [by_key.get((scope, version, week)) for week in weeks]
        values = tuple(None if point is None else point[field] for point in found)
        counts = tuple(0 if point is None else point[f"{field}_n"] for point in found)
        lines.append(
            TrendLineView(
                style=style,
                label=label,
                values=values,
                counts=counts,
                few=tuple(0 < count < FEW_CASES_BELOW for count in counts),
            )
        )
        cells_by_line.append([_to_trend_cell(point, field) for point in found])
    table_rows = tuple(
        TrendTableRowView(week=week, cells=tuple(cells[i] for cells in cells_by_line))
        for i, week in enumerate(weeks)
    )
    return TrendMetricView(title, tuple(lines), table_rows, None)


def _to_trend_cell(point: Any, field: str) -> str:
    if point is None:
        return "no cases"
    value, count = point[field], point[f"{field}_n"]
    if value is None:
        # The week has cases, but none the metric can use.
        return _EMPTY_TREND_CELLS.get(field, "no cases")
    text = f"{format_percent(value)} (n {format_count(count)})"
    return f"{text} {FEW_CASES_TEXT}" if count < FEW_CASES_BELOW else text


def _to_confusion_view(overall: Any) -> ConfusionView:
    matrix = overall["confusion"]
    dangerous = len(overall["dangerous_false_closes"])
    rows = []
    for row_index, row in enumerate(matrix):
        total = sum(row)
        cells = tuple(
            ConfusionCellView(
                count_text=format_count(count),
                heat=_to_heat(count, total),
                is_dangerous=_is_dangerous_cell(row_index, column_index),
                is_flagged=_is_dangerous_cell(row_index, column_index) and count > 0,
            )
            for column_index, count in enumerate(row)
        )
        rows.append(ConfusionRowView(f"Analyst: {_VERDICT_LABELS[row_index]}", cells))
    return ConfusionView(
        n_text=_n_text(sum(sum(row) for row in matrix)),
        dangerous_text=_plural(dangerous, "dangerous false close", "dangerous false closes"),
        column_labels=tuple(f"Agent: {label}" for label in _VERDICT_LABELS),
        rows=tuple(rows),
    )


def _is_dangerous_cell(row_index: int, column_index: int) -> bool:
    return row_index == _TRUE_POSITIVE and column_index != _TRUE_POSITIVE


def _to_heat(count: int, row_total: int) -> str:
    if count == 0:
        return "h-0"
    return f"h-{min(4, 1 + int(count / row_total * 4))}"


def _to_cases_view(data: Any) -> CasesView:
    case_rows = data["case_rows"]
    string_index: dict[str, int] = {}
    for index, text in enumerate(case_rows["strings"]):
        string_index.setdefault(text, index)
    detail_count = len(data["case_detail"])
    return CasesView(
        total_text=format_count(data["totals"]["cases"]),
        detail_sentence=(
            f"Tool calls are included for {_plural(detail_count, 'notable case', 'notable cases')} "
            "(dashboard.max_detail_cases); tool results are never included."
        ),
        # Enumerated in the same order as the class views, so each anchor names its class.
        class_filters=tuple(
            ClassFilterView(
                string_index[class_data["alert_class"]],
                _to_class_anchor(index),
                to_visible_text(class_data["alert_class"]),
            )
            for index, class_data in enumerate(data["classes"])
        ),
    )


def _to_coverage_views(coverage: Any, config_name: str | None) -> tuple[CoverageView, ...]:
    # Results written before the configuration's name was recorded still get a hint.
    config_text = "the configuration" if config_name is None else to_visible_text(config_name)
    return tuple(
        CoverageView(line.sentence, line.raw_hint, line.is_low)
        for line in _to_coverage_lines(coverage, config_text)
    )


def to_note_view(note: Any) -> NoteView:
    count = note["count"]
    examples = tuple(
        ExampleView(
            to_visible_text(example["subject"]),
            None if example["detail"] is None else to_visible_text(example["detail"]),
        )
        for example in note["examples"]
    )
    return NoteView(
        severity=note["severity"],
        severity_label=_SEVERITY_LABELS.get(note["severity"], "Note"),
        count_text=format_count(count),
        message=to_visible_text(to_message_without_count(note["message"], count)),
        hint=to_visible_text(note["hint"]),
        examples=examples,
        more_text=f"{len(examples)} of {format_count(count)} shown."
        if count > len(examples) > 0
        else None,
    )


def _to_tp_without_agent_note(class_data: Any) -> tuple[NoteView, ...]:
    case_ids = class_data["overall"]["true_positives_without_agent_verdict"]
    if not case_ids:
        return ()
    count = len(case_ids)
    name = to_visible_text(class_data["alert_class"])
    message = (
        f"analyst true positive in {name} has no agent verdict."
        if count == 1
        else f"analyst true positives in {name} have no agent verdict."
    )
    examples = tuple(
        ExampleView(to_visible_text(case_id), None) for case_id in case_ids[:MAX_LISTED_CASE_IDS]
    )
    return (_create_warning_note(count, message, TP_WITHOUT_AGENT_HINT, examples),)


def _to_dropped_notes(class_data: Any, styles: Mapping[str, str]) -> tuple[NoteView, ...]:
    """One note per slice whose kappa interval lost resamples: all versions, then the rows."""
    groups = _to_groups(class_data, styles)
    slices = [("all versions", class_data["overall"])]
    slices.extend((group.label, group.metrics) for group in groups)
    name = to_visible_text(class_data["alert_class"])
    return tuple(
        _create_warning_note(
            metrics["kappa"]["dropped_resamples"],
            f"of {format_count(BOOTSTRAP_RESAMPLES)} resamples for chance-corrected agreement in "
            f"{name}, {label}, were dropped because it was undefined in them.",
            DROPPED_RESAMPLES_HINT,
            (),
        )
        for label, metrics in slices
        if metrics["kappa"]["dropped_resamples"] > 0
    )


def _create_warning_note(
    count: int, message: str, hint: str, examples: tuple[ExampleView, ...]
) -> NoteView:
    return NoteView(
        severity="warning",
        severity_label=_SEVERITY_LABELS["warning"],
        count_text=format_count(count),
        message=message,
        hint=hint,
        examples=examples,
        more_text=f"{len(examples)} of {format_count(count)} shown."
        if count > len(examples) > 0
        else None,
    )


def _few_note(n: int) -> str | None:
    return FEW_CASES_TEXT if n < FEW_CASES_BELOW else None


def _n_text(n: int) -> str:
    return f"n = {format_count(n)}"


def _plural(count: int, singular: str, plural: str) -> str:
    return f"{format_count(count)} {singular if count == 1 else plural}"


def _to_served_view(served: ServedPage) -> ServedView:
    return ServedView(
        generation=served.generation,
        updated_at=served.updated_at,
        held_back_text=format_held_back_text(served.held_back_cases),
    )
