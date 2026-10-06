import { useId } from "react";

import { ClassSelector, getClassTabId } from "../components/ClassSelector";
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
        description="Share of cases where a step your checklist requires was not satisfied: not called, called with the wrong arguments, or failed."
      />
      <ClassSelector classes={view.classes} panelId={panelId} />
      {selected !== undefined && (
        <section
          key={selected.anchor}
          id={panelId}
          role="tabpanel"
          aria-labelledby={getClassTabId(selected.anchor)}
          className="analysis-panel"
        >
          <h2 className="analysis-panel-title">{selected.name}</h2>
          <Heatmap table={selected.skipped} alertClassName={selected.name} />
        </section>
      )}
    </>
  );
}
