import json
from pathlib import Path
from typing import Any

import pytest

from detecttrace.dashboard_view import build_view, format_kappa, format_percent

DEMO_GOLDEN = Path(__file__).parent / "fixtures" / "demo" / "expected.json"
UNSET: Any = object()


def interval(low: float, high: float) -> dict[str, float]:
    return {"low": low, "high": high}


def completeness(mean: float = 0.87, bounds: Any = UNSET, n: int = 15) -> dict[str, object]:
    return {
        "mean": mean,
        "interval": interval(0.72, 0.91) if bounds is UNSET else bounds,
        "n": n,
        "method": "t",
    }


def metrics(
    *,
    case_count: int = 15,
    rate: float | None = 0.8667,
    n: int = 15,
    kappa_value: float | None = 0.7412,
    kappa_interval: Any = UNSET,
    kappa_note: str | None = None,
    completeness_data: Any = UNSET,
    confusion: Any = UNSET,
) -> dict[str, object]:
    return {
        "case_count": case_count,
        "agreement": {
            "agreed": 13,
            "n": n,
            "rate": rate,
            "interval": None if rate is None else interval(0.62, 0.96),
        },
        "kappa": {
            "value": kappa_value,
            "interval": interval(0.62, 0.86) if kappa_interval is UNSET else kappa_interval,
            "method": "analytic",
            "note": kappa_note,
            "dropped_resamples": 0,
        },
        "confusion": [[5, 1, 1], [0, 5, 0], [0, 0, 3]] if confusion is UNSET else confusion,
        "dangerous_false_closes": ["C-1", "C-2"],
        "true_positives_without_agent_verdict": [],
        "completeness": completeness() if completeness_data is UNSET else completeness_data,
    }


def skipped(items: tuple[str, ...], n: int = 15) -> list[dict[str, object]]:
    return [{"item_id": item, "skipped": 3, "n": n, "rate": 3 / n} for item in items]


def version_entry(
    version: str | None,
    first_week: str = "2026-W10",
    slice_data: Any = UNSET,
    items: tuple[str, ...] = ("signin",),
) -> dict[str, object]:
    return {
        "version": version,
        "first_week": first_week,
        "metrics": metrics() if slice_data is UNSET else slice_data,
        "skipped": skipped(items),
    }


def class_data(
    alert_class: str = "impossible_travel",
    entries: Any = UNSET,
    shown: Any = UNSET,
    other_versions: tuple[str, ...] = (),
    items: tuple[str, ...] = ("signin",),
    overall: Any = UNSET,
    trend: Any = UNSET,
) -> dict[str, object]:
    by_version = [version_entry("v1", items=items)] if entries is UNSET else entries
    versions = [entry["version"] for entry in by_version]
    return {
        "alert_class": alert_class,
        "checklist_item_ids": list(items),
        "versions": versions,
        "shown_versions": versions if shown is UNSET else list(shown),
        "other_versions": list(other_versions),
        "overall": metrics() if overall is UNSET else overall,
        "skipped_overall": skipped(items),
        "by_version": by_version,
        "other": {"metrics": metrics(case_count=8), "skipped": skipped(items, 8)}
        if other_versions
        else None,
        "trend": [] if trend is UNSET else trend,
    }


def coverage(traces_matched: int = 100, verdicts_matched: int = 100) -> dict[str, object]:
    return {
        "verdicts_matched": verdicts_matched,
        "verdicts_total": 100,
        "traces_matched": traces_matched,
        "traces_total": 100,
        "verdicts_low": verdicts_matched * 2 < 100,
        "traces_low": traces_matched * 2 < 100,
    }


def results(
    classes: Any = UNSET,
    *,
    schema_version: int = 1,
    cases: int = 15,
    join: Any = UNSET,
    notes: Any = (),
    case_detail: Any = (),
    period: tuple[str | None, str | None] = ("2026-W10", "2026-W12"),
    source: Any = UNSET,
) -> dict[str, object]:
    class_list = [class_data()] if classes is UNSET else classes
    return {
        "schema_version": schema_version,
        "generated_by": "detecttrace test",
        "source": {
            "traces": "traces",
            "verdicts": "verdicts.csv",
            "checklists": None,
            "config": "detecttrace.yaml",
        }
        if source is UNSET
        else source,
        "totals": {
            "cases": cases,
            "classes": len(class_list),
            "period": {"first_week": period[0], "last_week": period[1]},
            "versions": [],
            "coverage": coverage() if join is UNSET else join,
        },
        "classes": class_list,
        "data_notes": list(notes),
        "case_rows": {
            "verdict_codes": ["true_positive", "false_positive", "benign"],
            "unknown_verdict_code": -1,
            "checklists": [],
            "strings": ["2026-W10", *(entry["alert_class"] for entry in class_list)],
            "columns": {},
        },
        "case_detail": list(case_detail),
    }


