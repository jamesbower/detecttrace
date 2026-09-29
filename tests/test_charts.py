from detecttrace.charts import SHAPE_BY_STYLE, Chart, SeriesInput, trend_chart

SIX_WEEKS = [f"2026-W{week}" for week in range(27, 33)]
TWELVE_WEEKS = [f"2026-W{week}" for week in range(27, 39)]


def _flat(style: str, weeks: list[str], value: float | None = 0.5, count: int = 20) -> SeriesInput:
    return SeriesInput(style, [value] * len(weeks), [count] * len(weeks), [False] * len(weeks))


def _by_style(chart: Chart, style: str):
    return next(item for item in chart.series if item.style == style)


def test_two_versions_over_six_weeks_yield_three_series() -> None:
    series = [_flat("all", SIX_WEEKS), _flat("1", SIX_WEEKS), _flat("2", SIX_WEEKS)]

    chart = trend_chart(SIX_WEEKS, series, {})

    assert [item.style for item in chart.series] == ["all", "1", "2"]


def test_version_markers_sit_at_first_weeks() -> None:
    chart = trend_chart(SIX_WEEKS, [_flat("all", SIX_WEEKS)], {"v1": "2026-W27", "v2": "2026-W30"})

    assert [(marker.label, marker.x) for marker in chart.version_markers] == [
        ("v1", 46.0),
        ("v2", 392.8),
    ]


def test_two_versions_in_one_week_share_one_marker_with_a_joined_label() -> None:
    chart = trend_chart(SIX_WEEKS, [], {"v2": "2026-W28", "v9": "2026-W28"})

    assert [marker.label for marker in chart.version_markers] == ["v2, v9"]


def test_version_markers_follow_the_week_order() -> None:
    chart = trend_chart(SIX_WEEKS, [], {"v2": "2026-W30", "v1": "2026-W27"})

    assert [marker.label for marker in chart.version_markers] == ["v1", "v2"]


def test_an_early_version_label_starts_right_of_its_line() -> None:
    chart = trend_chart(SIX_WEEKS, [], {"v1": "2026-W27"})

    marker = chart.version_markers[0]
    assert (marker.anchor, marker.label_x) == ("start", 50.0)


def test_a_last_week_version_label_ends_left_of_its_line() -> None:
    chart = trend_chart(SIX_WEEKS, [], {"v9": "2026-W32"})

    marker = chart.version_markers[0]
    assert (marker.anchor, marker.label_x) == ("end", 620.0)


def test_a_last_week_version_label_stays_inside_the_view_box() -> None:
    chart = trend_chart(SIX_WEEKS, [], {"v9": "2026-W32"})

    assert 0 < chart.version_markers[0].label_x < chart.width


def test_a_version_marker_line_spans_the_plot_height() -> None:
    chart = trend_chart(SIX_WEEKS, [], {"v1": "2026-W27"})

    marker = chart.version_markers[0]
    assert (marker.y_top, marker.y_bottom, marker.label_y) == (24.0, 220.0, 18.0)


def test_grid_lines_carry_their_ends_and_label_position() -> None:
    chart = trend_chart(SIX_WEEKS, [], {})

    line = chart.grid[0]
    assert (line.x_start, line.x_end, line.label_x, line.label_y) == (46.0, 624.0, 38.0, 224.0)


def test_week_labels_carry_their_rows() -> None:
    chart = trend_chart(SIX_WEEKS, [], {})

    assert (chart.x_labels[0].y_week, chart.x_labels[0].y_count) == (244.0, 260.0)


def test_a_square_marker_carries_its_corner_and_side() -> None:
    series = [SeriesInput("2", [0.5], [20], [False])]

    chart = trend_chart(["2026-W27"], series, {})

    marker = chart.series[0].markers[0]
    assert (marker.left, marker.top, marker.side) == (330.8, 120.8, 8.4)


def test_an_isolated_all_versions_week_has_a_marker() -> None:
    values = [0.5, 0.6, 0.4, None, None, 0.6]
    series = [SeriesInput("all", values, [20] * 6, [False] * 6)]

    chart = trend_chart(SIX_WEEKS, series, {})

    assert [marker.x for marker in chart.series[0].markers] == [624.0]


def test_an_all_versions_marker_is_a_circle() -> None:
    series = [SeriesInput("all", [0.5], [20], [True])]

    chart = trend_chart(["2026-W27"], series, {})

    marker = chart.series[0].markers[0]
    assert (marker.shape, marker.is_few) == ("circle", True)


