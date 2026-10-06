import { DATA_PATH } from "../router";
import { PageHead } from "./PageHead";
import "./WaitingState.css";

import type { WaitingView } from "../view";

/** What `detecttrace serve` or `detecttrace ui` has received so far, shown until at least one
 * case can be scored. */
export function WaitingState({ waiting }: { waiting: WaitingView }) {
  return (
    <>
      <PageHead
        eyebrow="Waiting for data"
        title="Nothing to score yet"
        description="No case has both a settled trace and an analyst verdict yet, so there is nothing to score."
      />
      {waiting.next_step_text !== null && (
        <p className="waiting-next-step">
          <a href={`#${DATA_PATH}`}>{waiting.next_step_text}</a>
        </p>
      )}
      <dl className="waiting-counts">
        {waiting.counts.map((count) => (
          <div key={count.label} className="waiting-count">
            <dt>{count.label}</dt>
            <dd>{count.value}</dd>
          </div>
        ))}
      </dl>
      {waiting.notes.length > 0 && (
        <section className="waiting-notes" aria-labelledby="waiting-notes-title">
          <h2 id="waiting-notes-title">Data notes</h2>
          <ol>
            {waiting.notes.map((note, index) => (
              // Notes are not unique by text, and their order is fixed for the page's life.
              <li key={index}>
                <p>{note.message}</p>
                <p className="waiting-note-hint">{note.hint}</p>
              </li>
            ))}
          </ol>
        </section>
      )}
    </>
  );
}
