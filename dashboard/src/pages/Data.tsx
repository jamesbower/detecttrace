import { useCallback, useEffect, useRef, useState } from "react";

import { ConfigStep } from "../components/ConfigStep";
import { NoteList } from "../components/NoteList";
import { PageHead } from "../components/PageHead";
import { PanelSlot } from "../components/PanelSlot";
import { UploadCard } from "../components/UploadCard";
import { WarnIcon } from "../components/WarnIcon";
import { reloadWithNote, takeReloadNote } from "../reload-note";
import { clearData, readUiState } from "../ui-api";
import "./Data.css";

import type { ReactNode } from "react";
import type { Results } from "../results";
import type { ApiError, UiState } from "../ui-api";
import type { View } from "../view";

type DataProps = {
  view: View;
  /** Null on the ui app's waiting page, which opens here before anything can be scored. */
  results: Results | null;
};

export function Data({ view, results }: DataProps) {
  // The data notes describe a run's results, so the ui app's waiting page has none to show.
  const notes =
    results === null ? null : (
      <>
        <ul className="coverage-list" aria-label="Coverage" data-mode={view.mode}>
          {view.coverage.map((line, index) => (
            // The view's coverage lines are fixed for the page's life, so the index is a stable key.
            <li key={index} className="panel coverage-line" data-low={line.is_low}>
              {line.is_low && <WarnIcon className="coverage-icon" />}
              <span>
                {line.is_low && <strong>Warning: </strong>}
                {line.text}
                {line.hint !== null && <span className="coverage-hint">{line.hint}</span>}
              </span>
            </li>
          ))}
        </ul>
        {/* In the ui app the notes sit under the "3 · Data notes" step heading. */}
        <NoteList notes={view.notes} headingLevel={view.mode === "ui" ? 3 : 2} />
        <PanelSlot name="notes-after" view={view} results={results} />
      </>
    );
  return (
    <>
      <PageHead eyebrow="Data" title="Data" description={view.ui?.data_intro_text ?? DESCRIPTION} />
      {view.mode === "ui" ? <UiSteps notes={notes} updatedText={view.ui?.updated_text ?? ""} /> : notes}
    </>
  );
}

const DESCRIPTION =
  "Input problems found while reading traces and verdicts, grouped, with how to fix them. Up to three examples each.";
const UPLOAD_HEADING_ID = "data-step-upload";
const CONFIG_HEADING_ID = "data-step-config";
const NOTES_HEADING_ID = "data-step-notes";

type UiStepsProps = {
  notes: ReactNode;
  /** Announced after the page reloaded itself. */
  updatedText: string;
};

// The steps keep their numbers when one is not shown yet, so "3" always means the data notes.
function UiSteps({ notes, updatedText }: UiStepsProps) {
  const [state, setState] = useState<UiState | ApiError | null>(null);
  const announceRef = useRef<HTMLParagraphElement>(null);
  const requestCount = useRef(0);
  const pendingFocusId = useRef<string | null>(null);

  // Replies can arrive out of order, after an upload; only the newest request's is shown.
  const refreshState = useCallback(() => {
    requestCount.current += 1;
    const request = requestCount.current;
    void readUiState().then((result) => {
      if (request === requestCount.current) {
        setState(result);
      }
    });
  }, []);
  useEffect(refreshState, [refreshState]);

  // A reload the page made by itself left a note: say why the page changed, and put focus back
  // in the step the reader was in once the steps are shown.
  useEffect(() => {
    const note = takeReloadNote();
    if (note === null) {
      return;
    }
    pendingFocusId.current = note.focusId;
    // Written after the live region is in the page, so screen readers announce it.
    if (note.shouldAnnounce && announceRef.current !== null) {
      announceRef.current.textContent = updatedText;
    }
  }, [updatedText]);
  useEffect(() => {
    const focusId = pendingFocusId.current;
    if (state === null || focusId === null) {
      return;
    }
    pendingFocusId.current = null;
    (document.getElementById(focusId) ?? document.getElementById(UPLOAD_HEADING_ID))?.focus();
  }, [state]);

  const isReady = state !== null && !("error" in state);
  return (
    <div className="data-steps">
      {/* Empty from the first render; the effect above writes into it after a reload. */}
      <p ref={announceRef} className="visually-hidden" role="status" aria-live="polite" />
      <UploadStep state={state} onUploaded={refreshState} />
      {isReady && state.canConfigure && (
        <section className="data-step" aria-labelledby={CONFIG_HEADING_ID} data-reload-focus={CONFIG_HEADING_ID}>
          {/* Focusable from script only: focus returns here after the page reloads itself. */}
          <h2 id={CONFIG_HEADING_ID} className="data-step-title" tabIndex={-1}>
            2 · Configuration
          </h2>
          <ConfigStep
            isConfigured={state.isConfigured}
            totalsKey={[state.spanCountText, state.verdictCountText, ...state.checklistClasses].join("\n")}
          />
        </section>
      )}
      {notes !== null && (
        <section className="data-step" aria-labelledby={NOTES_HEADING_ID} data-reload-focus={NOTES_HEADING_ID}>
          <h2 id={NOTES_HEADING_ID} className="data-step-title" tabIndex={-1}>
            3 · Data notes
          </h2>
          {notes}
        </section>
      )}
    </div>
  );
}

