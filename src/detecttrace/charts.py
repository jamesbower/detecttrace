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
# Below this many cases a point is drawn hollow, so a thin week never looks as solid as a full one.
FEW_CASES = 10

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
    """One line: a style key ("1".."6", "other", "none", or "all") and a value (0-1 or None) and case count per week."""

    style: str
    values: Sequence[float | None]
    counts: Sequence[int]


@dataclass(frozen=True, slots=True)
class GridLine:
    y: float
    label: str


@dataclass(frozen=True, slots=True)
class XLabel:
    x: float
    week: str
    count_text: str


@dataclass(frozen=True, slots=True)
class VersionMarker:
    x: float
    label: str


@dataclass(frozen=True, slots=True)
class Marker:
    """A circle or square is drawn from (x, y) and r (a square's side is 2r); other shapes carry their polygon points."""

    shape: str
    x: float
    y: float
    r: float
    points: tuple[tuple[float, float], ...]
    is_few: bool


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
    """Lay out weekly series. Weeks are placed in label order; each series' values and counts follow the input week order."""
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
        )
        for position in range(len(sorted_weeks))
    )
    version_markers = tuple(
        VersionMarker(x=x_by_week[week], label=label)
        for label, week in version_first_weeks.items()
        if week in x_by_week
    )
    return Chart(
        width=width,
        height=height,
        grid=tuple(
            GridLine(y=to_y(fraction), label=f"{round(fraction * 100)}%")
            for fraction in GRID_FRACTIONS
        ),
        x_labels=x_labels,
        version_markers=version_markers,
        series=tuple(_lay_out_series(item, order, to_x, to_y) for item in series),
    )


def _lay_out_series(item: SeriesInput, order: Sequence[int], to_x, to_y) -> ChartSeries:
    segments: list[list[tuple[float, float]]] = []
    markers: list[Marker] = []
    current: list[tuple[float, float]] = []
    for position, index in enumerate(order):
        value = item.values[index]
        if value is None:
            # A week without cases breaks the line; drawing it as zero would claim every case failed.
            if current:
                segments.append(current)
            current = []
            continue
        point = (to_x(position), to_y(value))
        current.append(point)
        if item.style != "all":
            markers.append(create_marker(item.style, *point, is_few=item.counts[index] < FEW_CASES))
    if current:
        segments.append(current)
    # A lone point has no line to draw; its marker stands for it.
    path = " ".join(
        "M" + " L".join(f"{x} {y}" for x, y in segment) for segment in segments if len(segment) > 1
    )
    return ChartSeries(style=item.style, path=path, markers=tuple(markers))


def create_marker(
    style: str, x: float, y: float, *, is_few: bool = False, radius: float = MARKER_RADIUS
) -> Marker:
    """The marker for a style key centred on (x, y); legends use it with a smaller radius."""
    shape = SHAPE_BY_STYLE[style]
    offsets = _POLYGONS.get(shape, ())
    return Marker(
        shape=shape,
        x=x,
        y=y,
        r=radius,
        points=tuple((_round(x + dx * radius), _round(y + dy * radius)) for dx, dy in offsets),
        is_few=is_few,
    )


def _short_label(week: str) -> str:
    # "2026-W27" reads as "W27" on the axis; the year is in the page header.
    return week[week.rfind("W") :] if "W" in week else week


def _round(value: float) -> float:
    return round(value, 1)
