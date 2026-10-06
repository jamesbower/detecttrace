import { afterEach, describe, expect, it, vi } from "vitest";

import {
  BAD_RESPONSE_TEXT,
  NETWORK_ERROR_TEXT,
  clearData,
  readProposal,
  readUiState,
  saveConfig,
  uploadFile,
} from "./ui-api";

import type { ConfigBody } from "./ui-api";

const CONFIG: ConfigBody = { fields: { case_id: "alert.id" }, labels: { label_map: {}, agent_label_map: {} } };

const NOTE = {
  severity: "warning",
  severity_label: "Warning",
  count_text: "3",
  message: "traces have no case ID.",
  hint: "",
  examples: [{ subject: "trace-1", detail: null }],
  more_text: null,
};

const STATE = {
  is_configured: false,
  can_configure: true,
  has_results: false,
  span_count_text: "2,129 spans stored.",
  verdict_count_text: "201 verdicts stored.",
  trace_family_text: "OTLP traces",
  checklist_classes: ["phishing"],
  checklist_classes_text: "phishing",
  checklist_error_text: null,
};

const PROPOSAL = {
  fields: [
    {
      name: "case_id",
      label: "Case ID",
      value: "alert.id",
      share_text: "201 of 201 runs (100%)",
      summary_text: "Case ID: alert.id",
      is_missing: false,
    },
  ],
  suggestions: ["alert.id"],
  label_map: { tp: "true_positive", maybe: "benign" },
  agent_label_map: {},
  unmapped_analyst_labels: [],
  unmapped_agent_labels: ["maybe"],
  user_labels: { label_map: { maybe: "benign" }, agent_label_map: {} },
  verdict_choices: ["true_positive", "false_positive", "benign"],
  verdict_choice_labels: [
    { value: "true_positive", label: "True positive" },
    { value: "false_positive", label: "False positive" },
    { value: "benign", label: "Benign" },
  ],
  labels_help_text: "Choose the verdict each one means.",
  not_set_text: "Not set",
  not_mapped_text: "Not mapped",
  notes: [],
  missing_required: [],
  missing_text: null,
  agent_run_count_text: "201 agent runs found.",
};

function mockFetch(status: number, body: unknown) {
  const fetchMock = vi.fn<(url: string, init?: RequestInit) => Promise<Response>>(() =>
    Promise.resolve(new Response(JSON.stringify(body), { status })),
  );
  vi.stubGlobal("fetch", fetchMock);
  return fetchMock;
}

function lastInit(fetchMock: ReturnType<typeof mockFetch>): RequestInit {
  return fetchMock.mock.calls.at(-1)![1]!;
}

afterEach(() => {
  vi.unstubAllGlobals();
});

