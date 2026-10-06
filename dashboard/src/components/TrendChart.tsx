// One metric's weekly trend for one class: a line per version plus all versions together, a
// dashed rule at each version's first week, and the same numbers in a table behind a toggle.
// Every point takes focus and names its week, series and value, read from the table rows.
import { useId, useState } from "react";

import { TrendMarker } from "./TrendMarker";
import { TrendTable } from "./TrendTable";
import { HIT_RADIUS, MARKER_RADIUS, createTrendChart } from "./trend-geometry";
import "./TrendChart.css";

import type { TrendMetricView, TrendView } from "../view";

type TrendChartProps = {
  metric: TrendMetricView;
  trend: TrendView;
  alertClassName: string;
};

export function TrendChart({ metric, trend, alertClassName }: TrendChartProps) {
  const captionId = useId();
  const [readout, setReadout] = useState<string | null>(null);

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

  function nameOf(seriesIndex: number, weekIndex: number): string {
    const week = trend.weeks[weekIndex] ?? "";
    const label = metric.lines[seriesIndex]?.label ?? "";
    const value = rowsByWeek.get(week)?.cells[seriesIndex] ?? "";
    return `${week}, ${label}: ${value}`;
  }

  return (
    <figure className="trend-chart" aria-labelledby={captionId}>
      <figcaption id={captionId} className="trend-chart-title">{`${metric.title} per week`}</figcaption>
      <div className="trend-chart-scroll">
        <svg
          className="trend-chart-svg"
          viewBox={`0 0 ${chart.width} ${chart.height}`}
          role="group"
          aria-labelledby={captionId}
        >
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
                    className={series.style === "all" ? "trend-line-all" : `trend-line trend-c-${series.style}`}
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
              return (
                <g
                  key={`${seriesIndex}-${point.index}`}
                  className="trend-point"
                  tabIndex={0}
                  role="img"
                  aria-label={name}
                  onFocus={() => setReadout(name)}
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