function UploadStep({ state, onUploaded }: { state: UiState | ApiError | null; onUploaded: () => void }) {
  return (
    <section className="data-step" aria-labelledby={UPLOAD_HEADING_ID} data-reload-focus={UPLOAD_HEADING_ID}>
      <h2 id={UPLOAD_HEADING_ID} className="data-step-title" tabIndex={-1}>
        1 · Upload
      </h2>
      <div className="upload-cards">
        <UploadCard
          kind="traces"
          title="Traces"
          addText="Add trace files"
          acceptText=".jsonl, .json, .gz, .zst"
          accept=".jsonl,.json,.gz,.zst"
          onUploaded={onUploaded}
        />
        <UploadCard
          kind="verdicts"
          title="Verdicts"
          addText="Add verdict files"
          acceptText=".csv"
          accept=".csv"
          onUploaded={onUploaded}
        />
        <UploadCard
          kind="checklists"
          title="Checklists"
          addText="Add checklist files"
          acceptText=".yaml, .yml"
          accept=".yaml,.yml"
          onUploaded={onUploaded}
        />
      </div>
      <div className="panel upload-totals">
        <UploadTotals state={state} />
        <ClearData />
      </div>
    </section>
  );
}

// Not a live region: each upload card announces its own file, and the totals would repeat it.
function UploadTotals({ state }: { state: UiState | ApiError | null }) {
  return (
    <div className="upload-totals-text">
      {state !== null &&
        ("error" in state ? (
          <p className="upload-totals-error" role="alert">
            {state.error}
          </p>
        ) : (
          <dl className="upload-totals-list" aria-label="Stored so far">
            <div>
              <dt>Spans</dt>
              <dd>{state.spanCountText}</dd>
            </div>
            {state.traceFamilyText !== null && (
              <div>
                <dt>Trace format</dt>
                <dd>{state.traceFamilyText}</dd>
              </div>
            )}
            <div>
              <dt>Verdicts</dt>
              <dd>{state.verdictCountText}</dd>
            </div>
            <div>
              <dt>Checklists</dt>
              <dd>{state.checklistClassesText}</dd>
            </div>
          </dl>
        ))}
      {state !== null && !("error" in state) && state.checklistErrorText !== null && (
        <p className="upload-totals-error" role="alert">
          {state.checklistErrorText}
        </p>
      )}
    </div>
  );
}

function ClearData() {
  const dialogRef = useRef<HTMLDialogElement>(null);
  const openerRef = useRef<HTMLButtonElement>(null);
  const cancelRef = useRef<HTMLButtonElement>(null);
  const [isClearing, setIsClearing] = useState(false);
  const [errorText, setErrorText] = useState<string | null>(null);

  function handleOpen() {
    setErrorText(null);
    dialogRef.current?.showModal();
    // Cancel takes focus first, so a stray Enter never deletes the data.
    cancelRef.current?.focus();
  }

  async function handleConfirm() {
    if (isClearing) {
      return;
    }
    setIsClearing(true);
    const result = await clearData();
    if ("error" in result) {
      setIsClearing(false);
      setErrorText(result.error);
      return;
    }
    // The reload lands back on this page, empty; focus goes to the upload step.
    reloadWithNote({ focusId: UPLOAD_HEADING_ID, shouldAnnounce: false });
  }

  return (
    <>
      <button ref={openerRef} type="button" className="data-button data-button-danger" onClick={handleOpen}>
        Clear all data
      </button>
      <dialog
        ref={dialogRef}
        className="clear-dialog"
        aria-labelledby="clear-dialog-title"
        aria-describedby="clear-dialog-text"
        // Escape closes a modal dialog by itself; either way focus goes back to the opener.
        onClose={() => openerRef.current?.focus()}
      >
        <h2 id="clear-dialog-title" className="clear-dialog-title">
          Clear all data?
        </h2>
        <p id="clear-dialog-text">
          This deletes every uploaded trace, verdict and checklist, the configuration and the results. It cannot be undone.
        </p>
        {errorText !== null && (
          <p className="clear-dialog-error" role="alert">
            {errorText}
          </p>
        )}
        <div className="clear-dialog-actions">
          <button ref={cancelRef} type="button" className="data-button" onClick={() => dialogRef.current?.close()}>
            Cancel
          </button>
          <button
            type="button"
            className="data-button data-button-danger"
            // Not `disabled`, which would drop focus out of the dialog while the clear is on its way.
            aria-disabled={isClearing || undefined}
            onClick={() => void handleConfirm()}
          >
            {isClearing ? "Clearing…" : "Clear all data"}
          </button>
        </div>
      </dialog>
    </>
  );
}
