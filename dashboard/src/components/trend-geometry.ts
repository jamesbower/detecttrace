// Geometry for the weekly trend charts, ported from the Python charts module. Returns
// coordinates, not markup, so the layout is testable and deterministic.

export const GRID_FRACTIONS = [0, 0.25, 0.5, 0.75, 1] as const;
export const PLOT_LEFT = 46;
export const PLOT_RIGHT_MARGIN = 16;
export const PLOT_TOP = 30;
export const PLOT_BOTTOM_MARGIN = 50;
export const MARKER_RADIUS = 4.2;
/** Half the 44px touch target: the chart never draws narrower than its view box. */
export const HIT_RADIUS = 22;
export const VERSION_LINE_TOP = 24;
export const VERSION_LABEL_Y = 18;
// Gaps between an axis and its text; the text sits just off the line it labels.
const LABEL_GAP = 4;
const GRID_LABEL_GAP = 8;
const WEEK_LABEL_OFFSET = 26;
const COUNT_LABEL_OFFSET = 10;

// One shape per style key so versions differ by more than color.
export const SHAPE_BY_STYLE: Readonly<Record<string, string>> = {
  "1": "circle",
  "2": "square",
  "3": "triangle",
  "4": "diamond",
  "5": "triangle-down",
  "6": "plus",
  other: "hexagon",
  none: "cross",
};

// The all-versions total is otherwise a bare line; its rare marker is a plain dot.
const ALL_SHAPE = "circle";

type Offset = readonly [number, number];

// Polygon outlines as offsets in units of the marker radius.
const POLYGONS: Readonly<Record<string, readonly Offset[]>> = {
  triangle: [
    [0, -1.15],
    [1.1, 0.85],
    [-1.1, 0.85],
  ],
  "triangle-down": [
    [0, 1.15],
    [1.1, -0.85],
    [-1.1, -0.85],
  ],
  diamond: [
    [0, -1.3],
    [1.3, 0],
    [0, 1.3],
    [-1.3, 0],
  ],
  plus: [
    [-0.35, -1.2],
    [0.35, -1.2],
    [0.35, -0.35],
    [1.2, -0.35],
    [1.2, 0.35],
    [0.35, 0.35],
    [0.35, 1.2],
    [-0.35, 1.2],
    [-0.35, 0.35],
    [-1.2, 0.35],
    [-1.2, -0.35],
    [-0.35, -0.35],
  ],
  hexagon: Array.from({ length: 6 }, (_, i): Offset => [
    Math.cos((i * Math.PI) / 3) * 1.15,
    Math.sin((i * Math.PI) / 3) * 1.15,
  ]),
  cross: [
    [-1, -1.2],
    [0, -0.25],
    [1, -1.2],
    [1.2, -1],
    [0.25, 0],
    [1.2, 1],
    [1, 1.2],
    [0, 0.25],
    [-1, 1.2],
    [-1.2, 1],
    [-0.25, 0],
    [-1.2, -1],
  ],
};

/** One line: a style key ("1".."6", "other", "none", or "all"), and per week a value (0-1 or
 * null), a case count, and whether the week has few cases (drawn hollow). */
export type SeriesInput = {
  readonly style: string;
  readonly values: ReadonlyArray<number | null>;
  readonly counts: ReadonlyArray<number>;
  readonly few: ReadonlyArray<boolean>;
};

export type GridLine = {
  y: number;
  label: string;
  xStart: number;
  xEnd: number;
  /** The label's right end. */
  labelX: number;
  labelY: number;
};

export type XLabel = { x: number; week: string; countText: string; yWeek: number; yCount: number };

/** One line per week where versions first appear; the label names them all. */
export type VersionMarker = {
  x: number;
  label: string;
  yTop: number;
  yBottom: number;
  labelX: number;
  labelY: number;
  /** SVG text-anchor, so the label stays inside the chart. */
  anchor: "start" | "end";
};

/** A circle is drawn from (x, y) and r, a square from (left, top) and side; other shapes carry
 * their polygon points. */
export type Marker = {
  shape: string;
  x: number;
  y: number;
  r: number;
  points: Array<[number, number]>;
  isFew: boolean;
  left: number;
  top: number;
  side: number;
};

/** A week with a value, whether or not a marker is drawn there; `index` is the input week's. */
export type ChartPoint = { x: number; y: number; index: number; isFew: boolean };

export type ChartSeries = {
  style: string;
  path: string;
  markers: Marker[];
  points: ChartPoint[];
};

export type Chart = {
  width: number;
  height: number;
  grid: GridLine[];
  xLabels: XLabel[];
  versionMarkers: VersionMarker[];
  series: ChartSeries[];
};

/** Lays out weekly series. Weeks are placed in label order; each series' values, counts and few
 * flags follow the input week order. */
