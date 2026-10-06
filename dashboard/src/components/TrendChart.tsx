// One metric's weekly trend for one class: a line per version plus all versions together, a
// dashed rule at each version's first week, and the same numbers in a table behind a toggle.
// Every point names its week, series and value, read from the table rows. The chart is one tab
// stop (a roving tabindex): ArrowLeft and ArrowRight move between a series' weeks, Home and End
// to its first and last; ArrowUp and ArrowDown move to the series above or below in the legend's
// order, at the same week or the nearest one that series has.
import { useId, useState } from "react";

import { useIsScrollable } from "../use-is-scrollable";
import { toSeriesClass } from "./series-class";
import { TrendMarker } from "./TrendMarker";
import { TrendTable } from "./TrendTable";
import { HIT_RADIUS, MARKER_RADIUS, createTrendChart } from "./trend-geometry";
import "./TrendChart.css";

import type * as React from "react";
import type { ChartSeries } from "./trend-geometry";
import type { TrendMetricView, TrendView } from "../view";

const SCROLL_LABEL = "Chart, scrolls sideways";

/** A point by its series and its week's index in the trend's weeks. */
type PointKey = { series: number; week: number };

type TrendChartProps = {
  metric: TrendMetricView;
  trend: TrendView;
  alertClassName: string;
};

export function TrendChart({ metric, trend, alertClassName }: TrendChartProps) {
  const captionId = useId();
  const [readout, setReadout] = useState<string | null>(null);
  const [active, setActive] = useState<PointKey | null>(null);
  const [scrollRef, isScrollable] = useIsScrollable<HTMLDivElement>();

  if (metric.empty_text !== null) {
    return (
      <figure className="trend-chart" aria-labelledby={captionId}>
        <figcaption id={captionId} className="trend-chart-title">{`${metric.title} per week`}</figcaption>
        <p className="trend-chart-empty">{metric.empty_text}</p>
      </figure>
    );
  }

  const chart = createTrendChart(trend.weeks, metric.lines, trend.version_first_weeks);
  const rowsByWeek = new Map(metric.table_rows.map((row) => [row.week, row]));

  // A point from another metric or class falls back to the first point.
  const tabStop = active !== null && hasPoint(chart.series, active) ? active : findFirstPoint(chart.series);

  function handleKeyDown(event: React.KeyboardEvent<SVGGElement>, from: PointKey) {
    const target = findTargetPoint(chart.series, from, event.key);
    if (target === null) {
      return;
    }
    event.preventDefault();
    setActive(target);
    event.currentTarget.ownerSVGElement
      ?.querySelector<SVGGElement>(`[data-point="${target.series}-${target.week}"]`)
      ?.focus();
  }

  function nameOf(seriesIndex: number, weekIndex: number): string {
    const week = trend.weeks[weekIndex] ?? "";
    const label = metric.lines[seriesIndex]?.label ?? "";
    const value = rowsByWeek.get(week)?.cells[seriesIndex] ?? "";
    return `${week}, ${label}: ${value}`;
  }

  return (
    <figure className="trend-chart" aria-labelledby={captionId}>
      <figcaption id={captionId} className="trend-chart-title">{`${metric.title} per week`}</figcaption>
      {/* The figure's caption names the chart once. Only a chart wider than the screen is a
          region of its own, a tab stop the keyboard can scroll, named for what it does. */}
      <div
        ref={scrollRef}
        className="trend-chart-scroll"
        role={isScrollable ? "region" : undefined}
        tabIndex={isScrollable ? 0 : undefined}
        aria-label={isScrollable ? SCROLL_LABEL : undefined}
      >
        <svg className="trend-chart-svg" viewBox={`0 0 ${chart.width} ${chart.height}`}>
          <g aria-hidden="true">
            {chart.grid.map((line) => (
              <g key={line.label}>
                <line className="trend-grid" x1={line.xStart} y1={line.y} x2={line.xEnd} y2={line.y} />
                <text className="trend-axis" x={line.labelX} y={line.labelY} textAnchor="end">
                  {line.label}
                </text>
              </g>
            ))}
            {chart.xLabels.map((label) => (
              <g key={label.x}>
                <text className="trend-axis" x={label.x} y={label.yWeek} textAnchor="middle">
                  {label.week}
                </text>
                <text className="trend-count" x={label.x} y={label.yCount} textAnchor="middle">
                  {label.countText}
                </text>
              </g>
            ))}
            {chart.versionMarkers.map((marker) => (
              <g key={marker.x}>
                <line className="trend-vmark" x1={marker.x} y1={marker.yTop} x2={marker.x} y2={marker.yBottom} />
                <text className="trend-vmark-text" x={marker.labelX} y={marker.labelY} textAnchor={marker.anchor}>
                  {marker.label}
                </text>
              </g>
            ))}
            {chart.series.map((series, index) => (
              <g key={index}>
                {series.path !== "" && (
                  <path
                    className={series.style === "all" ? "series-line-all" : `series-line ${toSeriesClass(series.style)}`}
                    d={series.path}
                  />
                )}
                {series.markers.map((marker) => (
                  <TrendMarker key={marker.x} marker={marker} style={series.style} />
                ))}
              </g>
            ))}
          </g>
          {chart.series.map((series, seriesIndex) =>
            series.points.map((point) => {
              const name = nameOf(seriesIndex, point.index);
              const key = { series: seriesIndex, week: point.index };
              const isTabStop = tabStop !== null && tabStop.series === seriesIndex && tabStop.week === point.index;
              return (
                <g
                  key={`${seriesIndex}-${point.index}`}
                  data-point={`${seriesIndex}-${point.index}`}
                  className="trend-point"
                  tabIndex={isTabStop ? 0 : -1}
                  role="img"
                  aria-label={name}
                  onKeyDown={(event) => handleKeyDown(event, key)}
                  onFocus={() => {
                    setActive(key);
                    setReadout(name);
                  }}
                  onBlur={() => setReadout(null)}
                  onPointerEnter={() => setReadout(name)}
                  onPointerLeave={() => setReadout(null)}
                >
                  <circle className="trend-point-hit" cx={point.x} cy={point.y} r={HIT_RADIUS} />
                  <circle className="trend-point-ring" cx={point.x} cy={point.y} r={2 * MARKER_RADIUS} />
                </g>
              );
            }),
          )}
        </svg>
      </div>
      {/* Repeats the focused point's name for sighted keyboard users; a screen reader already has it. */}
      <p className="trend-chart-readout" aria-hidden="true">
        {readout ?? ""}
      </p>
      <TrendTable metric={metric} caption={`${alertClassName}: ${metric.title} per week, ${trend.period_text}`} />
    </figure>
  );
}

