import { cleanup, render, screen, waitFor, within } from "@testing-library/react";
import { userEvent } from "@testing-library/user-event";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { registerPanel, resetRegistryForTests } from "../registry";
import { takeReloadNote } from "../reload-note";
import { DEMO_RESULTS, DEMO_VIEW } from "../test-fixtures";
import { Data } from "./Data";

import type { View } from "../view";

const VIEW_WITH_NOTES: View = {
  ...DEMO_VIEW,
  coverage: [
    {
      text: "150 of 201 verdicts matched a trace (75%).",
      hint: "Check that detecttrace.yaml names the right trace folder.",
      is_low: true,
    },
  ],
  notes: [
    {
      severity: "invalid_input",
      severity_label: "Invalid input",
      count_text: "37",
      message: "verdict rows have an unknown label.",
      hint: "Add the labels to label_map in detecttrace.yaml.",
      examples: [
        { subject: "DT-IT-0001", detail: "label 'maybe'" },
        { subject: "</script><img src=x onerror=alert(1)>", detail: null },
      ],
      more_text: "2 of 37 shown.",
    },
    {
      severity: "warning",
      severity_label: "Warning",
      count_text: "3",
      message: "traces have no case ID.",
      hint: "",
      examples: [],
      more_text: null,
    },
  ],
};

const UI_VIEW: View = {
  ...DEMO_VIEW,
  mode: "ui",
  ui: { data_intro_text: "Upload, check, confirm.", updated_text: "The dashboard was updated." },
};

const UI_STATE = {
  is_configured: false,
  can_configure: false,
  has_results: false,
  span_count_text: "2,129 spans stored.",
  verdict_count_text: "No verdicts stored.",
  trace_family_text: "OTLP traces",
  checklist_classes: ["phishing", "malware"],
  checklist_classes_text: "phishing and malware",
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
  label_map: {},
  agent_label_map: {},
  unmapped_analyst_labels: [],
  unmapped_agent_labels: [],
  user_labels: { label_map: {}, agent_label_map: {} },
  verdict_choices: ["true_positive", "false_positive", "benign"],
  verdict_choice_labels: [
    { value: "true_positive", label: "True positive" },
    { value: "false_positive", label: "False positive" },
    { value: "benign", label: "Benign" },
  ],
  labels_help_text: "Choose the verdict each label means.",
  not_set_text: "Not set",
  not_mapped_text: "Not mapped",
  notes: [],
  missing_required: [],
  missing_text: null,
  agent_run_count_text: "201 agent runs found.",
};

afterEach(() => {
  cleanup();
  resetRegistryForTests();
  vi.unstubAllGlobals();
  sessionStorage.clear();
});

function respondTo(routes: Record<string, unknown>) {
  return (url: string) =>
    Promise.resolve(new Response(JSON.stringify(routes[url] ?? {}), { status: 200 }));
}

describe("the demo", () => {
  it("says no input problems were found", () => {
    render(<Data view={DEMO_VIEW} results={DEMO_RESULTS} />);

    expect(screen.queryByText("No input problems were found.")).not.toBeNull();
  });

  it("lists the coverage lines", () => {
    render(<Data view={DEMO_VIEW} results={DEMO_RESULTS} />);

    expect(screen.getAllByRole("listitem").map((item) => item.textContent)).toEqual([
      "201 of 201 verdicts matched a trace (100%).",
      "201 of 201 traces matched a verdict (100%).",
    ]);
  });
});

describe("offline mode", () => {
  it("shows no upload step", () => {
    render(<Data view={DEMO_VIEW} results={DEMO_RESULTS} />);

    expect(screen.queryByRole("heading", { name: "1 · Upload" })).toBeNull();
  });

  it("asks the network nothing", () => {
    const fetchMock = vi.fn();
    vi.stubGlobal("fetch", fetchMock);
    render(<Data view={DEMO_VIEW} results={DEMO_RESULTS} />);

    expect(fetchMock).not.toHaveBeenCalled();
  });
});

