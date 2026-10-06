import { cleanup, render, screen, waitFor, within } from "@testing-library/react";
import { userEvent } from "@testing-library/user-event";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { registerPanel, resetRegistryForTests } from "../registry";
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

const UI_VIEW: View = { ...DEMO_VIEW, mode: "ui" };

const UI_STATE = {
  is_configured: false,
  can_configure: false,
  has_results: false,
  span_count_text: "2,129 spans stored.",
  verdict_count_text: "No verdicts stored.",
  trace_family_text: "OTLP traces",
  checklist_classes: ["phishing", "malware"],
};

afterEach(() => {
  cleanup();
  resetRegistryForTests();
  vi.unstubAllGlobals();
});

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
      ["Traces", "Verdicts", "Checklists"].map((title) =>
        screen.getByLabelText(`Choose files for ${title}`).getAttribute("accept"),
      ),
    ).toEqual([".jsonl,.json,.gz,.zst", ".csv", ".yaml,.yml"]);
  });

  it("shows the totals stored so far", async () => {
    render(<Data view={UI_VIEW} results={DEMO_RESULTS} />);

    expect((await screen.findByText("2,129 spans stored.")).tagName).toBe("DD");
  });

  it("lists the stored checklists' classes", async () => {
    render(<Data view={UI_VIEW} results={DEMO_RESULTS} />);

    expect((await screen.findByText("phishing, malware")).tagName).toBe("DD");
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

    await user.upload(screen.getByLabelText("Choose files for Verdicts"), new File(["x"], "v.csv"));

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
