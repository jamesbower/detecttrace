// A trend chart's numbers as a table, behind a toggle so the chart stays the first read.
import { useId, useState } from "react";

import "./TrendTable.css";

import type { TrendMetricView } from "../view";

type TrendTableProps = {
  metric: TrendMetricView;
  caption: string;
};

export function TrendTable({ metric, caption }: TrendTableProps) {
  const [isOpen, setIsOpen] = useState(false);
  const tableId = useId();

  return (
    <div className="trend-table">
      <button
        type="button"
        className="trend-table-toggle"
        aria-expanded={isOpen}
        aria-controls={tableId}
        onClick={() => setIsOpen(!isOpen)}
      >
        {isOpen ? "Hide table" : "Show table"}
        <span className="visually-hidden">{`: ${metric.title}`}</span>
      </button>
      <div id={tableId} className="trend-table-scroll" hidden={!isOpen}>
        <table>
          <caption>{caption}</caption>
          <thead>
            <tr>
              <th scope="col">Week</th>
              {metric.lines.map((line, index) => (
                <th key={index} scope="col">
                  {line.label}
                </th>
              ))}
            </tr>
          </thead>
          <tbody>
            {metric.table_rows.map((row) => (
              <tr key={row.week}>
                <th scope="row">{row.week}</th>
                {row.cells.map((cell, index) => (
                  <td key={index}>{cell}</td>
                ))}
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    </div>
  );
}
