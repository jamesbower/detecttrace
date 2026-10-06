// One kind of input file in the ui app's upload step: a labelled file input and a drop zone
// that feed the same queue. Files go up one at a time, in order, and each one's result is the
// server's text, shown as text.
import { useId, useRef, useState } from "react";

import { uploadFile } from "../ui-api";
import { NoteList } from "./NoteList";
import "./UploadCard.css";

import type { DragEvent } from "react";
import type { UploadKind, UploadResult } from "../ui-api";

type UploadCardProps = {
  kind: UploadKind;
  title: string;
  /** The accepted suffixes, as the reader sees them. */
  acceptText: string;
  /** The input's `accept` list. */
  accept: string;
  /** Called after each file, refused or not, so the page can refresh its totals. */
  onUploaded: () => void;
};

type FileOutcome = { readonly name: string; readonly result: UploadResult };

export function UploadCard({ kind, title, acceptText, accept, onUploaded }: UploadCardProps) {
  const inputId = useId();
  const titleId = useId();
  const [outcomes, setOutcomes] = useState<readonly FileOutcome[]>([]);
  const [statusText, setStatusText] = useState("");
  const [isUploading, setIsUploading] = useState(false);
  const [isDragOver, setIsDragOver] = useState(false);
  // State lags a render behind; a drop that lands mid-upload must see the queue as busy at once.
  const isBusyRef = useRef(false);
  const inputRef = useRef<HTMLInputElement>(null);

  async function handleFiles(files: readonly File[]) {
    if (files.length === 0 || isBusyRef.current) {
      return;
    }
    isBusyRef.current = true;
    const wasInputFocused = document.activeElement === inputRef.current;
    setIsUploading(true);
    for (const file of files) {
      setStatusText(`Uploading ${file.name}…`);
      const result = await uploadFile(kind, file);
      setOutcomes((previous) => [...previous, { name: file.name, result }]);
      setStatusText(`${file.name}: ${"error" in result ? result.error : result.storedText}`);
      onUploaded();
    }
    isBusyRef.current = false;
    setIsUploading(false);
    // Disabling the focused input drops keyboard focus to the page; give it back.
    if (wasInputFocused && (document.activeElement === null || document.activeElement === document.body)) {
      requestAnimationFrame(() => inputRef.current?.focus());
    }
  }

  function handleDragOver(event: DragEvent<HTMLDivElement>) {
    // Without this the browser opens the dropped file instead of handing it to the page.
    event.preventDefault();
    setIsDragOver(true);
  }

  function handleDrop(event: DragEvent<HTMLDivElement>) {
    event.preventDefault();
    setIsDragOver(false);
    void handleFiles(Array.from(event.dataTransfer.files));
  }

  return (
    <section className="panel upload-card" aria-labelledby={titleId}>
      <h3 id={titleId} className="upload-card-title">
        {title}
      </h3>
      <p className="upload-card-accept">{acceptText}</p>
      <div
        className="upload-drop"
        data-drag-over={isDragOver}
        onDragOver={handleDragOver}
        onDragLeave={() => setIsDragOver(false)}
        onDrop={handleDrop}
      >
        <label htmlFor={inputId} className="upload-label">
          Choose files<span className="visually-hidden"> for {title}</span>
        </label>
        <input
          ref={inputRef}
          id={inputId}
          className="upload-input"
          type="file"
          multiple
          accept={accept}
          disabled={isUploading}
          onChange={(event) => {
            const input = event.currentTarget;
            const files = Array.from(input.files ?? []);
            // Cleared, so choosing the same file again uploads it again.
            input.value = "";
            void handleFiles(files);
          }}
        />
        <p className="upload-drop-hint">or drop files here</p>
      </div>
      <p className="upload-status" role="status" aria-live="polite">
        {statusText}
      </p>
      {outcomes.length > 0 && (
        <ol className="upload-outcomes" aria-label={`${title} uploads`}>
          {outcomes.map((outcome, index) => (
            // Outcomes are only ever appended, so the index is a stable key.
            <li key={index} className="upload-outcome">
              <p className="upload-file-name">
                <code>{outcome.name}</code>
              </p>
              {"error" in outcome.result ? (
                <p className="upload-error">
                  <strong>Not stored: </strong>
                  {outcome.result.error}
                </p>
              ) : (
                <>
                  <p className="upload-stored">{outcome.result.storedText}</p>
                  <NoteList notes={outcome.result.problems} />
                </>
              )}
            </li>
          ))}
        </ol>
      )}
    </section>
  );
}
