import { act, cleanup, fireEvent, render, screen } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { isReloadHeld } from "../reload-hold";
import { readProposal, saveConfig } from "../ui-api";
import { ConfigStep, PROPOSAL_DELAY_MS } from "./ConfigStep";

import type { ApiError, Proposal, ProposalField } from "../ui-api";

vi.mock("../ui-api", () => ({ readProposal: vi.fn(), saveConfig: vi.fn() }));

const readProposalMock = vi.mocked(readProposal);
const saveConfigMock = vi.mocked(saveConfig);

const CASE_ID_FIELD: ProposalField = {
  name: "case_id",
  label: "Case ID",
  value: "alert.id",
  shareText: "201 of 201 runs (100%)",
  summaryText: "Case ID: alert.id",
  isMissing: false,
};
const AGENT_VERDICT_FIELD: ProposalField = {
  name: "agent_verdict",
  label: "Agent verdict",
  value: null,
  shareText: "Not found in any run",
  summaryText: "Agent verdict: Not set",
  isMissing: true,
};

const PROPOSAL: Proposal = {
  fields: [CASE_ID_FIELD, AGENT_VERDICT_FIELD],
  suggestions: ["alert.id", "agent.verdict"],
  labelMap: {},
  agentLabelMap: {},
  unmappedAnalystLabels: ["maybe"],
  unmappedAgentLabels: [],
  userLabels: { label_map: {}, agent_label_map: {} },
  verdictChoices: ["true_positive", "false_positive", "benign"],
  verdictChoiceLabels: [
    { value: "true_positive", label: "True positive" },
    { value: "false_positive", label: "False positive" },
    { value: "benign", label: "Benign" },
  ],
  labelsHelpText: "Choose the verdict each label means.",
  notSetText: "Nothing chosen",
  notMappedText: "Leave unmapped",
  notes: ["3 runs have no case ID."],
  missingRequired: [],
  missingText: null,
  agentRunCountText: "201 agent runs found.",
};

const MISSING: Proposal = {
  ...PROPOSAL,
  missingRequired: ["agent_verdict"],
  missingText: "Choose a key for Agent verdict before confirming.",
};

// A configuration saved earlier mapped "Malicious" by hand; "maybe" is still unmapped.
const SAVED: Proposal = {
  ...PROPOSAL,
  labelMap: { Malicious: "true_positive" },
  userLabels: { label_map: { Malicious: "true_positive" }, agent_label_map: {} },
};

const EMPTY_LABELS = { label_map: {}, agent_label_map: {} };
const SAVED_LABELS = { label_map: { Malicious: "true_positive" }, agent_label_map: {} };
const TOTALS = "2,129 spans stored.";
const NEW_TOTALS = "4,000 spans stored.";

// Each settles when the test says so, to put responses in any order.
function deferProposals() {
  const settlers: ((result: Proposal | ApiError) => void)[] = [];
  readProposalMock.mockImplementation(
    () =>
      new Promise((resolve) => {
        settlers.push(resolve);
      }),
  );
  return settlers;
}

async function flush() {
  await act(async () => {
    await Promise.resolve();
  });
}

async function renderStep({ proposal = PROPOSAL, isConfigured = false } = {}) {
  readProposalMock.mockResolvedValue(proposal);
  const view = render(<ConfigStep isConfigured={isConfigured} totalsKey={TOTALS} />);
  await flush();
  return view;
}

function edit(label: string, value: string) {
  fireEvent.change(screen.getByLabelText(label), { target: { value } });
}

async function waitForDelay() {
  await act(async () => {
    await vi.advanceTimersByTimeAsync(PROPOSAL_DELAY_MS);
  });
}

beforeEach(() => {
  vi.useFakeTimers();
  saveConfigMock.mockResolvedValue({ savedText: "Configuration saved. Scoring starts now." });
});

afterEach(() => {
  cleanup();
  vi.useRealTimers();
  vi.resetAllMocks();
});

