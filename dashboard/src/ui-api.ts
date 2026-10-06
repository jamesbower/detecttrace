// The `detecttrace ui` app's routes: the only module that talks to them. Each function returns
// the server's display text, or `{ error }` with a sentence to show; none ever throws. Every
// response body is checked before use, since it is whatever the server (or a proxy) sent.
import type { ExampleView, NoteView } from "./view";

export type UploadKind = "traces" | "verdicts" | "checklists";
export type ApiError = { readonly error: string };

export type UploadResult = { readonly storedText: string; readonly problems: readonly NoteView[] } | ApiError;

export type UiState = {
  readonly isConfigured: boolean;
  readonly canConfigure: boolean;
  readonly hasResults: boolean;
  readonly spanCountText: string;
  readonly verdictCountText: string;
  readonly traceFamilyText: string | null;
  readonly checklistClasses: readonly string[];
};

/** The configuration form's edits: a field name to an attribute key, and label choices. */
export type ConfigBody = {
  readonly fields: Record<string, string>;
  readonly labels: {
    readonly label_map: Record<string, string>;
    readonly agent_label_map: Record<string, string>;
  };
};

export type ProposalField = {
  readonly name: string;
  readonly label: string;
  readonly value: string | null;
  readonly shareText: string;
  readonly isMissing: boolean;
};

/** `POST /api/config/proposal`'s content, from `build_proposal_content` in serve/ui_config.py. */
export type Proposal = {
  readonly fields: readonly ProposalField[];
  readonly suggestions: readonly string[];
  readonly labelMap: Readonly<Record<string, string>>;
  readonly agentLabelMap: Readonly<Record<string, string>>;
  readonly unmappedAnalystLabels: readonly string[];
  readonly unmappedAgentLabels: readonly string[];
  readonly verdictChoices: readonly string[];
  readonly notes: readonly string[];
  readonly missingRequired: readonly string[];
  readonly missingText: string | null;
  readonly agentRunCountText: string;
};

export const NETWORK_ERROR_TEXT = "The app could not be reached. Check that detecttrace ui is still running.";
export const BAD_RESPONSE_TEXT = "The app sent a reply this page cannot read.";

// Every POST carries it; the server refuses a POST without it, which a cross-site form cannot add.
const GUARD_HEADERS = { "X-DetectTrace": "1" };

export async function uploadFile(kind: UploadKind, file: File): Promise<UploadResult> {
  const body = await request(`/api/upload/${kind}?name=${encodeURIComponent(file.name)}`, {
    method: "POST",
    headers: { ...GUARD_HEADERS, "Content-Type": "application/octet-stream" },
    body: file,
  });
  if ("error" in body) {
    return body;
  }
  const { stored_text: storedText, problems } = body.value;
  if (typeof storedText !== "string" || !Array.isArray(problems) || !problems.every(isNoteView)) {
    return { error: BAD_RESPONSE_TEXT };
  }
  return { storedText, problems };
}

export async function readUiState(): Promise<UiState | ApiError> {
  const body = await request("/api/ui/state", { method: "GET" });
  if ("error" in body) {
    return body;
  }
  const state = body.value;
  if (
    typeof state.is_configured !== "boolean" ||
    typeof state.can_configure !== "boolean" ||
    typeof state.has_results !== "boolean" ||
    typeof state.span_count_text !== "string" ||
    typeof state.verdict_count_text !== "string" ||
    !isStringOrNull(state.trace_family_text) ||
    !isStringArray(state.checklist_classes)
  ) {
    return { error: BAD_RESPONSE_TEXT };
  }
  return {
    isConfigured: state.is_configured,
    canConfigure: state.can_configure,
    hasResults: state.has_results,
    spanCountText: state.span_count_text,
    verdictCountText: state.verdict_count_text,
    traceFamilyText: state.trace_family_text,
    checklistClasses: state.checklist_classes,
  };
}

export async function readProposal(config: ConfigBody): Promise<Proposal | ApiError> {
  const body = await postJson("/api/config/proposal", config);
  if ("error" in body) {
    return body;
  }
  return toProposal(body.value) ?? { error: BAD_RESPONSE_TEXT };
}

export async function saveConfig(config: ConfigBody): Promise<{ readonly savedText: string } | ApiError> {
  const body = await postJson("/api/config", config);
  if ("error" in body) {
    return body;
  }
  const savedText = body.value.saved_text;
  return typeof savedText === "string" ? { savedText } : { error: BAD_RESPONSE_TEXT };
}

