import { useId } from "react";

import { ClassPanel, ClassSelector } from "../components/ClassSelector";
import { PageHead } from "../components/PageHead";
import { VersionTable } from "../components/VersionTable";
import { useSelectedClass } from "../use-selected-class";

import type { PageProps } from "../registry";

export function Versions({ view, results }: PageProps) {
  const panelId = useId();
  const selected = useSelectedClass(view.classes);

  return (
    <>
      <PageHead
        eyebrow="Versions"
        title="By version"
        description="Evidence completeness, verdict agreement and chance-corrected agreement for each version, in the order each version first appeared, with n and 95% intervals. Chance-corrected agreement is Cohen's kappa: 0 means no better than chance, 1 means perfect agreement. Wide intervals mean few cases."
      />
      <ClassSelector classes={view.classes} panelId={panelId} />
      {selected !== undefined && (
        <ClassPanel classes={view.classes} selected={selected} panelId={panelId}>
          <VersionTable alertClass={selected} view={view} results={results} />
        </ClassPanel>
      )}
    </>
  );
}