describe("ui mode", () => {
  let fetchMock: ReturnType<typeof vi.fn<(url: string, init?: RequestInit) => Promise<Response>>>;

  beforeEach(() => {
    fetchMock = vi.fn((url: string) =>
      Promise.resolve(new Response(JSON.stringify(url === "/api/ui/state" ? UI_STATE : {}), { status: 200 })),
    );
    vi.stubGlobal("fetch", fetchMock);
    // jsdom has no modal dialogs; these follow the browser's open and close.
    HTMLDialogElement.prototype.showModal = function (this: HTMLDialogElement) {
      this.setAttribute("open", "");
    };
    HTMLDialogElement.prototype.close = function (this: HTMLDialogElement) {
      this.removeAttribute("open");
      this.dispatchEvent(new Event("close"));
    };
  });

  afterEach(() => {
    Reflect.deleteProperty(HTMLDialogElement.prototype, "showModal");
    Reflect.deleteProperty(HTMLDialogElement.prototype, "close");
  });

  it("marks the coverage list with the page's mode", () => {
    render(<Data view={UI_VIEW} results={DEMO_RESULTS} />);

    expect(screen.getByRole("list", { name: "Coverage" }).dataset.mode).toBe("ui");
  });

  it("numbers the steps it shows", () => {
    render(<Data view={UI_VIEW} results={DEMO_RESULTS} />);

    expect(screen.getAllByRole("heading", { level: 2 }).map((heading) => heading.textContent)).toEqual([
      "1 · Upload",
      "3 · Data notes",
    ]);
  });

  it("shows an upload card for each kind of file", () => {
    render(<Data view={UI_VIEW} results={DEMO_RESULTS} />);

    expect(screen.getAllByRole("heading", { level: 3 }).map((heading) => heading.textContent)).toEqual([
      "Traces",
      "Verdicts",
      "Checklists",
    ]);
  });

  it("limits each card's chooser to its suffixes", () => {
    render(<Data view={UI_VIEW} results={DEMO_RESULTS} />);

    expect(
      ["Add trace files", "Add verdict files", "Add checklist files"].map((label) =>
        screen.getByLabelText(label).getAttribute("accept"),
      ),
    ).toEqual([".jsonl,.json,.gz,.zst", ".csv", ".yaml,.yml"]);
  });

  it("introduces the page's steps in the view's words", () => {
    render(<Data view={UI_VIEW} results={DEMO_RESULTS} />);

    expect(screen.queryByText("Upload, check, confirm.")).not.toBeNull();
  });

  it("titles the data notes one level below their step", () => {
    render(<Data view={{ ...VIEW_WITH_NOTES, mode: "ui", ui: UI_VIEW.ui }} results={DEMO_RESULTS} />);

    expect(screen.getByRole("heading", { level: 3, name: "37 verdict rows have an unknown label." })).not.toBeNull();
  });

  it("names the stored spans as spans", async () => {
    render(<Data view={UI_VIEW} results={DEMO_RESULTS} />);

    expect((await screen.findByText("2,129 spans stored.")).previousElementSibling?.textContent).toBe("Spans");
  });

  it("does not announce the totals, which the cards already announce", async () => {
    render(<Data view={UI_VIEW} results={DEMO_RESULTS} />);

    expect((await screen.findByText("2,129 spans stored.")).closest("[aria-live], [role=status]")).toBeNull();
  });

  it("shows why the totals could not be read as an alert", async () => {
    fetchMock.mockImplementation(() => Promise.reject(new TypeError("Failed to fetch")));
    render(<Data view={UI_VIEW} results={DEMO_RESULTS} />);

    expect((await screen.findByRole("alert")).textContent).toBe(
      "The app could not be reached. Check that detecttrace ui is still running.",
    );
  });

  it("shows the totals of the newest request when an older one answers last", async () => {
    const user = userEvent.setup();
    const settlers: ((body: unknown) => void)[] = [];
    fetchMock.mockImplementation((url: string) =>
      url === "/api/ui/state"
        ? new Promise((resolve) => {
            settlers.push((body) => resolve(new Response(JSON.stringify(body), { status: 200 })));
          })
        : Promise.resolve(new Response(JSON.stringify({ stored_text: "Done.", problems: [] }), { status: 200 })),
    );
    render(<Data view={UI_VIEW} results={DEMO_RESULTS} />);
    await user.upload(screen.getByLabelText("Add verdict files"), new File(["x"], "v.csv"));
    await waitFor(() => expect(settlers).toHaveLength(2));

    settlers[1]!({ ...UI_STATE, verdict_count_text: "201 verdicts stored." });
    await screen.findByText("201 verdicts stored.");
    settlers[0]!(UI_STATE);
    await new Promise((resolve) => setTimeout(resolve, 0));

    expect(screen.queryByText("No verdicts stored.")).toBeNull();
  });

  it("shows the totals stored so far", async () => {
    render(<Data view={UI_VIEW} results={DEMO_RESULTS} />);

    expect((await screen.findByText("2,129 spans stored.")).tagName).toBe("DD");
  });

  it("lists the stored checklists' classes in the server's words", async () => {
    render(<Data view={UI_VIEW} results={DEMO_RESULTS} />);

    expect((await screen.findByText("phishing and malware")).tagName).toBe("DD");
  });

  it("reads the totals again after an upload", async () => {
    const user = userEvent.setup();
    render(<Data view={UI_VIEW} results={DEMO_RESULTS} />);
    fetchMock.mockImplementation((url: string) =>
      Promise.resolve(
        new Response(
          JSON.stringify(
            url === "/api/ui/state"
              ? { ...UI_STATE, verdict_count_text: "201 verdicts stored." }
              : { stored_text: "201 verdicts added.", problems: [] },
          ),
          { status: 200 },
        ),
      ),
    );

    await user.upload(screen.getByLabelText("Add verdict files"), new File(["x"], "v.csv"));

    expect(await screen.findByText("201 verdicts stored.")).not.toBeNull();
  });

  it("moves focus into the clear dialog", async () => {
    const user = userEvent.setup();
    render(<Data view={UI_VIEW} results={DEMO_RESULTS} />);

    await user.click(screen.getByRole("button", { name: "Clear all data" }));

    expect(document.activeElement?.textContent).toBe("Cancel");
  });

  it("does not clear the data when the dialog is cancelled", async () => {
    const user = userEvent.setup();
    render(<Data view={UI_VIEW} results={DEMO_RESULTS} />);
    await user.click(screen.getByRole("button", { name: "Clear all data" }));

    await user.click(within(screen.getByRole("dialog")).getByRole("button", { name: "Cancel" }));

    expect(fetchMock.mock.calls.map(([url]) => url)).not.toContain("/api/data/clear");
  });

  it("returns focus to the clear button when the dialog closes", async () => {
    const user = userEvent.setup();
    render(<Data view={UI_VIEW} results={DEMO_RESULTS} />);
    const opener = screen.getByRole("button", { name: "Clear all data" });
    await user.click(opener);

    await user.click(within(screen.getByRole("dialog")).getByRole("button", { name: "Cancel" }));

    expect(document.activeElement).toBe(opener);
  });

  it("clears the data once confirmed, then reloads", async () => {
    const user = userEvent.setup();
    const reload = vi.fn();
    vi.stubGlobal("location", { reload });
    render(<Data view={UI_VIEW} results={DEMO_RESULTS} />);
    await user.click(screen.getByRole("button", { name: "Clear all data" }));

    await user.click(within(screen.getByRole("dialog")).getByRole("button", { name: "Clear all data" }));

    await waitFor(() => expect(reload).toHaveBeenCalled());
  });

  it("sends the clear only after the confirm", async () => {
    const user = userEvent.setup();
    vi.stubGlobal("location", { reload: vi.fn() });
    render(<Data view={UI_VIEW} results={DEMO_RESULTS} />);
    await user.click(screen.getByRole("button", { name: "Clear all data" }));

    await user.click(within(screen.getByRole("dialog")).getByRole("button", { name: "Clear all data" }));

    expect(fetchMock.mock.calls.map(([url]) => url)).toContain("/api/data/clear");
  });

  it("shows why the stored checklists could not be loaded as an alert", async () => {
    fetchMock.mockImplementation(() =>
      Promise.resolve(
        new Response(
          JSON.stringify({ ...UI_STATE, checklist_classes: [], checklist_error_text: "phishing.yaml is not valid YAML." }),
          { status: 200 },
        ),
      ),
    );
    render(<Data view={UI_VIEW} results={DEMO_RESULTS} />);

    expect((await screen.findByRole("alert")).textContent).toBe("phishing.yaml is not valid YAML.");
  });

  it("says it is clearing while the clear is on its way", async () => {
    const user = userEvent.setup();
    render(<Data view={UI_VIEW} results={DEMO_RESULTS} />);
    fetchMock.mockImplementation(() => new Promise<Response>(() => {}));
    await user.click(screen.getByRole("button", { name: "Clear all data" }));

    await user.click(within(screen.getByRole("dialog")).getByRole("button", { name: "Clear all data" }));

    expect(within(screen.getByRole("dialog")).queryByRole("button", { name: "Clearing…" })).not.toBeNull();
  });

  it("returns focus to the upload step after the clear's reload", async () => {
    const user = userEvent.setup();
    vi.stubGlobal("location", { reload: vi.fn() });
    render(<Data view={UI_VIEW} results={DEMO_RESULTS} />);
    await user.click(screen.getByRole("button", { name: "Clear all data" }));
    await user.click(within(screen.getByRole("dialog")).getByRole("button", { name: "Clear all data" }));
    await waitFor(() => expect(sessionStorage.length).toBe(1));

    expect(takeReloadNote()).toEqual({ focusId: "data-step-upload", shouldAnnounce: false });
  });

  it("leaves out the configuration step until it can be configured", async () => {
    render(<Data view={UI_VIEW} results={DEMO_RESULTS} />);
    await screen.findByText("2,129 spans stored.");

    expect(screen.queryByRole("heading", { name: "2 · Configuration" })).toBeNull();
  });

  it("shows the configuration step once traces and verdicts are stored", async () => {
    fetchMock.mockImplementation((url: string) =>
      Promise.resolve(
        new Response(JSON.stringify(url === "/api/ui/state" ? { ...UI_STATE, can_configure: true } : PROPOSAL), {
          status: 200,
        }),
      ),
    );
    render(<Data view={UI_VIEW} results={DEMO_RESULTS} />);

    expect(await screen.findByText("201 agent runs found.")).not.toBeNull();
  });

  it("leaves out the data notes before there are results", async () => {
    render(<Data view={UI_VIEW} results={null} />);
    await screen.findByText("2,129 spans stored.");

    expect(screen.queryByRole("heading", { name: "3 · Data notes" })).toBeNull();
  });

  it("shows why a clear failed and stays on the page", async () => {
    const user = userEvent.setup();
    render(<Data view={UI_VIEW} results={DEMO_RESULTS} />);
    fetchMock.mockImplementation(() =>
      Promise.resolve(new Response(JSON.stringify({ code: 403, message: "This request was refused." }), { status: 403 })),
    );
    await user.click(screen.getByRole("button", { name: "Clear all data" }));

    await user.click(within(screen.getByRole("dialog")).getByRole("button", { name: "Clear all data" }));

    expect((await screen.findByRole("alert")).textContent).toBe("This request was refused.");
  });
});