export async function clearData(): Promise<{ readonly ok: true } | ApiError> {
  const body = await postJson("/api/data/clear", { confirm: true });
  return "error" in body ? body : { ok: true };
}

type JsonObject = Readonly<Record<string, unknown>>;

function postJson(url: string, value: unknown): Promise<{ value: JsonObject } | ApiError> {
  return request(url, {
    method: "POST",
    headers: { ...GUARD_HEADERS, "Content-Type": "application/json" },
    body: JSON.stringify(value),
  });
}

// Wrapped in `value`, so a reply whose JSON has an `error` key is never taken for an error.
async function request(url: string, init: RequestInit): Promise<{ value: JsonObject } | ApiError> {
  let response: Response;
  try {
    response = await fetch(url, { ...init, credentials: "same-origin", cache: "no-store" });
  } catch {
    return { error: NETWORK_ERROR_TEXT };
  }
  let parsed: unknown;
  try {
    parsed = await response.json();
  } catch {
    return { error: BAD_RESPONSE_TEXT };
  }
  if (!isJsonObject(parsed)) {
    return { error: BAD_RESPONSE_TEXT };
  }
  if (!response.ok) {
    // The server's errors are `{"code", "message"}`, written for the reader.
    return { error: typeof parsed.message === "string" && parsed.message !== "" ? parsed.message : BAD_RESPONSE_TEXT };
  }
  return { value: parsed };
}

function toProposal(value: JsonObject): Proposal | null {
  const { fields, label_map: labelMap, agent_label_map: agentLabelMap } = value;
  if (
    !Array.isArray(fields) ||
    !fields.every(isProposalField) ||
    !isStringArray(value.suggestions) ||
    !isStringRecord(labelMap) ||
    !isStringRecord(agentLabelMap) ||
    !isStringArray(value.unmapped_analyst_labels) ||
    !isStringArray(value.unmapped_agent_labels) ||
    !isStringArray(value.verdict_choices) ||
    !isStringArray(value.notes) ||
    !isStringArray(value.missing_required) ||
    !isStringOrNull(value.missing_text) ||
    typeof value.agent_run_count_text !== "string"
  ) {
    return null;
  }
  return {
    fields: fields.map((field) => ({
      name: field.name,
      label: field.label,
      value: field.value,
      shareText: field.share_text,
      isMissing: field.is_missing,
    })),
    suggestions: value.suggestions,
    labelMap,
    agentLabelMap,
    unmappedAnalystLabels: value.unmapped_analyst_labels,
    unmappedAgentLabels: value.unmapped_agent_labels,
    verdictChoices: value.verdict_choices,
    notes: value.notes,
    missingRequired: value.missing_required,
    missingText: value.missing_text,
    agentRunCountText: value.agent_run_count_text,
  };
}

type RawProposalField = {
  readonly name: string;
  readonly label: string;
  readonly value: string | null;
  readonly share_text: string;
  readonly is_missing: boolean;
};

function isProposalField(value: unknown): value is RawProposalField {
  return (
    isJsonObject(value) &&
    typeof value.name === "string" &&
    typeof value.label === "string" &&
    isStringOrNull(value.value) &&
    typeof value.share_text === "string" &&
    typeof value.is_missing === "boolean"
  );
}

function isNoteView(value: unknown): value is NoteView {
  return (
    isJsonObject(value) &&
    typeof value.severity === "string" &&
    typeof value.severity_label === "string" &&
    typeof value.count_text === "string" &&
    typeof value.message === "string" &&
    typeof value.hint === "string" &&
    Array.isArray(value.examples) &&
    value.examples.every(isExampleView) &&
    isStringOrNull(value.more_text)
  );
}

function isExampleView(value: unknown): value is ExampleView {
  return isJsonObject(value) && typeof value.subject === "string" && isStringOrNull(value.detail);
}

function isJsonObject(value: unknown): value is JsonObject {
  return typeof value === "object" && value !== null && !Array.isArray(value);
}

function isStringOrNull(value: unknown): value is string | null {
  return value === null || typeof value === "string";
}

function isStringArray(value: unknown): value is string[] {
  return Array.isArray(value) && value.every((item) => typeof item === "string");
}

function isStringRecord(value: unknown): value is Record<string, string> {
  return isJsonObject(value) && Object.values(value).every((item) => typeof item === "string");
}