def trend_point(
    week: str, scope: str, version: str | None, value: float, n: int
) -> dict[str, object]:
    return {
        "week": week,
        "scope": scope,
        "version": version,
        "completeness": value,
        "completeness_n": n,
        "agreement": value,
        "agreement_n": n,
    }


def first_version_row(data: dict[str, object]) -> Any:
    return build_view(data).classes[0].rows[1]


def row_labels(data: dict[str, object]) -> list[str]:
    return [row.label for row in build_view(data).classes[0].rows]


# Number formats


def test_a_rate_shows_as_a_whole_percent() -> None:
    assert first_version_row(results()).agreement.value == "87%"


def test_a_value_with_fifteen_cases_is_not_muted() -> None:
    assert first_version_row(results()).agreement.few_note is None


def test_a_value_with_nine_cases_says_few_cases() -> None:
    data = results([class_data(entries=[version_entry("v1", slice_data=metrics(n=9))])])
    assert first_version_row(data).agreement.few_note == "Few cases."


def test_a_version_with_nine_cases_says_few_cases_on_its_row() -> None:
    data = results([class_data(entries=[version_entry("v1", slice_data=metrics(case_count=9))])])
    assert first_version_row(data).few_note == "Few cases."


def test_a_percent_interval_uses_an_en_dash() -> None:
    assert first_version_row(results()).completeness.interval == "72\u201391%"


def test_kappa_shows_two_decimals() -> None:
    assert first_version_row(results()).kappa.value == "0.74"


def test_a_kappa_interval_shows_two_decimals() -> None:
    assert first_version_row(results()).kappa.interval == "0.62\u20130.86"


def test_a_kappa_just_below_zero_shows_as_zero() -> None:
    assert format_kappa(-0.001) == "0.00"


def test_a_percent_rounds_half_up() -> None:
    assert format_percent(0.125) == "13%"


def test_counts_have_thousands_separators() -> None:
    data = results([class_data(entries=[version_entry("v1", slice_data=metrics(case_count=1030))])])
    assert first_version_row(data).cases_text == "1,030"


# Missing values say why


def test_kappa_with_all_cases_the_same_verdict_is_not_available() -> None:
    kappa = metrics(kappa_value=None, kappa_interval=None, kappa_note="all_same_verdict")
    row = first_version_row(results([class_data(entries=[version_entry("v1", slice_data=kappa)])]))
    assert (row.kappa.value, row.kappa.note) == ("n/a", "(all cases have the same verdict)")


def test_kappa_with_one_side_the_same_verdict_is_zero_without_an_interval() -> None:
    kappa = metrics(kappa_value=0.0, kappa_interval=None, kappa_note="one_side_same_verdict")
    row = first_version_row(results([class_data(entries=[version_entry("v1", slice_data=kappa)])]))
    assert (row.kappa.value, row.kappa.interval, row.kappa.note) == (
        "0.00",
        None,
        "(one side always gives the same verdict)",
    )


def test_kappa_without_a_bootstrap_interval_says_why() -> None:
    kappa = metrics(kappa_interval=None, kappa_note="interval_not_available")
    row = first_version_row(results([class_data(entries=[version_entry("v1", slice_data=kappa)])]))
    assert row.kappa.note == "No interval (too few usable resamples)"


def test_completeness_of_equal_cases_shows_the_mean_without_an_interval() -> None:
    equal = metrics(completeness_data=completeness(bounds=None, n=5))
    row = first_version_row(results([class_data(entries=[version_entry("v1", slice_data=equal)])]))
    assert (row.completeness.value, row.completeness.note) == (
        "87%",
        "No interval (all cases equal)",
    )


def test_completeness_of_one_case_says_one_case() -> None:
    single = metrics(completeness_data=completeness(bounds=None, n=1))
    row = first_version_row(results([class_data(entries=[version_entry("v1", slice_data=single)])]))
    assert row.completeness.note == "No interval (one case)"


# Version rows


def test_no_version_is_labelled() -> None:
    data = results([class_data(entries=[version_entry("v1"), version_entry(None)])])
    assert row_labels(data)[-1] == "(no version)"


