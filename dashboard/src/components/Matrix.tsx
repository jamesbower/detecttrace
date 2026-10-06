// One class's confusion matrix: analyst verdict down, agent verdict across. A cell where a
// dangerous false close would fall is outlined and says so in text, so color is never the cue.
import { useId } from "react";

import "./Matrix.css";

import type { ConfusionView } from "../view";

export function Matrix({ confusion }: { confusion: ConfusionView }) {
  const captionId = useId();

  return (
    <div className="matrix-scroll" role="region" tabIndex={0} aria-labelledby={captionId}>
      <table className="matrix">
        <caption id={captionId} className="matrix-caption">
          Rows are the analyst verdict, columns the agent verdict.
        </caption>
        <thead>
          <tr>
            <th scope="col">
              <span className="visually-hidden">Analyst verdict by agent verdict</span>
            </th>
            {confusion.column_labels.map((label) => (
              <th key={label} scope="col">
                {label}
              </th>
            ))}
          </tr>
        </thead>
        <tbody>
          {confusion.rows.map((row) => (
            <tr key={row.label}>
              <th scope="row">{row.label}</th>
              {row.cells.map((cell, index) => (
                <td key={index} className={toCellClass(cell.heat, cell.is_dangerous, cell.is_flagged)}>
                  <span className="matrix-count">{cell.count_text}</span>
                  {/* The space keeps "3 Dangerous" two words for a screen reader. */}
                  {cell.is_dangerous && " "}
                  {cell.is_dangerous && (
                    <span className="matrix-flag">
                      {cell.is_flagged && (
                        <svg className="matrix-flag-icon" viewBox="0 0 16 16" aria-hidden="true" focusable="false">
                          <path d="M8 1.8 15 14H1zM8 6.2v3.6M8 11.8v.2" />
                        </svg>
                      )}
                      Dangerous
                    </span>
                  )}
                </td>
              ))}
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}

function toCellClass(heat: string, isDangerous: boolean, isFlagged: boolean): string {
  const classes = ["matrix-cell", `matrix-${heat}`];
  if (isDangerous) {
    classes.push("is-dangerous");
  }
  if (isFlagged) {
    classes.push("is-flagged");
  }
  return classes.join(" ");
}
