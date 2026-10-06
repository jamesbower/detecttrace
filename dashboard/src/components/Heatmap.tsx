// The skipped-step table: checklist steps down, versions across. Each cell shows the share
// skipped as text; its tint only repeats that share.
import { useId } from "react";

import { SeriesSwatch } from "./SeriesSwatch";
import "./Heatmap.css";

import type * as React from "react";
import type { SkipTableView } from "../view";

type HeatmapProps = {
  table: SkipTableView;
  /** Names the table for its caption, as "<alertClassName>: skipped-step rate per version". */
  alertClassName: string;
};

export function Heatmap({ table, alertClassName }: HeatmapProps) {
  const captionId = useId();

  if (table.empty_text !== null) {
    return <p className="heatmap-empty">{table.empty_text}</p>;
  }

  return (
    <div className="heatmap">
      <p className="heatmap-sub">{`${table.steps_text} · share of cases where the step was not satisfied`}</p>
      <div className="heatmap-scroll" role="region" tabIndex={0} aria-labelledby={captionId}>
        <table className="heatmap-table">
          <caption id={captionId} className="visually-hidden">
            {`${alertClassName}: skipped-step rate per version, side by side.`}
          </caption>
          <thead>
            <tr>
              <th scope="col">Checklist step</th>
              {table.columns.map((column, index) => (
                <th key={index} scope="col" className={column.few_note === null ? undefined : "is-few"}>
                  <span className="heatmap-label">
                    <SeriesSwatch style={column.style} />
                    {column.label}
                  </span>{" "}
                  <span className="heatmap-of">{column.n_text}</span>
                  {column.few_note !== null && <>{" "}<span className="heatmap-few">{column.few_note}</span></>}
                </th>
              ))}
            </tr>
          </thead>
          <tbody>
            {table.rows.map((row) => (
              <tr key={row.item}>
                <th scope="row">
                  <code>{row.item}</code>
                </th>
                {row.cells.map((cell, index) => {
                  // A column marked few already says so once; its cells need not repeat it.
                  const isColumnFew = table.columns[index]?.few_note != null;
                  return (
                    <td
                      key={index}
                      className={cell.few_note === null ? "heatmap-cell" : "heatmap-cell is-few"}
                      style={{ "--heat-level": cell.bar_width } as React.CSSProperties}
                    >
                      <span className="heatmap-rate">{cell.rate_text}</span>{" "}
                      <span className="heatmap-of">{cell.count_text}</span>
                      {cell.few_note !== null && !isColumnFew && <>{" "}<span className="heatmap-few">{cell.few_note}</span></>}
                    </td>
                  );
                })}
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    </div>
  );
}