describe("the proposal", () => {
  it("asks for a proposal with no edits", async () => {
    await renderStep();

    expect(readProposalMock).toHaveBeenCalledWith({ fields: {}, labels: EMPTY_LABELS });
  });

  it("says how many agent runs it found", async () => {
    await renderStep();

    expect(screen.queryByText("201 agent runs found.")).not.toBeNull();
  });

  it("labels an input for each field, holding the proposed key", async () => {
    await renderStep();

    expect(screen.getAllByRole("combobox").map((input) => (input as HTMLInputElement).value)).toEqual([
      "alert.id",
      "",
      "",
    ]);
  });

  it("names each field's input after the field", async () => {
    await renderStep();

    expect((screen.getByLabelText("Case ID") as HTMLInputElement).value).toBe("alert.id");
  });

  it("describes each field's input with its share text", async () => {
    await renderStep();
    const describedBy = screen.getByLabelText("Case ID").getAttribute("aria-describedby") ?? "";

    expect(document.getElementById(describedBy)?.textContent).toBe("201 of 201 runs (100%)");
  });

  it("offers the suggestions to every field", async () => {
    await renderStep();

    expect(
      Array.from(document.querySelectorAll("#dt-config-suggestions option"), (option) => option.getAttribute("value")),
    ).toEqual(["alert.id", "agent.verdict"]);
  });

  it("lists the notes", async () => {
    await renderStep();

    expect(screen.getByRole("list", { name: "Configuration notes" }).textContent).toBe("3 runs have no case ID.");
  });

  it("shows why the proposal could not be read", async () => {
    readProposalMock.mockResolvedValue({ error: "Upload traces and verdicts first." });
    render(<ConfigStep isConfigured={false} totalsKey={TOTALS} />);
    await flush();

    expect(screen.getByRole("alert").textContent).toBe("Upload traces and verdicts first.");
  });
});

describe("an edit", () => {
  it("asks for a new proposal with the changed field once typing pauses", async () => {
    await renderStep();
    edit("Case ID", "x");

    await waitForDelay();

    expect(readProposalMock).toHaveBeenLastCalledWith({ fields: { case_id: "x" }, labels: EMPTY_LABELS });
  });

  it("waits for typing to pause before asking", async () => {
    await renderStep();
    edit("Case ID", "x");

    await act(async () => {
      await vi.advanceTimersByTimeAsync(PROPOSAL_DELAY_MS - 1);
    });

    expect(readProposalMock).toHaveBeenCalledOnce();
  });

  it("leaves out a field changed back to the proposed key", async () => {
    await renderStep();
    edit("Case ID", "x");
    edit("Case ID", "alert.id");

    await waitForDelay();

    expect(readProposalMock).toHaveBeenLastCalledWith({ fields: {}, labels: EMPTY_LABELS });
  });

  it("shows the new share text", async () => {
    await renderStep();
    readProposalMock.mockResolvedValue({
      ...PROPOSAL,
      fields: [{ ...CASE_ID_FIELD, value: "x", shareText: "0 of 201 runs (0%)" }, AGENT_VERDICT_FIELD],
    });
    edit("Case ID", "x");

    await waitForDelay();

    expect(screen.queryByText("0 of 201 runs (0%)")).not.toBeNull();
  });

  it("keeps the typed value while the proposal refreshes", async () => {
    await renderStep();
    edit("Agent verdict", "agent.v");

    await waitForDelay();

    expect((screen.getByLabelText("Agent verdict") as HTMLInputElement).value).toBe("agent.v");
  });

  it("shows the newer proposal when an older one answers last", async () => {
    const settlers = deferProposals();
    render(<ConfigStep isConfigured={false} totalsKey={TOTALS} />);
    settlers[0]!(PROPOSAL);
    await flush();
    edit("Case ID", "a");
    await waitForDelay();
    edit("Case ID", "b");
    await waitForDelay();

    settlers[2]!({ ...PROPOSAL, agentRunCountText: "Newer." });
    settlers[1]!({ ...PROPOSAL, agentRunCountText: "Older." });
    await flush();

    expect(screen.queryByText("Newer.")).not.toBeNull();
  });

  it("ignores an older proposal that answers after the newer one", async () => {
    const settlers = deferProposals();
    render(<ConfigStep isConfigured={false} totalsKey={TOTALS} />);
    settlers[0]!(PROPOSAL);
    await flush();
    edit("Case ID", "a");
    await waitForDelay();
    edit("Case ID", "b");
    await waitForDelay();

    settlers[2]!({ ...PROPOSAL, agentRunCountText: "Newer." });
    settlers[1]!({ ...PROPOSAL, agentRunCountText: "Older." });
    await flush();

    expect(screen.queryByText("Older.")).toBeNull();
  });
});

