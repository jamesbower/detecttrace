import { ClassSelector, getClassTabId, MAX_CLASS_TABS } from "../components/ClassSelector";
import { PageHead } from "../components/PageHead";
import { VersionTable } from "../components/VersionTable";
import { useSelectedClass } from "../use-selected-class";

import type { PageProps } from "../registry";

const PANEL_ID = "versions-class-panel";

export function Versions({ view, results }: PageProps) {
  const selected = useSelectedClass(view.classes);
  // A drop-down has no tab to name the panel, so the panel names itself.
  const hasTabs = view.classes.length <= MAX_CLASS_TABS;

  return (
    <>
      <PageHead
        eyebrow="Versions"
        title="By version"
        description="Evidence completeness, verdict agreement and chance-corrected agreement for each version, in the order each version first appeared, with n and 95% intervals. Chance-corrected agreement is Cohen's kappa: 0 means no better than chance, 1 means perfect agreement. Wide intervals mean few cases."
      />
      <ClassSelector classes={view.classes} panelId={PANEL_ID} />
      {selected !== undefined && (
        <section
          id={PANEL_ID}
          role={hasTabs ? "tabpanel" : undefined}
          aria-labelledby={hasTabs ? getClassTabId(selected.anchor) : undefined}
          aria-label={hasTabs ? undefined : selected.name}
        >
          <VersionTable alertClass={selected} view={view} results={results} />
        </section>
      )}
    </>
  );
}
