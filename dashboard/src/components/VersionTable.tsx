// One alert class's metrics, one row per version. Every string comes from the view as is.
import { Fragment, useId } from "react";

import { TERMS } from "../help/terms";
import { listPanels } from "../registry";
import { PanelSlot } from "./PanelSlot";
import { toSeriesClass } from "./series-class";
import { SeriesSwatch } from "./SeriesSwatch";
import { TermHint } from "./TermHint";
import { WarnIcon } from "./WarnIcon";
import "./VersionTable.css";

import type { PageProps } from "../registry";
import type { ClassView, MetricView, StripView, VersionRowView } from "../view";

type VersionTableProps = PageProps & { alertClass: ClassView };

const COLUMN_COUNT = 6;

export function VersionTable({ alertClass, view, results }: VersionTableProps) {
  const captionId = useId();
  const hasRowPanels = listPanels("version-row-detail").length > 0;

  return (
    <div className="version-table">
      <h2 className="version-table-heading">
        <span className="class-name">{alertClass.name}</span>{" "}
        <span className="version-table-sub">
          {alertClass.cases_text}
          {alertClass.versions_text !== "" && ` · ${alertClass.versions_text}`}
        </span>
      </h2>
      {/* The frame's clip-path would cut a focus ring drawn outside the scroll area, so the
          scroll area sits inside it, in the frame's padding. */}
      <div className="panel version-table-frame">
        <div className="version-table-scroll" role="region" tabIndex={0} aria-labelledby={captionId}>
          <table>
            <caption id={captionId}>
              {alertClass.name}: metrics per version, in the order each version first appeared. Ranges are 95%
              intervals.
            </caption>
            {/* Each measure's header is named by its label alone: its open pop-up would join the name. */}
            <thead>
              <tr>
                <th scope="col">Version</th>
                <th scope="col" className="version-table-num">
                  Cases
                </th>
                <th scope="col" aria-label={TERMS.completeness.label}>
                  <TermHint term="completeness" />
                </th>
                <th scope="col" aria-label={TERMS.agreement.label}>
                  <TermHint term="agreement" />
                </th>
                <th scope="col" aria-label={TERMS.kappa.label}>
                  <TermHint term="kappa" />
                </th>
                <th scope="col" className="version-table-num" aria-label={TERMS.dangerous.label}>
                  <TermHint term="dangerous" />
                </th>
              </tr>
            </thead>
            <tbody>
              {alertClass.rows.map((row) => (
                <Fragment key={row.label}>
                  <VersionRow row={row} />
                  {hasRowPanels && (
                    <tr className="version-table-detail">
                      <td colSpan={COLUMN_COUNT}>
                        <PanelSlot
                          name="version-row-detail"
                          view={view}
                          results={results}
                          classAnchor={alertClass.anchor}
                          versionLabel={row.label}
                        />
                      </td>
                    </tr>
                  )}
                </Fragment>
              ))}
            </tbody>
          </table>
        </div>
      </div>
      {alertClass.pooled_note !== null && <p className="version-table-note">{alertClass.pooled_note}</p>}
    </div>
  );
}

function VersionRow({ row }: { row: VersionRowView }) {
  const isRowFew = row.few_note !== null;
  return (
    <tr className={row.is_all ? "version-table-all" : undefined}>
      <th scope="row">
        <span className="version-table-label">
          <SeriesSwatch style={row.style} />
          <span>{row.label}</span>
        </span>
        {row.few_note !== null && <FewNote text={row.few_note} />}
      </th>
      <td className="version-table-num">{row.cases_text}</td>
      <MetricCell metric={row.completeness} style={row.style} isRowFew={isRowFew} />
      <MetricCell metric={row.agreement} style={row.style} isRowFew={isRowFew} />
      <MetricCell metric={row.kappa} style={row.style} isRowFew={isRowFew} />
      <td className="version-table-num">
        {row.is_dangerous ? (
          <span className="version-table-dangerous">
            <WarnIcon className="version-table-icon" />
            {row.dangerous_text}
          </span>
        ) : (
          <span>{row.dangerous_text}</span>
        )}
        {row.tp_without_agent_text !== null && (
          <span className="version-table-aside">
            <WarnIcon className="version-table-icon" />
            <span>{row.tp_without_agent_text}</span>
          </span>
        )}
      </td>
    </tr>
  );
}

// A row already marked "Few cases." does not repeat the mark in each cell.
function MetricCell({ metric, style, isRowFew }: { metric: MetricView; style: string; isRowFew: boolean }) {
  return (
    <td className="version-table-metric">
      <span className="version-table-value">{metric.value}</span>
      {metric.interval !== null && <span className="version-table-interval">{metric.interval}</span>}
      {metric.note !== null && <span className="version-table-why">{metric.note}</span>}
      {metric.strip !== null && <IntervalStrip strip={metric.strip} style={style} />}
      <span className="version-table-n">{metric.n_text}</span>
      {metric.few_note !== null && !isRowFew && <FewNote text={metric.few_note} />}
    </td>
  );
}

// The strip repeats the interval text drawn on a 0-100 scale, so screen readers skip it.
function IntervalStrip({ strip, style }: { strip: StripView; style: string }) {
  return (
    <svg
      className={`version-table-strip ${toSeriesClass(style)}`}
      viewBox="0 0 100 8"
      preserveAspectRatio="none"
      aria-hidden="true"
      focusable="false"
    >
      <rect className="strip-track" x="0" y="3" width="100" height="2" />
      <rect className="strip-range" x={strip.range_x} y="1" width={strip.range_width} height="6" />
      <rect className="strip-point" x={strip.point_left} y="0" width={strip.point_width} height="8" />
    </svg>
  );
}

function FewNote({ text }: { text: string }) {
  return (
    <span className="version-table-few">
      <svg className="version-table-icon" viewBox="0 0 16 16" aria-hidden="true" focusable="false">
        <circle cx="8" cy="8" r="6.5" strokeDasharray="2.2 2" />
        <circle className="icon-dot" cx="8" cy="8" r="1.6" />
      </svg>
      {text}
    </span>
  );
}
