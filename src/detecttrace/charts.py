"""Geometry for the weekly trend charts. Returns coordinates, not markup, so output is testable and deterministic."""

import math
from collections.abc import Mapping, Sequence
from dataclasses import dataclass

GRID_FRACTIONS = (0.0, 0.25, 0.5, 0.75, 1.0)
PLOT_LEFT = 46.0
PLOT_RIGHT_MARGIN = 16.0
PLOT_TOP = 30.0
PLOT_BOTTOM_MARGIN = 50.0
MARKER_RADIUS = 4.2
VERSION_LINE_TOP = 24.0
VERSION_LABEL_Y = 18.0
# Gaps between an axis and its text; the text sits just off the line it labels.
LABEL_GAP = 4.0
GRID_LABEL_GAP = 8.0
WEEK_LABEL_OFFSET = 26.0
COUNT_LABEL_OFFSET = 10.0

# One shape per style key so versions differ by more than color.
SHAPE_BY_STYLE = {
    "1": "circle",
    "2": "square",
    "3": "triangle",
    "4": "diamond",
    "5": "triangle-down",
    "6": "plus",
    "other": "hexagon",
    "none": "cross",
}

# The all-versions total is otherwise a bare line; its rare marker is a plain dot.
ALL_SHAPE = "circle"

# Polygon outlines as offsets in units of the marker radius.
_POLYGONS: dict[str, tuple[tuple[float, float], ...]] = {
    "triangle": ((0, -1.15), (1.1, 0.85), (-1.1, 0.85)),
    "triangle-down": ((0, 1.15), (1.1, -0.85), (-1.1, -0.85)),
    "diamond": ((0, -1.3), (1.3, 0), (0, 1.3), (-1.3, 0)),
    "plus": (
        (-0.35, -1.2),
        (0.35, -1.2),
        (0.35, -0.35),
        (1.2, -0.35),
        (1.2, 0.35),
        (0.35, 0.35),
        (0.35, 1.2),
        (-0.35, 1.2),
        (-0.35, 0.35),
        (-1.2, 0.35),
        (-1.2, -0.35),
        (-0.35, -0.35),
    ),
    "hexagon": tuple(
        (math.cos(i * math.pi / 3) * 1.15, math.sin(i * math.pi / 3) * 1.15) for i in range(6)
    ),
    "cross": (
        (-1, -1.2),
        (0, -0.25),
        (1, -1.2),
        (1.2, -1),
        (0.25, 0),
        (1.2, 1),
        (1, 1.2),
        (0, 0.25),
        (-1, 1.2),
        (-1.2, 1),
        (-0.25, 0),
        (-1.2, -1),
    ),
}


@dataclass(frozen=True, slots=True)
class SeriesInput:
    """One line: a style key ("1".."6", "other", "none", or "all"), and per week a value (0-1 or
    None), a case count, and whether the week has few cases (drawn hollow)."""

    style: str
    values: Sequence[float | None]
    counts: Sequence[int]
    few: Sequence[bool]


@dataclass(frozen=True, slots=True)
class GridLine:
    y: float
    label: str
    x_start: float
    x_end: float
    label_x: float  # the label's right end
    label_y: float


@dataclass(frozen=True, slots=True)
class XLabel:
    x: float
    week: str
    count_text: str
    y_week: float
    y_count: float


@dataclass(frozen=True, slots=True)
class VersionMarker:
    """One line per week where versions first appear; the label names them all."""

    x: float
    label: str  # "v2, v9"
    y_top: float
    y_bottom: float
    label_x: float
    label_y: float
    anchor: str  # SVG text-anchor: "start" or "end", so the label stays inside the chart


@dataclass(frozen=True, slots=True)
class Marker:
    """A circle is drawn from (x, y) and r, a square from (left, top) and side; other shapes
    carry their polygon points."""

    shape: str
    x: float
    y: float
    r: float
    points: tuple[tuple[float, float], ...]
    is_few: bool
    left: float
    top: float
    side: float


@dataclass(frozen=True, slots=True)
class ChartSeries:
    style: str
    path: str
    markers: tuple[Marker, ...]


@dataclass(frozen=True, slots=True)
class Chart:
    width: int
    height: int
    grid: tuple[GridLine, ...]
    x_labels: tuple[XLabel, ...]
    version_markers: tuple[VersionMarker, ...]
    series: tuple[ChartSeries, ...]


