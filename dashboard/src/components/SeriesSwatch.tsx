// A version series' key: its line, dash pattern and marker shape, as the trend charts draw
// them, next to a label that names the series. series.css gives each style its colour and dash.
import { toSeriesClass } from "./series-class";
import { TrendMarker } from "./TrendMarker";
import { createMarker } from "./trend-geometry";
import "./series.css";

// The swatch's view box, in SVG units; series.css sizes it with --swatch-width and --swatch-height.
export const SWATCH_VIEW_BOX = { width: 30, height: 12 } as const;
const MIDDLE_X = SWATCH_VIEW_BOX.width / 2;
const MIDDLE_Y = SWATCH_VIEW_BOX.height / 2;
export const SWATCH_MARKER_RADIUS = 3.5;

export function SeriesSwatch({ style }: { style: string }) {
  return (
    <svg
      className="series-swatch"
      viewBox={`0 0 ${SWATCH_VIEW_BOX.width} ${SWATCH_VIEW_BOX.height}`}
      aria-hidden="true"
      focusable="false"
    >
      {style === "all" ? (
        <line className="series-line-all" x1={3} y1={MIDDLE_Y} x2={SWATCH_VIEW_BOX.width - 3} y2={MIDDLE_Y} />
      ) : (
        <>
          <line
            className={`series-line ${toSeriesClass(style)}`}
            x1={1}
            y1={MIDDLE_Y}
            x2={SWATCH_VIEW_BOX.width - 1}
            y2={MIDDLE_Y}
          />
          <TrendMarker marker={createMarker(style, MIDDLE_X, MIDDLE_Y, { radius: SWATCH_MARKER_RADIUS })} style={style} />
        </>
      )}
    </svg>
  );
}
