import { expect, it } from "vitest";

import { SHAPE_BY_STYLE, createTrendChart } from "./trend-geometry";

import type { Chart, SeriesInput } from "./trend-geometry";

const SIX_WEEKS = Array.from({ length: 6 }, (_, i) => `2026-W${27 + i}`);
const TWELVE_WEEKS = Array.from({ length: 12 }, (_, i) => `2026-W${27 + i}`);

function createFlat(style: string, weeks: readonly string[], value: number | null = 0.5, count = 20): SeriesInput {
  return {
    style,
    values: weeks.map(() => value),
    counts: weeks.map(() => count),
    few: weeks.map(() => false),
  };
}

function createSeries(style: string, values: Array<number | null>, few?: boolean[]): SeriesInput {
  return { style, values, counts: values.map(() => 20), few: few ?? values.map(() => false) };
}

function firstSeries(chart: Chart) {
  return chart.series[0]!;
}

it("yields three series for two versions over six weeks", () => {
  const series = [createFlat("all", SIX_WEEKS), createFlat("1", SIX_WEEKS), createFlat("2", SIX_WEEKS)];

  const chart = createTrendChart(SIX_WEEKS, series, {});

  expect(chart.series.map((item) => item.style)).toEqual(["all", "1", "2"]);
});

it("puts version markers at their first weeks", () => {
  const chart = createTrendChart(SIX_WEEKS, [createFlat("all", SIX_WEEKS)], { v1: "2026-W27", v2: "2026-W30" });

  expect(chart.versionMarkers.map((marker) => [marker.label, marker.x])).toEqual([
    ["v1", 46],
    ["v2", 392.8],
  ]);
});

it("joins two versions in one week into one marker", () => {
  const chart = createTrendChart(SIX_WEEKS, [], { v2: "2026-W28", v9: "2026-W28" });

  expect(chart.versionMarkers.map((marker) => marker.label)).toEqual(["v2, v9"]);
});

it("orders version markers by week", () => {
  const chart = createTrendChart(SIX_WEEKS, [], { v2: "2026-W30", v1: "2026-W27" });

  expect(chart.versionMarkers.map((marker) => marker.label)).toEqual(["v1", "v2"]);
});

it("starts an early version label right of its line", () => {
  const chart = createTrendChart(SIX_WEEKS, [], { v1: "2026-W27" });

  const marker = chart.versionMarkers[0]!;
  expect([marker.anchor, marker.labelX]).toEqual(["start", 50]);
});

it("ends a last-week version label left of its line", () => {
  const chart = createTrendChart(SIX_WEEKS, [], { v9: "2026-W32" });

  const marker = chart.versionMarkers[0]!;
  expect([marker.anchor, marker.labelX]).toEqual(["end", 620]);
});

it("keeps a last-week version label inside the view box", () => {
  const chart = createTrendChart(SIX_WEEKS, [], { v9: "2026-W32" });

  const labelX = chart.versionMarkers[0]!.labelX;
  expect(labelX > 0 && labelX < chart.width).toBe(true);
});

it("spans a version marker line over the plot height", () => {
  const chart = createTrendChart(SIX_WEEKS, [], { v1: "2026-W27" });

  const marker = chart.versionMarkers[0]!;
  expect([marker.yTop, marker.yBottom, marker.labelY]).toEqual([24, 220, 18]);
});

it("ignores a version whose first week is not charted", () => {
  const chart = createTrendChart(SIX_WEEKS, [], { v1: "2025-W01" });

  expect(chart.versionMarkers).toEqual([]);
});

it("gives grid lines their ends and label position", () => {
  const chart = createTrendChart(SIX_WEEKS, [], {});

  const line = chart.grid[0]!;
  expect([line.xStart, line.xEnd, line.labelX, line.labelY]).toEqual([46, 624, 38, 224]);
});

it("gives week labels their rows", () => {
  const chart = createTrendChart(SIX_WEEKS, [], {});

  expect([chart.xLabels[0]!.yWeek, chart.xLabels[0]!.yCount]).toEqual([244, 260]);
});

it("gives a square marker its corner and side", () => {
  const chart = createTrendChart(["2026-W27"], [createSeries("2", [0.5])], {});

  const marker = firstSeries(chart).markers[0]!;
  expect([marker.left, marker.top, marker.side]).toEqual([330.8, 120.8, 8.4]);
});

it("marks an isolated all-versions week", () => {
  const series = [createSeries("all", [0.5, 0.6, 0.4, null, null, 0.6])];

  const chart = createTrendChart(SIX_WEEKS, series, {});

  expect(firstSeries(chart).markers.map((marker) => marker.x)).toEqual([624]);
});

it("draws an all-versions marker as a circle", () => {
  const chart = createTrendChart(["2026-W27"], [createSeries("all", [0.5], [true])], {});

  const marker = firstSeries(chart).markers[0]!;
  expect([marker.shape, marker.isFew]).toEqual(["circle", true]);
});