describe("after the page reloads itself", () => {
  beforeEach(() => {
    vi.stubGlobal(
      "fetch",
      vi.fn(respondTo({ "/api/ui/state": { ...UI_STATE, can_configure: true, is_configured: true }, "/api/config/proposal": PROPOSAL })),
    );
  });

  it("says the dashboard was updated", async () => {
    sessionStorage.setItem("detecttrace:reload-note", JSON.stringify({ focusId: null, shouldAnnounce: true }));
    render(<Data view={UI_VIEW} results={DEMO_RESULTS} />);

    expect(await screen.findByText("The dashboard was updated.")).not.toBeNull();
  });

  it("says it in a polite live region", async () => {
    sessionStorage.setItem("detecttrace:reload-note", JSON.stringify({ focusId: null, shouldAnnounce: true }));
    render(<Data view={UI_VIEW} results={DEMO_RESULTS} />);

    expect((await screen.findByText("The dashboard was updated.")).getAttribute("aria-live")).toBe("polite");
  });

  it("says nothing after a reload the page did not make", async () => {
    render(<Data view={UI_VIEW} results={DEMO_RESULTS} />);
    await screen.findByText("2,129 spans stored.");

    expect(screen.queryByText("The dashboard was updated.")).toBeNull();
  });

  it("moves focus to the step the reader was in", async () => {
    sessionStorage.setItem(
      "detecttrace:reload-note",
      JSON.stringify({ focusId: "data-step-config", shouldAnnounce: true }),
    );
    render(<Data view={UI_VIEW} results={DEMO_RESULTS} />);

    await waitFor(() => expect(document.activeElement?.textContent).toBe("2 · Configuration"));
  });

  it("moves focus to the upload step when the reader's step is not shown", async () => {
    vi.stubGlobal("fetch", vi.fn(respondTo({ "/api/ui/state": UI_STATE })));
    sessionStorage.setItem(
      "detecttrace:reload-note",
      JSON.stringify({ focusId: "data-step-config", shouldAnnounce: true }),
    );
    render(<Data view={UI_VIEW} results={DEMO_RESULTS} />);

    await waitFor(() => expect(document.activeElement?.textContent).toBe("1 · Upload"));
  });
});

