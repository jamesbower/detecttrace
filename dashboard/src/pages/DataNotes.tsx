import { NoteList } from "../components/NoteList";
import { PageHead } from "../components/PageHead";
import { PanelSlot } from "../components/PanelSlot";
import { WarnIcon } from "../components/WarnIcon";
import "./DataNotes.css";

import type { PageProps } from "../registry";

export function DataNotes({ view, results }: PageProps) {
  return (
    <>
      <PageHead
        eyebrow="Data notes"
        title="Data notes"
        description="Input problems found while reading traces and verdicts, grouped, with how to fix them. Up to three examples each."
      />
      <ul className="coverage-list" aria-label="Coverage">
        {view.coverage.map((line, index) => (
          // The view's coverage lines are fixed for the page's life, so the index is a stable key.
          <li key={index} className="coverage-line" data-low={line.is_low}>
            {line.is_low && (
              <WarnIcon className="coverage-icon" />
            )}
            <span>
              {line.is_low && <strong>Warning: </strong>}
              {line.text}
              {line.hint !== null && <span className="coverage-hint">{line.hint}</span>}
            </span>
          </li>
        ))}
      </ul>
      <NoteList notes={view.notes} />
      <PanelSlot name="notes-after" view={view} results={results} />
    </>
  );
}
