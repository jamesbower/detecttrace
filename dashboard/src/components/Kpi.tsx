import "./Kpi.css";

import type * as React from "react";

type KpiProps = {
  label: React.ReactNode;
  value: React.ReactNode;
  context?: React.ReactNode;
};

/** One headline figure. Place inside a `<dl>`: the label is its term, the value its description. */
export function Kpi({ label, value, context }: KpiProps) {
  return (
    <div className="kpi">
      <dt className="kpi-label">{label}</dt>
      <dd className="kpi-value">{value}</dd>
      {context !== undefined && <dd className="kpi-context">{context}</dd>}
    </div>
  );
}
