// One class's confusion matrix: analyst verdict down, agent verdict across. A cell where a
// dangerous false close would fall is outlined and says so in text, so color is never the cue.
import { useId } from "react";

import { useIsScrollable } from "../use-is-scrollable";
import { WarnIcon } from "./WarnIcon";
import "./Matrix.css";

import type { ConfusionView } from "../view";

type MatrixProps = {
  confusion: ConfusionView;
  /** Names the table in its caption, so each class's matrix has its own name. */
  alertClassName: string;
};

export function Matrix({ confusion, alertClassName }: MatrixProps) {
  const captionId = useId();
  const [scrollRef, isScrollable] = useIsScrollable<HTMLDivElement>();

  return (
    <div
      ref={scrollRef}
      className="matrix-scroll"
      role="region"
      tabIndex={isScrollable ? 0 : undefined}
      aria-labelledby={captionId}
    >
      <table className="matrix">
        <caption id={captionId} className="matrix-caption">
          {`${alertClassName}: rows are the analyst verdict, columns the agent verdict.`}
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
                        <WarnIcon className="matrix-flag-icon" />
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