it("splits the path into two segments at a missing week", () => {
  const series = [createSeries("1", [0.5, 0.6, null, 0.4, 0.5, 0.6])];

  const chart = createTrendChart(SIX_WEEKS, series, {});

  expect(firstSeries(chart).path.split("M").length - 1).toBe(2);
});

it("draws no marker at a missing week", () => {
  const series = [createSeries("1", [0.5, 0.6, null, 0.4, 0.5, 0.6])];

  const chart = createTrendChart(SIX_WEEKS, series, {});

  expect(firstSeries(chart).markers).toHaveLength(5);
});

it("gives a single week a marker and no path", () => {
  const series = [createSeries("1", [null, null, 0.5, null, null, null])];

  const chart = createTrendChart(SIX_WEEKS, series, {});

  expect([firstSeries(chart).path, firstSeries(chart).markers.length]).toEqual(["", 1]);
});

it("keeps zero and one inside the plot area", () => {
  const chart = createTrendChart(["2026-W27", "2026-W28"], [createSeries("1", [0, 1])], {});

  expect(firstSeries(chart).markers.map((marker) => marker.y)).toEqual([220, 30]);
});

it("spans the grid from zero to one hundred percent", () => {
  const chart = createTrendChart(SIX_WEEKS, [], {});

  expect(chart.grid.map((line) => [line.label, line.y])).toEqual([
    ["0%", 220],
    ["25%", 172.5],
    ["50%", 125],
    ["75%", 77.5],
    ["100%", 30],
  ]);
});

it("orders weeks by label across the 53-week boundary", () => {
  const weeks = ["2027-W01", "2026-W53", "2026-W52"];

  const chart = createTrendChart(weeks, [createFlat("all", weeks)], {});

  expect(chart.xLabels.map((label) => label.week)).toEqual(["W52", "W53", "W01"]);
});

it("keeps values with their week when weeks are reordered", () => {
  const chart = createTrendChart(["2027-W01", "2026-W52"], [createSeries("1", [1, 0])], {});

  expect(firstSeries(chart).markers.map((marker) => marker.y)).toEqual([220, 30]);
});

it("keeps each point's input week index when weeks are reordered", () => {
  const chart = createTrendChart(["2027-W01", "2026-W52"], [createSeries("1", [1, 0])], {});

  expect(firstSeries(chart).points.map((point) => point.index)).toEqual([1, 0]);
});

it("takes the count text from the all-versions series", () => {
  const weeks = ["2026-W27", "2026-W28"];
  const series = [{ style: "all", values: [0.5, 0.5], counts: [170, 1200], few: [false, false] }, createFlat("1", weeks)];

  const chart = createTrendChart(weeks, series, {});

  expect(chart.xLabels.map((label) => label.countText)).toEqual(["n 170", "n 1,200"]);
});

it("gives each style key its own shape", () => {
  expect(new Set(Object.values(SHAPE_BY_STYLE)).size).toBe(Object.keys(SHAPE_BY_STYLE).length);
});

it("makes a marker few when its series says so", () => {
  const chart = createTrendChart(["2026-W27", "2026-W28"], [createSeries("1", [0.5, 0.5], [true, false])], {});

  expect(firstSeries(chart).markers.map((marker) => marker.isFew)).toEqual([true, false]);
});

it("draws a path but no markers for an all-versions series without isolated weeks", () => {
  const chart = createTrendChart(SIX_WEEKS, [createFlat("all", SIX_WEEKS)], {});

  expect([firstSeries(chart).path !== "", firstSeries(chart).markers]).toEqual([true, []]);
});

it("gives an all-versions series a focus point at every week with a value", () => {
  const chart = createTrendChart(SIX_WEEKS, [createFlat("all", SIX_WEEKS)], {});

  expect(firstSeries(chart).points).toHaveLength(6);
});

it("yields identical charts for the same input", () => {
  const series = [createFlat("all", SIX_WEEKS), createFlat("3", SIX_WEEKS, 1 / 3)];

  expect(createTrendChart(SIX_WEEKS, series, { v: "2026-W28" })).toEqual(
    createTrendChart(SIX_WEEKS, series, { v: "2026-W28" }),
  );
});

it("spaces weeks evenly", () => {
  const chart = createTrendChart(SIX_WEEKS, [], {});

  expect(chart.xLabels.map((label) => label.x)).toEqual([46, 161.6, 277.2, 392.8, 508.4, 624]);
});

it("fits twelve weeks inside the width", () => {
  const chart = createTrendChart(TWELVE_WEEKS, [], {});

  expect([chart.xLabels[0]!.x, chart.xLabels.at(-1)!.x]).toEqual([46, 624]);
});

it("rounds coordinates to one decimal", () => {
  const series = [createSeries("3", [1 / 3, 2 / 3, 0.123456])];

  const chart = createTrendChart(["2026-W27", "2026-W28", "2026-W29"], series, {});

  const coordinates = firstSeries(chart).markers.flatMap((marker) => marker.points.flat());
  expect(coordinates.every((coordinate) => Math.round(coordinate * 10) / 10 === coordinate)).toBe(true);
});
