import type { Marker } from "./trend-geometry";

type TrendMarkerProps = { marker: Marker; style: string };

/** A series marker in its shape; hollow where the week has few cases. TrendChart.css styles it. */
export function TrendMarker({ marker, style }: TrendMarkerProps) {
  const className = `trend-marker trend-c-${style}${marker.isFew ? " is-few" : ""}`;
  if (marker.shape === "circle") {
    return <circle className={className} cx={marker.x} cy={marker.y} r={marker.r} />;
  }
  if (marker.shape === "square") {
    return <rect className={className} x={marker.left} y={marker.top} width={marker.side} height={marker.side} />;
  }
  return <polygon className={className} points={marker.points.map(([x, y]) => `${x},${y}`).join(" ")} />;
}