describe("notes", () => {
  it("shows each note's severity label", () => {
    render(<Data view={VIEW_WITH_NOTES} results={DEMO_RESULTS} />);

    expect(screen.getAllByText(/^(Invalid input|Warning)$/).map((label) => label.textContent)).toEqual([
      "Invalid input",
      "Warning",
    ]);
  });

  it("titles each note with its count and message", () => {
    render(<Data view={VIEW_WITH_NOTES} results={DEMO_RESULTS} />);

    expect(screen.getAllByRole("heading", { level: 2 }).map((heading) => heading.textContent)).toEqual([
      "37 verdict rows have an unknown label.",
      "3 traces have no case ID.",
    ]);
  });

  it("shows a note's fix hint", () => {
    render(<Data view={VIEW_WITH_NOTES} results={DEMO_RESULTS} />);

    expect(screen.queryByText("Add the labels to label_map in detecttrace.yaml.")).not.toBeNull();
  });

  it("lists a note's examples with their details", () => {
    render(<Data view={VIEW_WITH_NOTES} results={DEMO_RESULTS} />);

    expect(screen.getByRole("list", { name: "Examples" }).textContent).toBe(
      "DT-IT-0001: label 'maybe'</script><img src=x onerror=alert(1)>2 of 37 shown.",
    );
  });

  it("says how many examples of how many are shown", () => {
    render(<Data view={VIEW_WITH_NOTES} results={DEMO_RESULTS} />);

    expect(screen.queryByText("2 of 37 shown.")).not.toBeNull();
  });

  it("renders a hostile example as text, adding no element", () => {
    render(<Data view={VIEW_WITH_NOTES} results={DEMO_RESULTS} />);

    expect(document.querySelector("img, script")).toBeNull();
  });
});

describe("coverage", () => {
  it("marks low coverage with a warning in words", () => {
    render(<Data view={VIEW_WITH_NOTES} results={DEMO_RESULTS} />);

    expect(screen.getByRole("list", { name: "Coverage" }).textContent).toBe(
      "Warning: 150 of 201 verdicts matched a trace (75%).Check that detecttrace.yaml names the right trace folder.",
    );
  });
});

it("shows the notes-after panels", () => {
  registerPanel("notes-after", () => <p>Notes panel</p>);
  render(<Data view={DEMO_VIEW} results={DEMO_RESULTS} />);

  expect(screen.queryByText("Notes panel")).not.toBeNull();
});
