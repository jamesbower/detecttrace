// The key to a class's trend charts: each series' color, dash and marker, the version rule and
// the hollow marker for few cases.
import { TrendMarker } from "./TrendMarker";
import { createMarker } from "./trend-geometry";
import "./TrendChart.css";
import "./TrendLegend.css";

import type { TrendLineView } from "../view";

const SWATCH_WIDTH = 30;
const SWATCH_HEIGHT = 12;
const SWATCH_MARKER_RADIUS = 3.5;

type TrendLegendProps = {
  lines: readonly TrendLineView[];
  fewLegendText: string;
  alertClassName: string;
};

export function TrendLegend({ lines, fewLegendText, alertClassName }: TrendLegendProps) {
  const middleX = SWATCH_WIDTH / 2;
  const middleY = SWATCH_HEIGHT / 2;
  return (
    <ul className="trend-legend" aria-label={`Legend for ${alertClassName}`}>
      {lines.map((line, index) => (
        <li key={index}>
          <svg className="trend-swatch" viewBox={`0 0 ${SWATCH_WIDTH} ${SWATCH_HEIGHT}`} aria-hidden="true" focusable="false">
            {line.style === "all" ? (
              <line className="trend-line-all" x1={3} y1={middleY} x2={SWATCH_WIDTH - 3} y2={middleY} />
            ) : (
              <>
                <line className={`trend-line trend-c-${line.style}`} x1={1} y1={middleY} x2={SWATCH_WIDTH - 1} y2={middleY} />
                <TrendMarker
                  marker={createMarker(line.style, middleX, middleY, { radius: SWATCH_MARKER_RADIUS })}
                  style={line.style}
                />
              </>
            )}
          </svg>
          <span className="trend-legend-label">{line.label}</span>
        </li>
      ))}
      <li>
        <svg className="trend-swatch" viewBox={`0 0 ${SWATCH_WIDTH} ${SWATCH_HEIGHT}`} aria-hidden="true" focusable="false">
          <line className="trend-vmark" x1={middleX} y1={0} x2={middleX} y2={SWATCH_HEIGHT} />
        </svg>
        <span className="trend-legend-label">First week of a version</span>
      </li>
      <li>
        <svg className="trend-swatch" viewBox={`0 0 ${SWATCH_WIDTH} ${SWATCH_HEIGHT}`} aria-hidden="true" focusable="false">
          <TrendMarker
            marker={createMarker("none", middleX, middleY, { isFew: true, radius: SWATCH_MARKER_RADIUS })}
            style="none"
          />
        </svg>
        <span className="trend-legend-label">{fewLegendText}</span>
      </li>
    </ul>
  );
}