describe("unmapped labels", () => {
  it("offers each verdict by its label, after not mapping it", async () => {
    await renderStep();

    expect(
      Array.from((screen.getByLabelText("maybe") as HTMLSelectElement).options, (option) => option.textContent),
    ).toEqual(["Leave unmapped", "True positive", "False positive", "Benign"]);
  });

  it("gives each verdict option the verdict's value", async () => {
    await renderStep();

    expect(
      Array.from((screen.getByLabelText("maybe") as HTMLSelectElement).options, (option) => option.value),
    ).toEqual(["", "true_positive", "false_positive", "benign"]);
  });

  it("explains the label step under the group's legend", async () => {
    await renderStep();
    const describedBy = screen.getByRole("group", { name: "Analyst labels" }).getAttribute("aria-describedby") ?? "";

    expect(document.getElementById(describedBy)?.textContent).toBe("Choose the verdict each label means.");
  });

  it("sends a chosen verdict in the label map", async () => {
    await renderStep();
    fireEvent.change(screen.getByLabelText("maybe"), { target: { value: "benign" } });

    await waitForDelay();

    expect(readProposalMock).toHaveBeenLastCalledWith({
      fields: {},
      labels: { label_map: { maybe: "benign" }, agent_label_map: {} },
    });
  });

  it("drops a label set back to not mapped", async () => {
    await renderStep();
    fireEvent.change(screen.getByLabelText("maybe"), { target: { value: "benign" } });
    fireEvent.change(screen.getByLabelText("maybe"), { target: { value: "" } });

    await waitForDelay();

    expect(readProposalMock).toHaveBeenLastCalledWith({ fields: {}, labels: EMPTY_LABELS });
  });

  it("shows no group for agent labels when every one is mapped", async () => {
    await renderStep();

    expect(screen.queryByRole("group", { name: "Agent labels" })).toBeNull();
  });
});