describe("uploadFile", () => {
  it("posts to the kind's route with the encoded file name", async () => {
    const fetchMock = mockFetch(200, { stored_text: "Done.", problems: [] });

    await uploadFile("verdicts", new File(["a"], "my verdicts&?.csv"));

    expect(fetchMock.mock.calls[0]![0]).toBe("/api/upload/verdicts?name=my%20verdicts%26%3F.csv");
  });

  it("sends the guard header", async () => {
    const fetchMock = mockFetch(200, { stored_text: "Done.", problems: [] });

    await uploadFile("traces", new File(["a"], "t.jsonl"));

    expect(new Headers(lastInit(fetchMock).headers).get("X-DetectTrace")).toBe("1");
  });

  it("sends the raw bytes as octet-stream", async () => {
    const fetchMock = mockFetch(200, { stored_text: "Done.", problems: [] });

    await uploadFile("traces", new File(["a"], "t.jsonl"));

    expect(new Headers(lastInit(fetchMock).headers).get("Content-Type")).toBe("application/octet-stream");
  });

  it("sends the file itself as the body", async () => {
    const fetchMock = mockFetch(200, { stored_text: "Done.", problems: [] });
    const file = new File(["a"], "t.jsonl");

    await uploadFile("traces", file);

    expect(lastInit(fetchMock).body).toBe(file);
  });

  it("sends the page's own credentials and skips the cache", async () => {
    const fetchMock = mockFetch(200, { stored_text: "Done.", problems: [] });

    await uploadFile("traces", new File(["a"], "t.jsonl"));

    expect([lastInit(fetchMock).credentials, lastInit(fetchMock).cache]).toEqual(["same-origin", "no-store"]);
  });

  it("returns the stored text and problems", async () => {
    mockFetch(200, { stored_text: "201 verdicts added.", problems: [NOTE] });

    expect(await uploadFile("verdicts", new File(["a"], "v.csv"))).toEqual({
      storedText: "201 verdicts added.",
      problems: [NOTE],
    });
  });

  it("returns the server's message for a refused file", async () => {
    mockFetch(422, { code: 422, message: "The file has no header row." });

    expect(await uploadFile("verdicts", new File(["a"], "v.csv"))).toEqual({ error: "The file has no header row." });
  });

  it("returns a plain sentence when the network fails", async () => {
    vi.stubGlobal("fetch", vi.fn(() => Promise.reject(new TypeError("Failed to fetch"))));

    expect(await uploadFile("verdicts", new File(["a"], "v.csv"))).toEqual({ error: NETWORK_ERROR_TEXT });
  });

  it("returns an error for a reply that is not JSON", async () => {
    vi.stubGlobal("fetch", vi.fn(() => Promise.resolve(new Response("<html>", { status: 200 }))));

    expect(await uploadFile("verdicts", new File(["a"], "v.csv"))).toEqual({ error: BAD_RESPONSE_TEXT });
  });

  it("returns an error for a problem of the wrong shape", async () => {
    mockFetch(200, { stored_text: "Done.", problems: [{ ...NOTE, examples: "none" }] });

    expect(await uploadFile("verdicts", new File(["a"], "v.csv"))).toEqual({ error: BAD_RESPONSE_TEXT });
  });

  it("returns an error for a server error without a message", async () => {
    mockFetch(500, { detail: "boom" });

    expect(await uploadFile("verdicts", new File(["a"], "v.csv"))).toEqual({ error: BAD_RESPONSE_TEXT });
  });
});

describe("readUiState", () => {
  it("returns the state in the page's names", async () => {
    mockFetch(200, STATE);

    expect(await readUiState()).toEqual({
      isConfigured: false,
      canConfigure: true,
      hasResults: false,
      spanCountText: "2,129 spans stored.",
      verdictCountText: "201 verdicts stored.",
      traceFamilyText: "OTLP traces",
      checklistClasses: ["phishing"],
      checklistClassesText: "phishing",
      checklistErrorText: null,
    });
  });

  it("returns an error for a state without the checklist classes text", async () => {
    mockFetch(200, { ...STATE, checklist_classes_text: undefined });

    expect(await readUiState()).toEqual({ error: BAD_RESPONSE_TEXT });
  });

  it("returns why the checklists could not be loaded", async () => {
    mockFetch(200, { ...STATE, checklist_classes: [], checklist_error_text: "phishing.yaml is not valid YAML." });

    expect(await readUiState()).toMatchObject({ checklistErrorText: "phishing.yaml is not valid YAML." });
  });

  it("reads a state without the checklist error as having none", async () => {
    mockFetch(200, { ...STATE, checklist_error_text: undefined });

    expect(await readUiState()).toMatchObject({ checklistErrorText: null });
  });

  it("returns an error for a checklist error that is not text", async () => {
    mockFetch(200, { ...STATE, checklist_error_text: 3 });

    expect(await readUiState()).toEqual({ error: BAD_RESPONSE_TEXT });
  });

  it("returns an error for a state with a missing count", async () => {
    mockFetch(200, { ...STATE, span_count_text: undefined });

    expect(await readUiState()).toEqual({ error: BAD_RESPONSE_TEXT });
  });

  it("sends no guard header on a read", async () => {
    const fetchMock = mockFetch(200, STATE);

    await readUiState();

    expect(new Headers(lastInit(fetchMock).headers).has("X-DetectTrace")).toBe(false);
  });
});