function hasPoint(series: readonly ChartSeries[], key: PointKey): boolean {
  return series[key.series]?.points.some((point) => point.index === key.week) ?? false;
}

function findFirstPoint(series: readonly ChartSeries[]): PointKey | null {
  const seriesIndex = series.findIndex((item) => item.points.length > 0);
  return seriesIndex === -1 ? null : { series: seriesIndex, week: series[seriesIndex]!.points[0]!.index };
}

function findTargetPoint(series: readonly ChartSeries[], from: PointKey, key: string): PointKey | null {
  const points = series[from.series]!.points;
  const position = points.findIndex((point) => point.index === from.week);
  switch (key) {
    case "ArrowRight":
      return toKey(from.series, points[position + 1]?.index);
    case "ArrowLeft":
      return toKey(from.series, points[position - 1]?.index);
    case "Home":
      return toKey(from.series, points[0]?.index);
    case "End":
      return toKey(from.series, points.at(-1)?.index);
    case "ArrowDown":
      return findInNextSeries(series, from, 1);
    case "ArrowUp":
      return findInNextSeries(series, from, -1);
    default:
      return null;
  }
}

function toKey(seriesIndex: number, week: number | undefined): PointKey | null {
  return week === undefined ? null : { series: seriesIndex, week };
}

// The nearest series in that direction with any point, at the same week or the nearest one;
// a tie goes to the earlier week.
function findInNextSeries(series: readonly ChartSeries[], from: PointKey, step: 1 | -1): PointKey | null {
  for (let index = from.series + step; index >= 0 && index < series.length; index += step) {
    const points = series[index]!.points;
    if (points.length > 0) {
      const nearest = points.reduce((best, point) =>
        Math.abs(point.index - from.week) < Math.abs(best.index - from.week) ? point : best,
      );
      return { series: index, week: nearest.index };
    }
  }
  return null;
}