def test_the_pooled_group_names_its_versions() -> None:
    data = results([class_data(other_versions=("v7", "v8"))])
    assert row_labels(data)[-1] == "(other versions: v7, v8)"


def test_versions_keep_the_results_order() -> None:
    data = results([class_data(entries=[version_entry("v2"), version_entry("v1")])])
    assert row_labels(data) == ["All versions", "v2", "v1"]


def test_rows_put_versions_then_the_pooled_group_then_no_version() -> None:
    entries = [version_entry("v1"), version_entry(None), version_entry("v2"), version_entry("v3")]
    data = results([class_data(entries=entries, shown=("v1", None, "v2"), other_versions=("v3",))])
    assert row_labels(data) == [
        "All versions",
        "v1",
        "v2",
        "(other versions: v3)",
        "(no version)",
    ]


def test_the_pooled_group_gets_a_note() -> None:
    data = results([class_data(other_versions=("v7", "v8"))])
    assert build_view(data).classes[0].pooled_note == (
        "At most six versions are shown per class, by number of cases. "
        "v7, v8 have the fewest cases here and are pooled as one group."
    )


def test_a_version_label_shows_a_bidi_override_as_an_escape() -> None:
    data = results([class_data(entries=[version_entry("v‮1")])])
    assert first_version_row(data).label == "v\\u202e1"


def test_a_long_class_name_is_not_shortened() -> None:
    data = results([class_data(alert_class="x" * 300)])
    assert build_view(data).classes[0].name == "x" * 300


# Version styles


def test_a_version_in_two_classes_has_one_style() -> None:
    first = class_data(entries=[version_entry("v1"), version_entry("v2")])
    second = class_data(alert_class="oauth_consent", entries=[version_entry("v2")])
    assert build_view(results([first, second])).classes[1].rows[1].style == "2"


def test_styles_are_never_reused_within_a_class_with_ten_labels_on_the_page() -> None:
    first = class_data(
        entries=[
            version_entry("v1"),
            version_entry("v2"),
            version_entry("v3"),
            version_entry("v4"),
            version_entry("v5"),
            version_entry("v6"),
        ]
    )
    second = class_data(
        alert_class="oauth_consent",
        entries=[
            version_entry("v1"),
            version_entry("v2"),
            version_entry("v7", first_week="2026-W11"),
            version_entry("v8", first_week="2026-W11"),
            version_entry("v9", first_week="2026-W11"),
            version_entry("v10", first_week="2026-W11"),
        ],
    )
    view = build_view(results([first, second]))
    assert [row.style for row in view.classes[1].rows] == ["all", "1", "2", "3", "4", "5", "6"]


def test_the_pooled_group_and_no_version_have_fixed_styles() -> None:
    entries = [version_entry("v1"), version_entry(None), version_entry("v2")]
    data = results([class_data(entries=entries, shown=("v1", None), other_versions=("v2",))])
    assert [row.style for row in build_view(data).classes[0].rows] == [
        "all",
        "1",
        "other",
        "none",
    ]


# Confusion matrix


def test_dangerous_false_close_cells_are_flagged() -> None:
    cells = build_view(results()).classes[0].confusion.rows
    assert [[cell.is_dangerous for cell in row.cells] for row in cells] == [
        [False, True, True],
        [False, False, False],
        [False, False, False],
    ]


def test_the_confusion_matrix_counts_dangerous_false_closes() -> None:
    assert build_view(results()).classes[0].confusion.dangerous_text == "2 dangerous false closes"


# Classes without a checklist


def no_checklist_class() -> dict[str, object]:
    slice_data = metrics(completeness_data=None)
    return class_data(
        entries=[version_entry("v1", slice_data=slice_data, items=())],
        items=(),
        overall=slice_data,
    )


def test_completeness_without_a_checklist_says_so() -> None:
    data = results([no_checklist_class()])
    assert first_version_row(data).completeness.note == "No checklist for this class."


def test_skipped_steps_without_a_checklist_say_so() -> None:
    data = results([no_checklist_class()])
    assert build_view(data).classes[0].skipped.empty_text == "No checklist for this class."


def test_the_completeness_trend_without_a_checklist_says_so() -> None:
    data = results([no_checklist_class()])
    assert (
        build_view(data).classes[0].trend.completeness.empty_text == "No checklist for this class."
    )


# Skipped steps


