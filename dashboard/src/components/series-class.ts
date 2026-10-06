/** The class that gives an element its version series' colour and dash, as --series-color and
 * --series-dash; series.css defines one per style key. */
export function toSeriesClass(style: string): string {
  return `series-${style}`;
}