describe("readProposal", () => {
  it("posts the edits as JSON", async () => {
    const fetchMock = mockFetch(200, PROPOSAL);

    await readProposal(CONFIG);

    expect(lastInit(fetchMock).body).toBe(JSON.stringify(CONFIG));
  });

  it("sends the guard header", async () => {
    const fetchMock = mockFetch(200, PROPOSAL);

    await readProposal(CONFIG);

    expect(new Headers(lastInit(fetchMock).headers).get("X-DetectTrace")).toBe("1");
  });

  it("returns the proposal's fields in the page's names", async () => {
    mockFetch(200, PROPOSAL);

    const proposal = await readProposal(CONFIG);

    expect("fields" in proposal && proposal.fields).toEqual([
      {
        name: "case_id",
        label: "Case ID",
        value: "alert.id",
        shareText: "201 of 201 runs (100%)",
        summaryText: "Case ID: alert.id",
        isMissing: false,
      },
    ]);
  });

  it("returns the labels mapped by hand", async () => {
    mockFetch(200, PROPOSAL);

    const proposal = await readProposal(CONFIG);

    expect("userLabels" in proposal && proposal.userLabels).toEqual({ label_map: { maybe: "benign" }, agent_label_map: {} });
  });

  it("returns each verdict choice with its label", async () => {
    mockFetch(200, PROPOSAL);

    const proposal = await readProposal(CONFIG);

    expect("verdictChoiceLabels" in proposal && proposal.verdictChoiceLabels[2]).toEqual({ value: "benign", label: "Benign" });
  });

  it("returns the label step's texts", async () => {
    mockFetch(200, PROPOSAL);

    const proposal = await readProposal(CONFIG);

    expect(
      "labelsHelpText" in proposal && [proposal.labelsHelpText, proposal.notSetText, proposal.notMappedText],
    ).toEqual(["Choose the verdict each one means.", "Not set", "Not mapped"]);
  });

  it.each([
    "user_labels",
    "verdict_choice_labels",
    "labels_help_text",
    "not_set_text",
    "not_mapped_text",
  ])("returns an error for a proposal without %s", async (key) => {
    mockFetch(200, { ...PROPOSAL, [key]: undefined });

    expect(await readProposal(CONFIG)).toEqual({ error: BAD_RESPONSE_TEXT });
  });

  it("returns an error for a field without its summary text", async () => {
    mockFetch(200, { ...PROPOSAL, fields: [{ ...PROPOSAL.fields[0], summary_text: undefined }] });

    expect(await readProposal(CONFIG)).toEqual({ error: BAD_RESPONSE_TEXT });
  });

  it("returns an error for hand-mapped labels of the wrong shape", async () => {
    mockFetch(200, { ...PROPOSAL, user_labels: { label_map: { maybe: 1 }, agent_label_map: {} } });

    expect(await readProposal(CONFIG)).toEqual({ error: BAD_RESPONSE_TEXT });
  });

  it("returns an error for a proposal with a malformed label map", async () => {
    mockFetch(200, { ...PROPOSAL, label_map: { tp: 1 } });

    expect(await readProposal(CONFIG)).toEqual({ error: BAD_RESPONSE_TEXT });
  });

  it("returns the server's message before both inputs are stored", async () => {
    mockFetch(409, { code: 409, message: "Upload traces and verdicts first." });

    expect(await readProposal(CONFIG)).toEqual({ error: "Upload traces and verdicts first." });
  });
});

describe("saveConfig", () => {
  it("returns the saved text", async () => {
    mockFetch(200, { saved_text: "Configuration saved." });

    expect(await saveConfig(CONFIG)).toEqual({ savedText: "Configuration saved." });
  });

  it("sends the guard header", async () => {
    const fetchMock = mockFetch(200, { saved_text: "Configuration saved." });

    await saveConfig(CONFIG);

    expect(new Headers(lastInit(fetchMock).headers).get("X-DetectTrace")).toBe("1");
  });
});

describe("clearData", () => {
  it("posts the confirmation", async () => {
    const fetchMock = mockFetch(200, {});

    await clearData();

    expect(lastInit(fetchMock).body).toBe('{"confirm":true}');
  });

  it("sends the guard header", async () => {
    const fetchMock = mockFetch(200, {});

    await clearData();

    expect(new Headers(lastInit(fetchMock).headers).get("X-DetectTrace")).toBe("1");
  });

  it("returns ok once cleared", async () => {
    mockFetch(200, {});

    expect(await clearData()).toEqual({ ok: true });
  });
});
