// The ui app's configuration step: the server proposes which trace attribute holds each field
// and the reader corrects it, maps any unknown verdict labels and confirms. Every sentence on it
// is the server's; the page only sends back what the reader changed.
import { useCallback, useEffect, useEffectEvent, useId, useRef, useState } from "react";

import { setReloadHeld } from "../reload-hold";
import { readProposal, saveConfig } from "../ui-api";
import "./ConfigStep.css";

import type { ConfigBody, Proposal, ProposalField } from "../ui-api";

type ConfigStepProps = {
  /** A configuration is already saved, so the step opens as its summary. */
  isConfigured: boolean;
  /** Changes whenever the stored totals do, so the proposal is read again after an upload. */
  totalsKey: string;
};

type Labels = { label_map: Record<string, string>; agent_label_map: Record<string, string> };
type LabelMapName = keyof Labels;

export const SUGGESTIONS_ID = "dt-config-suggestions";
export const PROPOSAL_DELAY_MS = 400;
const NOT_MAPPED = "";
const EMPTY_LABELS: Labels = { label_map: {}, agent_label_map: {} };
const EMPTY_BODY_KEY = JSON.stringify({ fields: {}, labels: EMPTY_LABELS });

export function ConfigStep({ isConfigured, totalsKey }: ConfigStepProps) {
  // The unedited proposal is what an edit is compared with; the latest one gives the share texts.
  const [original, setOriginal] = useState<Proposal | null>(null);
  const [latest, setLatest] = useState<Proposal | null>(null);
  const [proposalError, setProposalError] = useState<string | null>(null);
  const [edits, setEdits] = useState<Readonly<Record<string, string>>>({});
  const [labels, setLabels] = useState<Labels>(EMPTY_LABELS);
  const [isSaving, setIsSaving] = useState(false);
  const [saveError, setSaveError] = useState<string | null>(null);
  const [savedText, setSavedText] = useState<string | null>(null);
  const [isCollapsed, setIsCollapsed] = useState(isConfigured);
  const [savedBodyKey, setSavedBodyKey] = useState(EMPTY_BODY_KEY);
  const requestCount = useRef(0);
  const timer = useRef<ReturnType<typeof setTimeout> | undefined>(undefined);
  const focusAfterToggle = useRef(false);
  const changeRef = useRef<HTMLButtonElement>(null);
  const formRef = useRef<HTMLDivElement>(null);
  const missingId = useId();

  // Responses can arrive out of order; only the newest request's is shown.
  const requestProposal = useCallback((body: ConfigBody) => {
    requestCount.current += 1;
    const request = requestCount.current;
    void readProposal(body).then((result) => {
      if (request !== requestCount.current) {
        return;
      }
      if ("error" in result) {
        setProposalError(result.error);
        return;
      }
      setProposalError(null);
      setLatest(result);
      setOriginal((previous) =>
        previous === null || JSON.stringify(body) === EMPTY_BODY_KEY ? result : mergeProposal(previous, result, body),
      );
    });
  }, []);

  useEffect(() => {
    requestProposal({ fields: {}, labels: EMPTY_LABELS });
    return () => {
      clearTimeout(timer.current);
      // A response that lands after unmounting is ignored.
      requestCount.current += 1;
    };
  }, [requestProposal]);

  const body = toBody(original, edits, labels);
  const bodyKey = JSON.stringify(body);
  // Unsaved edits on an open form, or a save on its way, would be lost to a reload.
  const isHoldingReload = !isCollapsed && (isSaving || bodyKey !== savedBodyKey);
  useEffect(() => {
    setReloadHeld(isHoldingReload);
    return () => setReloadHeld(false);
  }, [isHoldingReload]);

  // An upload can add labels or runs; the reader's edits are sent along and kept.
  const refreshProposal = useEffectEvent(() => {
    clearTimeout(timer.current);
    requestProposal(body);
  });
  const shownTotalsKey = useRef(totalsKey);
  useEffect(() => {
    if (totalsKey !== shownTotalsKey.current) {
      shownTotalsKey.current = totalsKey;
      refreshProposal();
    }
  }, [totalsKey]);

  // After Confirm or Change configuration, the button that was pressed is gone; focus moves to
  // what replaced it.
  useEffect(() => {
    if (!focusAfterToggle.current) {
      return;
    }
    focusAfterToggle.current = false;
    if (isCollapsed) {
      changeRef.current?.focus();
    } else {
      formRef.current?.querySelector<HTMLInputElement>("input")?.focus();
    }
  }, [isCollapsed]);

  function scheduleProposal(body: ConfigBody) {
    clearTimeout(timer.current);
    timer.current = setTimeout(() => requestProposal(body), PROPOSAL_DELAY_MS);
  }

  function handleFieldChange(name: string, value: string) {
    const nextEdits = { ...edits, [name]: value };
    setEdits(nextEdits);
    scheduleProposal(toBody(original, nextEdits, labels));
  }

  function handleLabelChange(mapName: LabelMapName, label: string, verdict: string) {
    const nextMap = { ...labels[mapName], [label]: verdict };
    if (verdict === NOT_MAPPED) {
      delete nextMap[label];
    }
    const nextLabels = { ...labels, [mapName]: nextMap };
    setLabels(nextLabels);
    scheduleProposal(toBody(original, edits, nextLabels));
  }

  async function handleConfirm() {
    clearTimeout(timer.current);
    setIsSaving(true);
    setSaveError(null);
    const result = await saveConfig(body);
    setIsSaving(false);
    if ("error" in result) {
      setSaveError(result.error);
      return;
    }
    setSavedText(result.savedText);
    setSavedBodyKey(bodyKey);
    focusAfterToggle.current = true;
    setIsCollapsed(true);
  }

  function handleReopen() {
    setSavedText(null);
    focusAfterToggle.current = true;
    setIsCollapsed(false);
  }

  function readValue(field: ProposalField): string {
    return edits[field.name] ?? findValue(original, field.name) ?? "";
  }

  return (
    <div className="config-step">
      {/* Kept in the tree from the first render, so the saved sentence is announced. */}
      <p className="config-saved" role="status" aria-live="polite">
        {savedText}
      </p>
      {proposalError !== null && (
        <p className="config-error" role="alert">
          {proposalError}
        </p>
      )}
      {original !== null && latest !== null && isCollapsed && (
        <div className="panel config-summary">
          <dl className="config-summary-list" aria-label="Configuration">
            {latest.fields.map((field) => (
              <div key={field.name}>
                <dt>{field.label}</dt>
                <dd>{readValue(field) === "" ? "Not set" : <code>{readValue(field)}</code>}</dd>
              </div>
            ))}
          </dl>
          <button ref={changeRef} type="button" className="data-button" onClick={handleReopen}>
            Change configuration
          </button>
        </div>
      )}
      {original !== null && latest !== null && !isCollapsed && (
        <div ref={formRef} className="config-form">
          <p className="config-run-count">{latest.agentRunCountText}</p>
          <datalist id={SUGGESTIONS_ID}>
            {latest.suggestions.map((suggestion) => (
              <option key={suggestion} value={suggestion} />
            ))}
          </datalist>
          <fieldset className="panel config-group">
            <legend className="config-legend">Trace attributes</legend>
            {latest.fields.map((field) => (
              <ConfigField
                key={field.name}
                field={field}
                value={readValue(field)}
                onChange={(value) => handleFieldChange(field.name, value)}
              />
            ))}
          </fieldset>
          <LabelGroup
            legend="Analyst labels"
            labels={original.unmappedAnalystLabels}
            choices={latest.verdictChoices}
            chosen={labels.label_map}
            onChange={(label, verdict) => handleLabelChange("label_map", label, verdict)}
          />
          <LabelGroup
            legend="Agent labels"
            labels={original.unmappedAgentLabels}
            choices={latest.verdictChoices}
            chosen={labels.agent_label_map}
            onChange={(label, verdict) => handleLabelChange("agent_label_map", label, verdict)}
          />
          {latest.notes.length > 0 && (
            <ul className="config-notes" aria-label="Configuration notes">
              {latest.notes.map((note, index) => (
                // The notes come whole with each proposal and are never reordered in place.
                <li key={index}>{note}</li>
              ))}
            </ul>
          )}
          <div className="config-actions">
            <button
              type="button"
              className="data-button config-confirm"
              disabled={isSaving || latest.missingRequired.length > 0}
              aria-describedby={latest.missingText === null ? undefined : missingId}
              onClick={() => void handleConfirm()}
            >
              Confirm
            </button>
            {latest.missingText !== null && (
              <p id={missingId} className="config-missing">
                {latest.missingText}
              </p>
            )}
          </div>
          {saveError !== null && (
            <p className="config-error" role="alert">
              {saveError}
            </p>
          )}
        </div>
      )}
    </div>
  );
}