def test_a_skipped_step_cell_shows_rate_count_and_bar() -> None:
    cell = build_view(results()).classes[0].skipped.rows[0].cells[0]
    assert (cell.rate_text, cell.count_text, cell.bar_width) == ("20%", "3 of 15", 20.0)


# Trend


def version_trend() -> dict[str, object]:
    points = [
        trend_point("2026-W10", "all", None, 0.9, 20),
        trend_point("2026-W10", "version", "v1", 0.9, 20),
        trend_point("2026-W11", "all", None, 0.5, 5),
    ]
    return results([class_data(trend=points)])


def test_a_week_without_cases_for_a_version_has_no_value() -> None:
    assert build_view(version_trend()).classes[0].trend.agreement.lines[1].values == (0.9, None)


def test_a_week_with_five_cases_is_marked_few() -> None:
    assert build_view(version_trend()).classes[0].trend.agreement.lines[0].few == (False, True)


def test_the_trend_table_says_when_a_version_has_no_cases() -> None:
    rows = build_view(version_trend()).classes[0].trend.agreement.table_rows
    assert rows[1].cells == ("50% (n 5) Few cases.", "no cases")


def test_the_trend_gives_each_version_its_first_week() -> None:
    assert build_view(version_trend()).classes[0].trend.version_first_weeks == {"v1": "2026-W10"}


# Header


def test_the_header_counts_weeks_across_a_53_week_year() -> None:
    data = results(period=("2026-W52", "2027-W02"))
    assert build_view(data).header.period_text == "2026-W52 to 2027-W02 (4 weeks, UTC)"


def test_the_header_warns_about_low_coverage() -> None:
    data = results(join=coverage(traces_matched=44))
    assert (
        build_view(data).header.low_coverage_text
        == "Low coverage: 44% of traces matched a verdict."
    )


def test_the_header_has_no_coverage_warning_when_coverage_is_fine() -> None:
    assert build_view(results()).header.low_coverage_text is None


def test_the_header_lists_versions_then_no_version() -> None:
    data = results([class_data(entries=[version_entry(None), version_entry("v1")])])
    assert build_view(data).header.versions_text == "v1, (no version)"


# Coverage and data notes


def test_low_coverage_gets_a_warning_line() -> None:
    data = results(join=coverage(traces_matched=44))
    assert build_view(data).coverage[1].text == (
        "44 of 100 traces matched a verdict (44%). Less than half matched, so the results may "
        "be misleading; check mapping.case_id in detecttrace.yaml."
    )


def test_low_coverage_without_a_configuration_name_names_the_configuration() -> None:
    data = results(
        join=coverage(traces_matched=44),
        source={"traces": "traces", "verdicts": "verdicts.csv", "checklists": None},
    )
    assert build_view(data).coverage[1].text.endswith("check mapping.case_id in the configuration.")


def unmapped_label_note() -> dict[str, object]:
    return {
        "severity": "invalid_input",
        "kind": "unmapped_agent_label",
        "count": 1037,
        "message": "1,037 cases have the agent verdict 'x\x1b[31m', which has no mapping.",
        "hint": "Add it to agent_label_map or label_map in detecttrace.yaml.",
        "examples": [
            {"subject": "C-1", "detail": None},
            {"subject": "C-2", "detail": "d"},
            {"subject": "C-3", "detail": None},
        ],
    }


def test_a_note_message_follows_the_count_with_control_characters_escaped() -> None:
    note = build_view(results(notes=[unmapped_label_note()])).notes[0]
    assert note.message == "cases have the agent verdict 'x\\x1b[31m', which has no mapping."


def test_a_note_says_how_many_examples_are_shown() -> None:
    note = build_view(results(notes=[unmapped_label_note()])).notes[0]
    assert note.more_text == "3 of 1,037 shown."


def test_a_note_keeps_its_examples_as_subject_and_detail() -> None:
    note = build_view(results(notes=[unmapped_label_note()])).notes[0]
    assert (note.examples[1].subject, note.examples[1].detail) == ("C-2", "d")


# Cases


def test_a_class_filter_uses_the_class_index_in_the_strings_table() -> None:
    assert build_view(results()).cases.class_filters[0].class_index == 1


# Schema


def test_an_unknown_schema_version_is_named_in_the_error() -> None:
    with pytest.raises(ValueError, match="99"):
        build_view(results(schema_version=99))


def test_the_demo_results_build_two_classes() -> None:
    demo = json.loads(DEMO_GOLDEN.read_text(encoding="utf-8"))
    assert len(build_view(demo).classes) == 2
