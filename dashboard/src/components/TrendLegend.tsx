// The key to a class's trend charts: each series' swatch, the version rule and the hollow
// marker for few cases, drawn as a circle so it reads as hollow.
import { SeriesSwatch, SWATCH_MARKER_RADIUS, SWATCH_VIEW_BOX } from "./SeriesSwatch";
import { TrendMarker } from "./TrendMarker";
import { createMarker } from "./trend-geometry";
import "./TrendChart.css";
import "./TrendLegend.css";

import type { TrendLineView } from "../view";

const VIEW_BOX = `0 0 ${SWATCH_VIEW_BOX.width} ${SWATCH_VIEW_BOX.height}`;
const MIDDLE_X = SWATCH_VIEW_BOX.width / 2;
const MIDDLE_Y = SWATCH_VIEW_BOX.height / 2;
// The swatch draws past its box, so the rule stands a little taller than the text beside it.
const VMARK_OVERHANG = 3;

type TrendLegendProps = {
  lines: readonly TrendLineView[];
  fewLegendText: string;
  alertClassName: string;
};

export function TrendLegend({ lines, fewLegendText, alertClassName }: TrendLegendProps) {
  return (
    <ul className="trend-legend" aria-label={`Legend for ${alertClassName}`}>
      {lines.map((line, index) => (
        <li key={index}>
          <SeriesSwatch style={line.style} />
          <span className="trend-legend-label">{line.label}</span>
        </li>
      ))}
      <li>
        <svg className="series-swatch" viewBox={VIEW_BOX} aria-hidden="true" focusable="false">
          <line
            className="trend-vmark"
            x1={MIDDLE_X}
            y1={-VMARK_OVERHANG}
            x2={MIDDLE_X}
            y2={SWATCH_VIEW_BOX.height + VMARK_OVERHANG}
          />
        </svg>
        <span className="trend-legend-label">First week of a version</span>
      </li>
      <li>
        <svg className="series-swatch" viewBox={VIEW_BOX} aria-hidden="true" focusable="false">
          <TrendMarker
            marker={createMarker("all", MIDDLE_X, MIDDLE_Y, { isFew: true, radius: SWATCH_MARKER_RADIUS })}
            style="all"
          />
        </svg>
        <span className="trend-legend-label">{fewLegendText}</span>
      </li>
    </ul>
  );
}