describe("Confirm", () => {
  it("is marked unavailable while a required field is missing", async () => {
    await renderStep({ proposal: MISSING });

    expect(screen.getByRole("button", { name: "Confirm" }).getAttribute("aria-disabled")).toBe("true");
  });

  it("can still take focus while a required field is missing", async () => {
    await renderStep({ proposal: MISSING });
    const confirm = screen.getByRole("button", { name: "Confirm" });

    confirm.focus();

    expect(document.activeElement).toBe(confirm);
  });

  it("saves nothing while a required field is missing", async () => {
    await renderStep({ proposal: MISSING });

    fireEvent.click(screen.getByRole("button", { name: "Confirm" }));
    await flush();

    expect(saveConfigMock).not.toHaveBeenCalled();
  });

  it("is described by the sentence saying what is missing", async () => {
    await renderStep({ proposal: MISSING });
    const describedBy = screen.getByRole("button", { name: "Confirm" }).getAttribute("aria-describedby") ?? "";

    expect(document.getElementById(describedBy)?.textContent).toBe(
      "Choose a key for Agent verdict before confirming.",
    );
  });

  it("is available once nothing required is missing", async () => {
    await renderStep();

    expect(screen.getByRole("button", { name: "Confirm" }).hasAttribute("aria-disabled")).toBe(false);
  });

  it("is marked unavailable while saving", async () => {
    await renderStep();
    saveConfigMock.mockReturnValue(new Promise(() => {}));

    fireEvent.click(screen.getByRole("button", { name: "Confirm" }));
    await flush();

    expect(screen.getByRole("button", { name: "Confirm" }).getAttribute("aria-disabled")).toBe("true");
  });

  it("saves once however often it is pressed while saving", async () => {
    await renderStep();
    saveConfigMock.mockReturnValue(new Promise(() => {}));

    fireEvent.click(screen.getByRole("button", { name: "Confirm" }));
    await flush();
    fireEvent.click(screen.getByRole("button", { name: "Confirm" }));
    await flush();

    expect(saveConfigMock).toHaveBeenCalledOnce();
  });

  it("saves the edits", async () => {
    await renderStep();
    edit("Agent verdict", "agent.verdict");

    fireEvent.click(screen.getByRole("button", { name: "Confirm" }));
    await flush();

    expect(saveConfigMock).toHaveBeenCalledWith({ fields: { agent_verdict: "agent.verdict" }, labels: EMPTY_LABELS });
  });

  it("says the configuration was saved", async () => {
    await renderStep();

    fireEvent.click(screen.getByRole("button", { name: "Confirm" }));
    await flush();

    expect(screen.getByRole("status").textContent).toBe("Configuration saved. Scoring starts now.");
  });

  it("collapses the step to a summary of each field", async () => {
    await renderStep();
    edit("Agent verdict", "agent.verdict");

    fireEvent.click(screen.getByRole("button", { name: "Confirm" }));
    await flush();

    expect(Array.from(document.querySelectorAll("dl > div"), (row) => row.textContent)).toEqual(["Case IDalert.id", "Agent verdictagent.verdict"]);
  });

  it("adds the labels mapped before saving to the summary", async () => {
    await renderStep();
    fireEvent.change(screen.getByLabelText("maybe"), { target: { value: "benign" } });

    fireEvent.click(screen.getByRole("button", { name: "Confirm" }));
    await flush();

    expect(screen.getByText("Analyst labels").nextElementSibling?.textContent).toBe("maybeBenign");
  });

  it("hides the form once saved", async () => {
    await renderStep();

    fireEvent.click(screen.getByRole("button", { name: "Confirm" }));
    await flush();

    expect(screen.queryByRole("button", { name: "Confirm" })).toBeNull();
  });

  it("moves focus to Change configuration once saved", async () => {
    await renderStep();

    fireEvent.click(screen.getByRole("button", { name: "Confirm" }));
    await flush();

    expect(document.activeElement?.textContent).toBe("Change configuration");
  });

  it("shows why the save failed as an alert", async () => {
    await renderStep();
    saveConfigMock.mockResolvedValue({ error: "Agent verdict needs a key." });

    fireEvent.click(screen.getByRole("button", { name: "Confirm" }));
    await flush();

    expect(screen.getByRole("alert").textContent).toBe("Agent verdict needs a key.");
  });

  it("keeps the form open when the save fails", async () => {
    await renderStep();
    saveConfigMock.mockResolvedValue({ error: "Agent verdict needs a key." });

    fireEvent.click(screen.getByRole("button", { name: "Confirm" }));
    await flush();

    expect(screen.queryByRole("button", { name: "Confirm" })).not.toBeNull();
  });
});

describe("Change configuration", () => {
  it("reopens the form with the saved values", async () => {
    await renderStep();
    edit("Agent verdict", "agent.verdict");
    fireEvent.click(screen.getByRole("button", { name: "Confirm" }));
    await flush();

    fireEvent.click(screen.getByRole("button", { name: "Change configuration" }));

    expect((screen.getByLabelText("Agent verdict") as HTMLInputElement).value).toBe("agent.verdict");
  });

  it("moves focus to the first field", async () => {
    await renderStep();
    fireEvent.click(screen.getByRole("button", { name: "Confirm" }));
    await flush();

    fireEvent.click(screen.getByRole("button", { name: "Change configuration" }));

    expect(document.activeElement).toBe(screen.getByLabelText("Case ID"));
  });
});

