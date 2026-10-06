// A version series' key: its colour and dash pattern, next to a label that names it.
import "./series.css";

/** The class that gives an element its series' colour and dash, as --series-color and --series-dash. */
export function toSeriesClass(style: string): string {
  return `series-${style}`;
}

export function SeriesSwatch({ style }: { style: string }) {
  return (
    <svg className={`series-swatch ${toSeriesClass(style)}`} viewBox="0 0 30 12" aria-hidden="true" focusable="false">
      <line x1="1" y1="6" x2="29" y2="6" />
    </svg>
  );
}
