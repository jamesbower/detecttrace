import { useId } from "react";

import { ClassSelector, getClassTabId } from "../components/ClassSelector";
import { PageHead } from "../components/PageHead";
import { PanelSlot } from "../components/PanelSlot";
import { TrendChart } from "../components/TrendChart";
import { TrendLegend } from "../components/TrendLegend";
import { useSelectedClass } from "../use-selected-class";
import "./analysis-panel.css";

import type { PageProps } from "../registry";

export function WeeklyTrend({ view, results }: PageProps) {
  const panelId = useId();
  const selected = useSelectedClass(view.classes);
  const trend = selected?.trend;
  // A class without a checklist has no completeness lines, so the legend takes the fuller set.
  const legendLines =
    trend === undefined
      ? []
      : trend.agreement.lines.length >= trend.completeness.lines.length
        ? trend.agreement.lines
        : trend.completeness.lines;

  return (
    <>
      <PageHead
        eyebrow="Weekly trend"
        title="Weekly trend"
        description="One line per version, with a point only in weeks where that version has cases, plus all versions together. ISO weeks in UTC."
      />
      <ClassSelector classes={view.classes} panelId={panelId} />
      {selected !== undefined && trend !== undefined && (
        <section
          key={selected.anchor}
          id={panelId}
          role="tabpanel"
          aria-labelledby={getClassTabId(selected.anchor)}
          className="analysis-panel"
        >
          <h2 className="analysis-panel-title">{selected.name}</h2>
          <p className="analysis-panel-lead">{`ISO weeks in UTC, ${trend.period_text}. n under each week counts all versions.`}</p>
          <TrendLegend lines={legendLines} fewLegendText={trend.few_legend_text} alertClassName={selected.name} />
          <TrendChart metric={trend.completeness} trend={trend} alertClassName={selected.name} />
          <TrendChart metric={trend.agreement} trend={trend} alertClassName={selected.name} />
        </section>
      )}
      <PanelSlot name="trend-footer" view={view} results={results} />
    </>
  );
}
