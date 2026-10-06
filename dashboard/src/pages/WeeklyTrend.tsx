import { useId } from "react";

import { ClassPanel, ClassSelector } from "../components/ClassSelector";
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
        description="One line per version, with a point only in weeks where that version has cases, plus all versions together. A dashed rule marks the first week of each version. A version that appears again later is a rollback."
      />
      <ClassSelector classes={view.classes} panelId={panelId} />
      {selected !== undefined && trend !== undefined && (
        <ClassPanel classes={view.classes} selected={selected} panelId={panelId} className="analysis-panel">
          <h2 className="analysis-panel-title">{selected.name}</h2>
          <p className="analysis-panel-lead">{`ISO weeks in UTC, ${trend.period_text}. n under each week counts all versions.`}</p>
          <TrendLegend lines={legendLines} fewLegendText={trend.few_legend_text} alertClassName={selected.name} />
          <TrendChart metric={trend.completeness} trend={trend} alertClassName={selected.name} />
          <TrendChart metric={trend.agreement} trend={trend} alertClassName={selected.name} />
        </ClassPanel>
      )}
      <PanelSlot name="trend-footer" view={view} results={results} />
    </>
  );
}