describe("a saved configuration", () => {
  it("opens as its summary", async () => {
    await renderStep({ isConfigured: true });

    expect(screen.queryByRole("button", { name: "Change configuration" })).not.toBeNull();
  });

  it("names a field with no key in the server's words", async () => {
    await renderStep({ isConfigured: true });

    expect(screen.getByText("Agent verdict").nextElementSibling?.textContent).toBe("Nothing chosen");
  });

  it("lists the labels mapped by hand with their verdicts", async () => {
    await renderStep({ proposal: SAVED, isConfigured: true });

    expect(screen.getByText("Analyst labels").nextElementSibling?.textContent).toBe("MaliciousTrue positive");
  });

  it("lists no agent labels when none is mapped by hand", async () => {
    await renderStep({ proposal: SAVED, isConfigured: true });

    expect(screen.queryByText("Agent labels")).toBeNull();
  });

  it("keeps its label mappings when confirmed again without edits", async () => {
    await renderStep({ proposal: SAVED, isConfigured: true });
    fireEvent.click(screen.getByRole("button", { name: "Change configuration" }));

    fireEvent.click(screen.getByRole("button", { name: "Confirm" }));
    await flush();

    expect(saveConfigMock).toHaveBeenCalledWith({ fields: {}, labels: SAVED_LABELS });
  });

  it("shows a saved label with its verdict chosen", async () => {
    await renderStep({ proposal: SAVED, isConfigured: true });

    fireEvent.click(screen.getByRole("button", { name: "Change configuration" }));

    expect((screen.getByLabelText("Malicious") as HTMLSelectElement).value).toBe("true_positive");
  });

  it("lists the saved labels before the unmapped ones", async () => {
    await renderStep({ proposal: SAVED, isConfigured: true });

    fireEvent.click(screen.getByRole("button", { name: "Change configuration" }));

    expect(
      Array.from(screen.getByRole("group", { name: "Analyst labels" }).querySelectorAll("select"), (select) =>
        select.labels?.[0]?.textContent,
      ),
    ).toEqual(["Malicious", "maybe"]);
  });

  it("offers no way to unmap a saved label, which saving cannot do", async () => {
    await renderStep({ proposal: SAVED, isConfigured: true });

    fireEvent.click(screen.getByRole("button", { name: "Change configuration" }));

    expect(
      Array.from((screen.getByLabelText("Malicious") as HTMLSelectElement).options, (option) => option.value),
    ).toEqual(["true_positive", "false_positive", "benign"]);
  });

  it("sends a changed saved label", async () => {
    await renderStep({ proposal: SAVED, isConfigured: true });
    fireEvent.click(screen.getByRole("button", { name: "Change configuration" }));

    fireEvent.change(screen.getByLabelText("Malicious"), { target: { value: "benign" } });
    await waitForDelay();

    expect(readProposalMock).toHaveBeenLastCalledWith({
      fields: {},
      labels: { label_map: { Malicious: "benign" }, agent_label_map: {} },
    });
  });

  it("holds no reload when reopened with its labels unchanged", async () => {
    await renderStep({ proposal: SAVED, isConfigured: true });

    fireEvent.click(screen.getByRole("button", { name: "Change configuration" }));

    expect(isReloadHeld()).toBe(false);
  });
});

describe("the reload hold", () => {
  it("holds a reload while the form has unsaved edits", async () => {
    await renderStep();

    edit("Agent verdict", "agent.verdict");

    expect(isReloadHeld()).toBe(true);
  });

  it("holds no reload while the form has no edits", async () => {
    await renderStep();

    expect(isReloadHeld()).toBe(false);
  });

  it("holds a reload while a save is on its way", async () => {
    await renderStep();
    saveConfigMock.mockReturnValue(new Promise(() => {}));
    edit("Agent verdict", "agent.verdict");

    fireEvent.click(screen.getByRole("button", { name: "Confirm" }));
    await flush();

    expect(isReloadHeld()).toBe(true);
  });

  it("lets the reload go once the form is saved", async () => {
    await renderStep();
    edit("Agent verdict", "agent.verdict");

    fireEvent.click(screen.getByRole("button", { name: "Confirm" }));
    await flush();

    expect(isReloadHeld()).toBe(false);
  });

  it("holds no reload when a saved form is reopened unchanged", async () => {
    await renderStep();
    edit("Agent verdict", "agent.verdict");
    fireEvent.click(screen.getByRole("button", { name: "Confirm" }));
    await flush();

    fireEvent.click(screen.getByRole("button", { name: "Change configuration" }));

    expect(isReloadHeld()).toBe(false);
  });

  it("lets the reload go once the step is gone", async () => {
    const { unmount } = await renderStep();
    edit("Agent verdict", "agent.verdict");

    unmount();

    expect(isReloadHeld()).toBe(false);
  });
});