def test_missing_week_splits_the_path_into_two_segments() -> None:
    values = [0.5, 0.6, None, 0.4, 0.5, 0.6]
    series = [SeriesInput("1", values, [20] * 6, [False] * 6)]

    chart = trend_chart(SIX_WEEKS, series, {})

    assert chart.series[0].path.count("M") == 2


def test_missing_week_has_no_marker() -> None:
    values = [0.5, 0.6, None, 0.4, 0.5, 0.6]
    series = [SeriesInput("1", values, [20] * 6, [False] * 6)]

    chart = trend_chart(SIX_WEEKS, series, {})

    assert len(chart.series[0].markers) == 5


def test_single_week_has_a_marker_and_no_path() -> None:
    values = [None, None, 0.5, None, None, None]
    series = [SeriesInput("1", values, [20] * 6, [False] * 6)]

    chart = trend_chart(SIX_WEEKS, series, {})

    assert (chart.series[0].path, len(chart.series[0].markers)) == ("", 1)


def test_zero_and_one_stay_inside_the_plot_area() -> None:
    series = [SeriesInput("1", [0.0, 1.0], [20, 20], [False, False])]

    chart = trend_chart(["2026-W27", "2026-W28"], series, {})

    assert [marker.y for marker in chart.series[0].markers] == [220.0, 30.0]


def test_grid_spans_zero_to_one_hundred_percent() -> None:
    chart = trend_chart(SIX_WEEKS, [], {})

    assert [(line.label, line.y) for line in chart.grid] == [
        ("0%", 220.0),
        ("25%", 172.5),
        ("50%", 125.0),
        ("75%", 77.5),
        ("100%", 30.0),
    ]


def test_weeks_order_by_label_across_the_53_week_boundary() -> None:
    weeks = ["2027-W01", "2026-W53", "2026-W52"]

    chart = trend_chart(weeks, [_flat("all", weeks)], {})

    assert [label.week for label in chart.x_labels] == ["W52", "W53", "W01"]


def test_values_follow_their_week_when_weeks_are_reordered() -> None:
    weeks = ["2027-W01", "2026-W52"]
    series = [SeriesInput("1", [1.0, 0.0], [20, 20], [False, False])]

    chart = trend_chart(weeks, series, {})

    assert [marker.y for marker in chart.series[0].markers] == [220.0, 30.0]


def test_count_text_uses_the_all_versions_series() -> None:
    weeks = ["2026-W27", "2026-W28"]
    series = [SeriesInput("all", [0.5, 0.5], [170, 1200], [False, False]), _flat("1", weeks)]

    chart = trend_chart(weeks, series, {})

    assert [label.count_text for label in chart.x_labels] == ["n 170", "n 1,200"]


def test_each_style_key_has_its_own_shape() -> None:
    assert len(set(SHAPE_BY_STYLE.values())) == len(SHAPE_BY_STYLE)


def test_a_marker_is_few_when_its_series_says_so() -> None:
    series = [SeriesInput("1", [0.5, 0.5], [20, 20], [True, False])]

    chart = trend_chart(["2026-W27", "2026-W28"], series, {})

    assert [marker.is_few for marker in chart.series[0].markers] == [True, False]


def test_all_series_without_isolated_weeks_draws_a_path_but_no_markers() -> None:
    chart = trend_chart(SIX_WEEKS, [_flat("all", SIX_WEEKS)], {})

    assert (chart.series[0].path != "", chart.series[0].markers) == (True, ())


def test_same_input_yields_identical_charts() -> None:
    series = [_flat("all", SIX_WEEKS), _flat("3", SIX_WEEKS, 1 / 3)]

    assert trend_chart(SIX_WEEKS, series, {"v": "2026-W28"}) == trend_chart(
        SIX_WEEKS, series, {"v": "2026-W28"}
    )


def test_weeks_are_evenly_spaced() -> None:
    chart = trend_chart(SIX_WEEKS, [], {})

    assert [label.x for label in chart.x_labels] == [46.0, 161.6, 277.2, 392.8, 508.4, 624.0]


def test_twelve_weeks_fit_inside_the_width() -> None:
    chart = trend_chart(TWELVE_WEEKS, [], {})

    assert (chart.x_labels[0].x, chart.x_labels[-1].x) == (46.0, 624.0)


def test_coordinates_are_rounded_to_one_decimal() -> None:
    series = [SeriesInput("3", [1 / 3, 2 / 3, 0.123456], [20, 20, 20], [False] * 3)]

    chart = trend_chart(["2026-W27", "2026-W28", "2026-W29"], series, {})

    assert all(
        round(coordinate, 1) == coordinate
        for marker in chart.series[0].markers
        for point in marker.points
        for coordinate in point
    )
