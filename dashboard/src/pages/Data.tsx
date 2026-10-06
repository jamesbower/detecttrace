import { useCallback, useEffect, useRef, useState } from "react";

import { ConfigStep } from "../components/ConfigStep";
import { NoteList } from "../components/NoteList";
import { PageHead } from "../components/PageHead";
import { PanelSlot } from "../components/PanelSlot";
import { UploadCard } from "../components/UploadCard";
import { WarnIcon } from "../components/WarnIcon";
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
        <NoteList notes={view.notes} />
        <PanelSlot name="notes-after" view={view} results={results} />
      </>
    );
  return (
    <>
      <PageHead
        eyebrow="Data"
        title="Data"
        description="Input problems found while reading traces and verdicts, grouped, with how to fix them. Up to three examples each."
      />
      {view.mode === "ui" ? <UiSteps notes={notes} /> : notes}
    </>
  );
}

// The steps keep their numbers when one is not shown yet, so "3" always means the data notes.
function UiSteps({ notes }: { notes: ReactNode }) {
  const [state, setState] = useState<UiState | ApiError | null>(null);
  const refreshState = useCallback(() => {
    void readUiState().then(setState);
  }, []);
  useEffect(refreshState, [refreshState]);
  const isReady = state !== null && !("error" in state);
  return (
    <div className="data-steps">
      <UploadStep state={state} onUploaded={refreshState} />
      {isReady && state.canConfigure && (
        <section className="data-step" aria-labelledby="data-step-config">
          <h2 id="data-step-config" className="data-step-title">
            2 · Configuration
          </h2>
          <ConfigStep
            isConfigured={state.isConfigured}
            totalsKey={[state.spanCountText, state.verdictCountText, ...state.checklistClasses].join("\n")}
          />
        </section>
      )}
      {notes !== null && (
        <section className="data-step" aria-labelledby="data-step-notes">
          <h2 id="data-step-notes" className="data-step-title">
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
    <section className="data-step" aria-labelledby="data-step-upload">
      <h2 id="data-step-upload" className="data-step-title">
        1 · Upload
      </h2>
      <div className="upload-cards">
        <UploadCard
          kind="traces"
          title="Traces"
          acceptText=".jsonl, .json, .gz, .zst"
          accept=".jsonl,.json,.gz,.zst"
          onUploaded={onUploaded}
        />
        <UploadCard kind="verdicts" title="Verdicts" acceptText=".csv" accept=".csv" onUploaded={onUploaded} />
        <UploadCard
          kind="checklists"
          title="Checklists"
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

function UploadTotals({ state }: { state: UiState | ApiError | null }) {
  // Kept in the tree from the first render, so a changed total is announced.
  return (
    <div className="upload-totals-text" role="status" aria-live="polite">
      {state !== null &&
        ("error" in state ? (
          <p className="upload-totals-error">{state.error}</p>
        ) : (
          <dl className="upload-totals-list" aria-label="Stored so far">
            <div>
              <dt>Traces</dt>
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
              <dd>{state.checklistClasses.length === 0 ? "None" : state.checklistClasses.join(", ")}</dd>
            </div>
          </dl>
        ))}
      {state !== null && !("error" in state) && state.checklistErrorText !== null && (
        <p className="upload-totals-error">{state.checklistErrorText}</p>
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
    setIsClearing(true);
    const result = await clearData();
    if ("error" in result) {
      setIsClearing(false);
      setErrorText(result.error);
      return;
    }
    location.reload();
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
            disabled={isClearing}
            onClick={() => void handleConfirm()}
          >
            Clear all data
          </button>
        </div>
      </dialog>
    </>
  );
}