export function createTrendChart(
  weeks: readonly string[],
  series: readonly SeriesInput[],
  versionFirstWeeks: Readonly<Record<string, string>>,
  { width = 640, height = 270 }: { width?: number; height?: number } = {},
): Chart {
  const order = weeks.map((_, index) => index).sort((a, b) => compareText(weeks[a]!, weeks[b]!));
  const sortedWeeks = order.map((index) => weeks[index]!);
  const plotRight = width - PLOT_RIGHT_MARGIN;
  const plotBottom = height - PLOT_BOTTOM_MARGIN;

  // A single week sits mid-plot instead of dividing by zero.
  const toX = (position: number): number =>
    sortedWeeks.length === 1
      ? roundCoordinate((PLOT_LEFT + plotRight) / 2)
      : roundCoordinate(PLOT_LEFT + (position * (plotRight - PLOT_LEFT)) / (sortedWeeks.length - 1));
  const toY = (value: number): number => roundCoordinate(PLOT_TOP + (1 - value) * (plotBottom - PLOT_TOP));

  const xByWeek = new Map(sortedWeeks.map((week, position) => [week, toX(position)]));
  const allCounts = series.find((item) => item.style === "all")?.counts;
  const xLabels = sortedWeeks.map((week, position) => ({
    x: toX(position),
    week: toShortLabel(week),
    countText: `n ${(allCounts?.[order[position]!] ?? 0).toLocaleString("en-US")}`,
    yWeek: height - WEEK_LABEL_OFFSET,
    yCount: height - COUNT_LABEL_OFFSET,
  }));

  const labelsByWeek = new Map<string, string[]>();
  for (const [label, week] of Object.entries(versionFirstWeeks)) {
    if (xByWeek.has(week)) {
      labelsByWeek.set(week, [...(labelsByWeek.get(week) ?? []), label]);
    }
  }
  const middle = (PLOT_LEFT + plotRight) / 2;
  const versionMarkers = [...labelsByWeek.entries()]
    .sort(([a], [b]) => compareText(a, b))
    .map(([week, labels]) => createVersionMarker(xByWeek.get(week)!, labels.join(", "), plotBottom, middle));

  return {
    width,
    height,
    grid: GRID_FRACTIONS.map((fraction) => ({
      y: toY(fraction),
      label: `${Math.round(fraction * 100)}%`,
      xStart: PLOT_LEFT,
      xEnd: plotRight,
      labelX: PLOT_LEFT - GRID_LABEL_GAP,
      labelY: roundCoordinate(toY(fraction) + LABEL_GAP),
    })),
    xLabels,
    versionMarkers,
    series: series.map((item) => layOutSeries(item, order, toX, toY)),
  };
}

/** The marker for a style key centred on (x, y); the legend uses it with a smaller radius. */
export function createMarker(
  style: string,
  x: number,
  y: number,
  { isFew = false, radius = MARKER_RADIUS }: { isFew?: boolean; radius?: number } = {},
): Marker {
  const shape = style === "all" ? ALL_SHAPE : (SHAPE_BY_STYLE[style] ?? ALL_SHAPE);
  const offsets = POLYGONS[shape] ?? [];
  return {
    shape,
    x,
    y,
    r: radius,
    points: offsets.map(([dx, dy]) => [roundCoordinate(x + dx * radius), roundCoordinate(y + dy * radius)]),
    isFew,
    left: roundCoordinate(x - radius),
    top: roundCoordinate(y - radius),
    side: roundCoordinate(2 * radius),
  };
}

function createVersionMarker(x: number, label: string, plotBottom: number, middle: number): VersionMarker {
  // Past the middle a label reads leftwards, so a version in the last week stays in the chart.
  const isRightHalf = x > middle;
  return {
    x,
    label,
    yTop: VERSION_LINE_TOP,
    yBottom: plotBottom,
    labelX: roundCoordinate(isRightHalf ? x - LABEL_GAP : x + LABEL_GAP),
    labelY: VERSION_LABEL_Y,
    anchor: isRightHalf ? "end" : "start",
  };
}

function layOutSeries(
  item: SeriesInput,
  order: readonly number[],
  toX: (position: number) => number,
  toY: (value: number) => number,
): ChartSeries {
  const segments: ChartPoint[][] = [];
  let current: ChartPoint[] = [];
  order.forEach((index, position) => {
    const value = item.values[index];
    if (value === null || value === undefined) {
      // A week without cases breaks the line; drawing it as zero would claim every case failed.
      if (current.length > 0) {
        segments.push(current);
      }
      current = [];
      return;
    }
    current.push({ x: toX(position), y: toY(value), index, isFew: item.few[index] ?? false });
  });
  if (current.length > 0) {
    segments.push(current);
  }
  // The total is drawn as a bare line, so a week with no neighbour needs a marker to show.
  const markedSegments = item.style === "all" ? segments.filter((segment) => segment.length === 1) : segments;
  const markers = markedSegments.flat().map((point) => createMarker(item.style, point.x, point.y, { isFew: point.isFew }));
  // A lone point has no line to draw; its marker stands for it.
  const path = segments
    .filter((segment) => segment.length > 1)
    .map((segment) => `M${segment.map((point) => `${point.x} ${point.y}`).join(" L")}`)
    .join(" ");
  return { style: item.style, path, markers, points: segments.flat() };
}

// "2026-W27" reads as "W27" on the axis; the year is in the page header.
function toShortLabel(week: string): string {
  const start = week.lastIndexOf("W");
  return start === -1 ? week : week.slice(start);
}

function compareText(a: string, b: string): number {
  return a < b ? -1 : a > b ? 1 : 0;
}

function roundCoordinate(value: number): number {
  return Math.round(value * 10) / 10;
}