def trend_chart(
    weeks: Sequence[str],
    series: Sequence[SeriesInput],
    version_first_weeks: Mapping[str, str],
    *,
    width: int = 640,
    height: int = 270,
) -> Chart:
    """Lay out weekly series. Weeks are placed in label order; each series' values, counts and few flags follow the input week order."""
    order = sorted(range(len(weeks)), key=lambda index: weeks[index])
    sorted_weeks = [weeks[index] for index in order]
    plot_right = width - PLOT_RIGHT_MARGIN
    plot_bottom = height - PLOT_BOTTOM_MARGIN

    def to_x(position: int) -> float:
        # A single week sits mid-plot instead of dividing by zero.
        if len(sorted_weeks) == 1:
            return _round((PLOT_LEFT + plot_right) / 2)
        return _round(PLOT_LEFT + position * (plot_right - PLOT_LEFT) / (len(sorted_weeks) - 1))

    def to_y(value: float) -> float:
        return _round(PLOT_TOP + (1 - value) * (plot_bottom - PLOT_TOP))

    x_by_week = {week: to_x(position) for position, week in enumerate(sorted_weeks)}
    all_counts = next((item.counts for item in series if item.style == "all"), None)
    x_labels = tuple(
        XLabel(
            x=to_x(position),
            week=_short_label(sorted_weeks[position]),
            count_text=f"n {all_counts[order[position]] if all_counts else 0:,}",
            y_week=height - WEEK_LABEL_OFFSET,
            y_count=height - COUNT_LABEL_OFFSET,
        )
        for position in range(len(sorted_weeks))
    )
    labels_by_week: dict[str, list[str]] = {}
    for label, week in version_first_weeks.items():
        if week in x_by_week:
            labels_by_week.setdefault(week, []).append(label)
    middle = (PLOT_LEFT + plot_right) / 2
    version_markers = tuple(
        _create_version_marker(x_by_week[week], ", ".join(labels), plot_bottom, middle)
        for week, labels in sorted(labels_by_week.items())
    )
    return Chart(
        width=width,
        height=height,
        grid=tuple(
            GridLine(
                y=to_y(fraction),
                label=f"{round(fraction * 100)}%",
                x_start=PLOT_LEFT,
                x_end=plot_right,
                label_x=PLOT_LEFT - GRID_LABEL_GAP,
                label_y=_round(to_y(fraction) + LABEL_GAP),
            )
            for fraction in GRID_FRACTIONS
        ),
        x_labels=x_labels,
        version_markers=version_markers,
        series=tuple(_lay_out_series(item, order, to_x, to_y) for item in series),
    )


def _create_version_marker(
    x: float, label: str, plot_bottom: float, middle: float
) -> VersionMarker:
    # Past the middle a label reads leftwards, so a version in the last week stays in the chart.
    is_right_half = x > middle
    return VersionMarker(
        x=x,
        label=label,
        y_top=VERSION_LINE_TOP,
        y_bottom=plot_bottom,
        label_x=_round(x - LABEL_GAP if is_right_half else x + LABEL_GAP),
        label_y=VERSION_LABEL_Y,
        anchor="end" if is_right_half else "start",
    )


def _lay_out_series(item: SeriesInput, order: Sequence[int], to_x, to_y) -> ChartSeries:
    segments: list[list[tuple[float, float, bool]]] = []
    current: list[tuple[float, float, bool]] = []
    for position, index in enumerate(order):
        value = item.values[index]
        if value is None:
            # A week without cases breaks the line; drawing it as zero would claim every case failed.
            if current:
                segments.append(current)
            current = []
            continue
        current.append((to_x(position), to_y(value), item.few[index]))
    if current:
        segments.append(current)
    if item.style == "all":
        # The total is drawn as a bare line, so a week with no neighbour needs a marker to show.
        markers = [
            create_marker(item.style, x, y, is_few=is_few)
            for segment in segments
            if len(segment) == 1
            for x, y, is_few in segment
        ]
    else:
        markers = [
            create_marker(item.style, x, y, is_few=is_few)
            for segment in segments
            for x, y, is_few in segment
        ]
    # A lone point has no line to draw; its marker stands for it.
    path = " ".join(
        "M" + " L".join(f"{x} {y}" for x, y, _ in segment)
        for segment in segments
        if len(segment) > 1
    )
    return ChartSeries(style=item.style, path=path, markers=tuple(markers))


def create_marker(
    style: str, x: float, y: float, *, is_few: bool = False, radius: float = MARKER_RADIUS
) -> Marker:
    """The marker for a style key centred on (x, y); legends use it with a smaller radius."""
    shape = ALL_SHAPE if style == "all" else SHAPE_BY_STYLE[style]
    offsets = _POLYGONS.get(shape, ())
    return Marker(
        shape=shape,
        x=x,
        y=y,
        r=radius,
        points=tuple((_round(x + dx * radius), _round(y + dy * radius)) for dx, dy in offsets),
        is_few=is_few,
        left=_round(x - radius),
        top=_round(y - radius),
        side=_round(2 * radius),
    )


def _short_label(week: str) -> str:
    # "2026-W27" reads as "W27" on the axis; the year is in the page header.
    return week[week.rfind("W") :] if "W" in week else week


def _round(value: float) -> float:
    return round(value, 1)
