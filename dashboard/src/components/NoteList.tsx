// The data notes: each problem found in the input, with its severity, how to fix it and a
// few examples. Every text is the view's, already made visible by the Python side.
import { WarnIcon } from "./WarnIcon";
import "./NoteList.css";

import type { NoteView } from "../view";

const INVALID_INPUT = "invalid_input";

export function NoteList({ notes }: { notes: readonly NoteView[] }) {
  if (notes.length === 0) {
    return <p className="note-list-empty">No input problems were found.</p>;
  }
  return (
    <ol className="note-list">
      {notes.map((note, index) => {
        const isInvalid = note.severity === INVALID_INPUT;
        return (
          // The view's note order is fixed for the page's life, so the index is a stable key.
          <li key={index} className="note" data-severity={isInvalid ? "invalid" : "warning"}>
            <p className="note-severity">
              {isInvalid ? (
                <svg className="note-icon" viewBox="0 0 16 16" aria-hidden="true" focusable="false">
                  <path d="M8 1.5a6.5 6.5 0 100 13 6.5 6.5 0 000-13zM5.5 5.5l5 5M10.5 5.5l-5 5" />
                </svg>
              ) : (
                <WarnIcon className="note-icon" />
              )}
              {note.severity_label}
            </p>
            <h2 className="note-title">
              <strong>{note.count_text}</strong> {note.message}
            </h2>
            {note.hint !== "" && <p className="note-hint">{note.hint}</p>}
            {note.examples.length > 0 && (
              <ul className="note-examples" aria-label="Examples">
                {note.examples.map((example, exampleIndex) => (
                  <li key={exampleIndex}>
                    <code>{example.subject}</code>
                    {example.detail !== null && `: ${example.detail}`}
                  </li>
                ))}
                {note.more_text !== null && <li className="note-more">{note.more_text}</li>}
              </ul>
            )}
          </li>
        );
      })}
    </ol>
  );
}