type ConfigFieldProps = { field: ProposalField; value: string; onChange: (value: string) => void };

function ConfigField({ field, value, onChange }: ConfigFieldProps) {
  const inputId = useId();
  const shareId = useId();
  return (
    <div className="config-field" data-missing={field.isMissing}>
      <label htmlFor={inputId} className="config-label">
        {field.label}
      </label>
      <input
        id={inputId}
        className="config-input"
        type="text"
        list={SUGGESTIONS_ID}
        value={value}
        spellCheck={false}
        autoComplete="off"
        aria-describedby={shareId}
        onChange={(event) => onChange(event.currentTarget.value)}
      />
      <p id={shareId} className="config-share">
        {field.shareText}
      </p>
    </div>
  );
}

type LabelGroupProps = {
  legend: string;
  labels: readonly string[];
  choices: readonly string[];
  chosen: Readonly<Record<string, string>>;
  onChange: (label: string, verdict: string) => void;
};

function LabelGroup({ legend, labels, choices, chosen, onChange }: LabelGroupProps) {
  const baseId = useId();
  if (labels.length === 0) {
    return null;
  }
  return (
    <fieldset className="panel config-group">
      <legend className="config-legend">{legend}</legend>
      {labels.map((label, index) => (
        <div key={label} className="config-field">
          <label htmlFor={`${baseId}-${index}`} className="config-label">
            <code>{label}</code>
          </label>
          <select
            id={`${baseId}-${index}`}
            className="config-input"
            value={chosen[label] ?? NOT_MAPPED}
            onChange={(event) => onChange(label, event.currentTarget.value)}
          >
            <option value={NOT_MAPPED}>Not mapped</option>
            {choices.map((choice) => (
              <option key={choice} value={choice}>
                {choice}
              </option>
            ))}
          </select>
        </div>
      ))}
    </fieldset>
  );
}

// Only the fields the reader changed from the first proposal are sent; the server keeps the rest.
function toBody(original: Proposal | null, edits: Readonly<Record<string, string>>, labels: Labels): ConfigBody {
  const fields = Object.fromEntries(
    Object.entries(edits).filter(([name, value]) => value !== (findValue(original, name) ?? "")),
  );
  return { fields, labels };
}

// The proposal for edits keeps the unedited value of each edited field, so an edit stays an edit,
// and keeps every label the reader could map, including the ones mapped since.
function mergeProposal(previous: Proposal, result: Proposal, body: ConfigBody): Proposal {
  return {
    ...result,
    fields: result.fields.map((field) =>
      field.name in body.fields ? { ...field, value: findValue(previous, field.name) } : field,
    ),
    unmappedAnalystLabels: toUnion(previous.unmappedAnalystLabels, result.unmappedAnalystLabels),
    unmappedAgentLabels: toUnion(previous.unmappedAgentLabels, result.unmappedAgentLabels),
  };
}

function toUnion(first: readonly string[], second: readonly string[]): string[] {
  return [...new Set([...first, ...second])];
}

function findValue(proposal: Proposal | null, name: string): string | null {
  return proposal?.fields.find((field) => field.name === name)?.value ?? null;
}
