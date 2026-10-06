import { useId } from "react";

import { ClassPanel, ClassSelector } from "../components/ClassSelector";
import { Heatmap } from "../components/Heatmap";
import { PageHead } from "../components/PageHead";
import { useSelectedClass } from "../use-selected-class";
import "./analysis-panel.css";

import type { PageProps } from "../registry";

export function SkippedSteps({ view }: PageProps) {
  const panelId = useId();
  const selected = useSelectedClass(view.classes);

  return (
    <>
      <PageHead
        eyebrow="Skipped steps"
        title="Skipped steps"
        description="Share of cases where a step your checklist requires was not satisfied: not called, called with the wrong arguments, or failed. Read across a row to compare versions."
      />
      <ClassSelector classes={view.classes} panelId={panelId} />
      {selected !== undefined && (
        <ClassPanel classes={view.classes} selected={selected} panelId={panelId} className="panel analysis-panel">
          <h2 className="analysis-panel-title">
            <span className="class-name">{selected.name}</span>
          </h2>
          <Heatmap table={selected.skipped} alertClassName={selected.name} />
        </ClassPanel>
      )}
    </>
  );
}