describe("new uploads", () => {
  const WITH_NEW_LABEL: Proposal = { ...PROPOSAL, unmappedAnalystLabels: ["maybe", "unsure"] };

  it("read the proposal again", async () => {
    const { rerender } = await renderStep();
    readProposalMock.mockResolvedValue(WITH_NEW_LABEL);

    rerender(<ConfigStep isConfigured={false} totalsKey={NEW_TOTALS} />);
    await flush();

    expect(screen.queryByLabelText("unsure")).not.toBeNull();
  });

  it("send the unsaved edits along", async () => {
    const { rerender } = await renderStep();
    edit("Agent verdict", "agent.verdict");
    fireEvent.change(screen.getByLabelText("maybe"), { target: { value: "benign" } });

    rerender(<ConfigStep isConfigured={false} totalsKey={NEW_TOTALS} />);
    await flush();

    expect(readProposalMock).toHaveBeenLastCalledWith({
      fields: { agent_verdict: "agent.verdict" },
      labels: { label_map: { maybe: "benign" }, agent_label_map: {} },
    });
  });

  it("keep a typed value", async () => {
    const { rerender } = await renderStep();
    edit("Agent verdict", "agent.verdict");
    readProposalMock.mockResolvedValue({
      ...WITH_NEW_LABEL,
      fields: [CASE_ID_FIELD, { ...AGENT_VERDICT_FIELD, value: "agent.verdict" }],
    });

    rerender(<ConfigStep isConfigured={false} totalsKey={NEW_TOTALS} />);
    await flush();

    expect((screen.getByLabelText("Agent verdict") as HTMLInputElement).value).toBe("agent.verdict");
  });

  it("keep a mapped label the new proposal no longer lists as unmapped", async () => {
    const { rerender } = await renderStep();
    fireEvent.change(screen.getByLabelText("maybe"), { target: { value: "benign" } });
    readProposalMock.mockResolvedValue({ ...PROPOSAL, unmappedAnalystLabels: ["unsure"] });

    rerender(<ConfigStep isConfigured={false} totalsKey={NEW_TOTALS} />);
    await flush();

    expect((screen.getByLabelText("maybe") as HTMLSelectElement).value).toBe("benign");
  });

  it("add a newly uploaded label beside the edits", async () => {
    const { rerender } = await renderStep();
    edit("Agent verdict", "agent.verdict");
    readProposalMock.mockResolvedValue(WITH_NEW_LABEL);

    rerender(<ConfigStep isConfigured={false} totalsKey={NEW_TOTALS} />);
    await flush();

    expect(screen.queryByLabelText("unsure")).not.toBeNull();
  });

  it("still save a typed value the new proposal echoes back", async () => {
    const { rerender } = await renderStep();
    edit("Agent verdict", "agent.verdict");
    readProposalMock.mockResolvedValue({
      ...PROPOSAL,
      fields: [CASE_ID_FIELD, { ...AGENT_VERDICT_FIELD, value: "agent.verdict" }],
    });
    rerender(<ConfigStep isConfigured={false} totalsKey={NEW_TOTALS} />);
    await flush();

    fireEvent.click(screen.getByRole("button", { name: "Confirm" }));
    await flush();

    expect(saveConfigMock).toHaveBeenCalledWith({ fields: { agent_verdict: "agent.verdict" }, labels: EMPTY_LABELS });
  });

  it("refresh the share texts", async () => {
    const { rerender } = await renderStep();
    edit("Agent verdict", "agent.verdict");
    readProposalMock.mockResolvedValue({
      ...PROPOSAL,
      fields: [{ ...CASE_ID_FIELD, shareText: "400 of 400 runs (100%)" }, AGENT_VERDICT_FIELD],
    });

    rerender(<ConfigStep isConfigured={false} totalsKey={NEW_TOTALS} />);
    await flush();

    expect(screen.queryByText("400 of 400 runs (100%)")).not.toBeNull();
  });

  it("ask nothing while the totals stay the same", async () => {
    const { rerender } = await renderStep();

    rerender(<ConfigStep isConfigured={false} totalsKey={TOTALS} />);
    await flush();

    expect(readProposalMock).toHaveBeenCalledOnce();
  });
});
