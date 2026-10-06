import { act, cleanup, fireEvent, render, screen, within } from "@testing-library/react";
import { userEvent } from "@testing-library/user-event";
import { Profiler } from "react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { WINDOW_THRESHOLD } from "../case-window";
import { registerPanel, resetRegistryForTests } from "../registry";
import { DEMO_RESULTS, DEMO_VIEW } from "../test-fixtures";
import { Cases } from "./Cases";

import type { CaseDetail, Results } from "../results";

const TRUE_POSITIVE = 0;
const FALSE_POSITIVE = 1;
const BENIGN = 2;
const UNKNOWN = -1;

type SyntheticCase = { id: string; analyst: number; agent: number };

/** One class, one week, one version; each case missed the one checklist step. */
function createResults(
  cases: readonly SyntheticCase[],
  { names = ["phishing", "v1", "headers"], details = [] as CaseDetail[] } = {},
): Results {
  const [className, version, item] = names;
  const each = <T,>(value: T): T[] => cases.map(() => value);
  return {
    case_rows: {
      verdict_codes: ["true_positive", "false_positive", "benign"],
      unknown_verdict_code: UNKNOWN,
      checklists: [{ class_index: 0, items: [3] }],
      strings: [className!, "2026-W10", version!, item!],
      columns: {
        case_id: cases.map((entry) => entry.id),
        class_index: each(0),
        week: each(1),
        version: each(2),
        analyst: cases.map((entry) => entry.analyst),
        agent: cases.map((entry) => entry.agent),
        satisfied: each(0),
        not_called_items: each([0]),
        wrong_argument_items: each([]),
        failed_items: each([]),
      },
    },
    case_detail: details,
  };
}

function createManyCases(count: number): SyntheticCase[] {
  return Array.from({ length: count }, (_, index) => ({
    id: `C${String(index).padStart(5, "0")}`,
    analyst: TRUE_POSITIVE,
    agent: TRUE_POSITIVE,
  }));
}

function countText(): string | null {
  return screen.getByText(/^Showing /).textContent;
}

function caseButton(caseId: string): HTMLElement {
  return screen.getByRole("button", { name: caseId });
}

function scrollTable(scrollTop: number): void {
  fireEvent.scroll(screen.getByRole("region", { name: /^Cases, newest week first/ }), {
    target: { scrollTop },
  });
}

afterEach(() => {
  cleanup();
  resetRegistryForTests();
  window.history.replaceState(null, "", "#");
});

describe("the demo", () => {
  it("shows every case in the count line", () => {
    render(<Cases view={DEMO_VIEW} results={DEMO_RESULTS} />);

    expect(countText()).toBe("Showing 201 of 201 cases.");
  });

  it("says which cases carry tool calls", () => {
    render(<Cases view={DEMO_VIEW} results={DEMO_RESULTS} />);

    expect(screen.queryByText(DEMO_VIEW.cases.detail_sentence)).not.toBeNull();
  });

  it("lists an opened case's tool calls with their arguments", async () => {
    window.history.replaceState(null, "", "#/cases?q=DT-OC-0083");
    render(<Cases view={DEMO_VIEW} results={DEMO_RESULTS} />);

    await userEvent.click(caseButton("DT-OC-0083"));

    expect(screen.queryByText('{"app_id": "6b659285-6fdf-7a0e-639a-2670925f9cc0"}')).not.toBeNull();
  });

  it("names each unsatisfied step of an opened case", async () => {
    window.history.replaceState(null, "", "#/cases?q=DT-IT-0073");
    render(<Cases view={DEMO_VIEW} results={DEMO_RESULTS} />);

    await userEvent.click(caseButton("DT-IT-0073"));

    expect(screen.getAllByRole("listitem").map((item) => item.textContent)).toContain(
      "Missedmfa_checknot called",
    );
  });
});

describe("filters", () => {
  it("a chosen class keeps only its cases", async () => {
    render(<Cases view={DEMO_VIEW} results={DEMO_RESULTS} />);

    await userEvent.selectOptions(screen.getByRole("combobox", { name: "Alert class" }), "oauth_consent");

    expect(countText()).toBe("Showing 95 of 201 cases.");
  });

  it("a chosen class is stored in the hash by its anchor", async () => {
    render(<Cases view={DEMO_VIEW} results={DEMO_RESULTS} />);

    await userEvent.selectOptions(screen.getByRole("combobox", { name: "Alert class" }), "oauth_consent");

    expect(window.location.hash).toBe("#/?class=class-1");
  });

  it("all classes removes the class from the hash", async () => {
    window.history.replaceState(null, "", "#/?class=class-1");
    render(<Cases view={DEMO_VIEW} results={DEMO_RESULTS} />);

    await userEvent.selectOptions(screen.getByRole("combobox", { name: "Alert class" }), "All classes");

    expect(window.location.hash).toBe("#/");
  });

  it("the class in the hash filters the rows", () => {
    window.history.replaceState(null, "", "#/?class=class-0");
    render(<Cases view={DEMO_VIEW} results={DEMO_RESULTS} />);

    expect(countText()).toBe("Showing 106 of 201 cases.");
  });

  it("an unknown class in the hash keeps every case", () => {
    window.history.replaceState(null, "", "#/?class=class-9");
    render(<Cases view={DEMO_VIEW} results={DEMO_RESULTS} />);

    expect(countText()).toBe("Showing 201 of 201 cases.");
  });

  it("dangerous false closes keeps only those cases", async () => {
    render(<Cases view={DEMO_VIEW} results={DEMO_RESULTS} />);

    await userEvent.selectOptions(screen.getByRole("combobox", { name: "Result" }), "Dangerous false closes");

    expect(countText()).toBe("Showing 4 of 201 cases.");
  });

  it("dangerous false closes is stored in the hash", async () => {
    render(<Cases view={DEMO_VIEW} results={DEMO_RESULTS} />);

    await userEvent.selectOptions(screen.getByRole("combobox", { name: "Result" }), "Dangerous false closes");

    expect(window.location.hash).toBe("#/?result=dangerous");
  });

  it("disagreements keeps every case where the verdicts differ", async () => {
    render(<Cases view={DEMO_VIEW} results={DEMO_RESULTS} />);

    await userEvent.selectOptions(screen.getByRole("combobox", { name: "Result" }), "Disagreements");

    expect(countText()).toBe("Showing 19 of 201 cases.");
  });

  it("disagreements is stored in the hash", async () => {
    render(<Cases view={DEMO_VIEW} results={DEMO_RESULTS} />);

    await userEvent.selectOptions(screen.getByRole("combobox", { name: "Result" }), "Disagreements");

    expect(window.location.hash).toBe("#/?result=disagree");
  });

  it("all results removes the result from the hash", async () => {
    window.history.replaceState(null, "", "#/?result=disagree");
    render(<Cases view={DEMO_VIEW} results={DEMO_RESULTS} />);

    await userEvent.selectOptions(screen.getByRole("combobox", { name: "Result" }), "All results");

    expect(window.location.hash).toBe("#/");
  });

  it("an old link's dangerous=1 keeps the dangerous false closes", () => {
    window.history.replaceState(null, "", "#/?dangerous=1");
    render(<Cases view={DEMO_VIEW} results={DEMO_RESULTS} />);

    expect(countText()).toBe("Showing 4 of 201 cases.");
  });

  it("a new result choice drops an old link's dangerous=1", async () => {
    window.history.replaceState(null, "", "#/?dangerous=1");
    render(<Cases view={DEMO_VIEW} results={DEMO_RESULTS} />);

    await userEvent.selectOptions(screen.getByRole("combobox", { name: "Result" }), "All results");

    expect(window.location.hash).toBe("#/");
  });

  it("a search keeps the matching case IDs", async () => {
    render(<Cases view={DEMO_VIEW} results={DEMO_RESULTS} />);

    await userEvent.type(screen.getByRole("searchbox", { name: "Search case ID" }), "dt-it-0003");

    expect(countText()).toBe("Showing 1 of 201 cases.");
  });

  it("a search is stored in the hash", async () => {
    render(<Cases view={DEMO_VIEW} results={DEMO_RESULTS} />);

    await userEvent.type(screen.getByRole("searchbox", { name: "Search case ID" }), "DT-IT-0003");

    expect(window.location.hash).toBe("#/?q=DT-IT-0003");
  });

  it("filters from the hash combine", () => {
    window.history.replaceState(null, "", "#/?class=class-1&result=dangerous&q=DT-IT");
    render(<Cases view={DEMO_VIEW} results={DEMO_RESULTS} />);

    expect(screen.queryByText("No cases match these filters.")).not.toBeNull();
  });
});

describe("expansion", () => {
  const results = createResults([{ id: "A", analyst: TRUE_POSITIVE, agent: BENIGN }]);

  it("a row starts collapsed", () => {
    render(<Cases view={DEMO_VIEW} results={results} />);

    expect(caseButton("A").getAttribute("aria-expanded")).toBe("false");
  });

  it("Enter on a row's button expands it", async () => {
    render(<Cases view={DEMO_VIEW} results={results} />);
    act(() => caseButton("A").focus());

    await userEvent.keyboard("{Enter}");

    expect(caseButton("A").getAttribute("aria-expanded")).toBe("true");
  });

  it("Space on a row's button expands it", async () => {
    render(<Cases view={DEMO_VIEW} results={results} />);
    act(() => caseButton("A").focus());

    await userEvent.keyboard(" ");

    expect(caseButton("A").getAttribute("aria-expanded")).toBe("true");
  });

  it("a second Enter collapses the row", async () => {
    render(<Cases view={DEMO_VIEW} results={results} />);
    act(() => caseButton("A").focus());

    await userEvent.keyboard("{Enter}{Enter}");

    expect(caseButton("A").getAttribute("aria-expanded")).toBe("false");
  });

  it("an expanded row's button controls its detail", async () => {
    render(<Cases view={DEMO_VIEW} results={results} />);

    await userEvent.click(caseButton("A"));

    const detail = document.getElementById(caseButton("A").getAttribute("aria-controls")!);
    expect(detail?.textContent).toContain("Checklist steps not satisfied");
  });

  it("a collapsed row's button controls nothing", () => {
    render(<Cases view={DEMO_VIEW} results={results} />);

    expect(caseButton("A").getAttribute("aria-controls")).toBeNull();
  });

  it("a case without tool calls in the results says how to include them", async () => {
    render(<Cases view={DEMO_VIEW} results={results} />);

    await userEvent.click(caseButton("A"));

    expect(screen.queryByText("Details not included. Raise dashboard.max_detail_cases.")).not.toBeNull();
  });

  it("shows the case-detail panels inside an expanded row", async () => {
    registerPanel("case-detail", () => <p>Case panel</p>);
    render(<Cases view={DEMO_VIEW} results={results} />);

    await userEvent.click(caseButton("A"));

    expect(screen.getByText("Case panel").closest("tr")?.id).toBe(caseButton("A").getAttribute("aria-controls"));
  });

  it("tells a case-detail panel which case it is in", async () => {
    registerPanel("case-detail", ({ caseId }) => <p>{`Panel for ${caseId}`}</p>);
    render(<Cases view={DEMO_VIEW} results={results} />);

    await userEvent.click(caseButton("A"));

    expect(screen.queryByText("Panel for A")).not.toBeNull();
  });
});

describe("windowing", () => {
  const results = createResults(createManyCases(5000));

  it("draws a bounded number of rows for 5,000 cases", () => {
    render(<Cases view={DEMO_VIEW} results={results} />);

    expect(screen.getAllByRole("button", { name: /^C\d{5}$/ }).length).toBeLessThan(WINDOW_THRESHOLD);
  });

  it("draws the rows at the scroll position", () => {
    render(<Cases view={DEMO_VIEW} results={results} />);

    scrollTable(2500 * 52);

    expect(screen.queryByRole("button", { name: "C02500" })).not.toBeNull();
  });

  it("drops the rows far above the scroll position", () => {
    render(<Cases view={DEMO_VIEW} results={results} />);

    scrollTable(2500 * 52);

    expect(screen.queryByRole("button", { name: "C00000" })).toBeNull();
  });

  it("keeps an expanded row expanded after it scrolls out and back", async () => {
    render(<Cases view={DEMO_VIEW} results={results} />);
    await userEvent.click(caseButton("C00000"));

    scrollTable(2500 * 52);
    scrollTable(0);

    expect(caseButton("C00000").getAttribute("aria-expanded")).toBe("true");
  });

  it("counts every row of the whole table for a screen reader", () => {
    render(<Cases view={DEMO_VIEW} results={results} />);

    expect(screen.getByRole("table").getAttribute("aria-rowcount")).toBe("5001");
  });

  it("gives a drawn row its place in the whole table", () => {
    render(<Cases view={DEMO_VIEW} results={results} />);

    scrollTable(2500 * 52);

    expect(caseButton("C02500").closest("tr")?.getAttribute("aria-rowindex")).toBe("2502");
  });

  it("counts an opened detail as a row", async () => {
    render(<Cases view={DEMO_VIEW} results={results} />);

    await userEvent.click(caseButton("C00000"));

    expect(caseButton("C00001").closest("tr")?.getAttribute("aria-rowindex")).toBe("4");
  });

  it("keeps focus on a row when the table scrolls far from it", () => {
    render(<Cases view={DEMO_VIEW} results={results} />);
    act(() => caseButton("C00000").focus());

    scrollTable(2500 * 52);

    expect(document.activeElement?.textContent).toBe("C00000");
  });

  it("keeps a focused row below the window when the table scrolls back up", () => {
    render(<Cases view={DEMO_VIEW} results={results} />);
    scrollTable(2500 * 52);
    act(() => caseButton("C02500").focus());

    scrollTable(0);

    expect(document.activeElement?.textContent).toBe("C02500");
  });

  it("draws the case after the last drawn one once it takes focus, before the table scrolls", () => {
    render(<Cases view={DEMO_VIEW} results={results} />);
    const lastDrawn = screen.getAllByRole("button", { name: /^C\d{5}$/ }).at(-1)!;
    const nextCase = `C${String(Number(lastDrawn.textContent!.slice(1)) + 1).padStart(5, "0")}`;

    act(() => lastDrawn.focus());

    expect(screen.queryByRole("button", { name: nextCase })).not.toBeNull();
  });

  it("draws the case before the first drawn one once it takes focus, before the table scrolls", () => {
    render(<Cases view={DEMO_VIEW} results={results} />);
    scrollTable(2500 * 52);
    const firstDrawn = screen.getAllByRole("button", { name: /^C\d{5}$/ })[0]!;
    const previousCase = `C${String(Number(firstDrawn.textContent!.slice(1)) - 1).padStart(5, "0")}`;

    act(() => firstDrawn.focus());

    expect(screen.queryByRole("button", { name: previousCase })).not.toBeNull();
  });

  it("keeps the case after a focused row drawn when the table scrolls far from it", () => {
    render(<Cases view={DEMO_VIEW} results={results} />);
    act(() => caseButton("C00000").focus());

    scrollTable(2500 * 52);

    expect(screen.queryByRole("button", { name: "C00001" })).not.toBeNull();
  });

  it("drops the row once focus moves out of the table", () => {
    render(<Cases view={DEMO_VIEW} results={results} />);
    act(() => caseButton("C00000").focus());
    act(() => screen.getByRole("searchbox", { name: "Search case ID" }).focus());

    scrollTable(2500 * 52);

    expect(screen.queryByRole("button", { name: "C00000" })).toBeNull();
  });

  it("still counts every case", () => {
    render(<Cases view={DEMO_VIEW} results={results} />);

    expect(countText()).toBe("Showing 5,000 of 5,000 cases.");
  });
});

// Stands in for the browser's ResizeObserver: it records what it observes and reports sizes
// only when a test asks, so a render loop shows up as extra observe calls or commits.
class FakeResizeObserver {
  static latest: FakeResizeObserver | null = null;
  observeCount = 0;
  private readonly targets = new Set<Element>();

  constructor(private readonly callback: ResizeObserverCallback) {
    FakeResizeObserver.latest = this;
  }

  observe(target: Element): void {
    this.observeCount += 1;
    this.targets.add(target);
  }

  unobserve(target: Element): void {
    this.targets.delete(target);
  }

  disconnect(): void {
    this.targets.clear();
  }

  report(heightPx: number): void {
    const entries = [...this.targets].map((target) => ({
      target,
      borderBoxSize: [{ blockSize: heightPx, inlineSize: 0 }],
      contentRect: { height: heightPx },
    }));
    this.callback(entries as unknown as ResizeObserverEntry[], this as unknown as ResizeObserver);
  }
}

function reportHeight(heightPx: number): void {
  act(() => FakeResizeObserver.latest!.report(heightPx));
}

describe("measuring an opened row", () => {
  const results = createResults([{ id: "A", analyst: TRUE_POSITIVE, agent: TRUE_POSITIVE }]);

  beforeEach(() => {
    vi.stubGlobal("ResizeObserver", FakeResizeObserver);
  });

  afterEach(() => {
    vi.unstubAllGlobals();
  });

  it("observes the detail once however often its height changes", async () => {
    render(<Cases view={DEMO_VIEW} results={results} />);
    await userEvent.click(caseButton("A"));

    reportHeight(300);
    reportHeight(310);
    reportHeight(320);

    expect(FakeResizeObserver.latest!.observeCount).toBe(1);
  });

  it("does not render again when the reported height is unchanged", async () => {
    let commits = 0;
    render(
      <Profiler id="cases" onRender={() => (commits += 1)}>
        <Cases view={DEMO_VIEW} results={results} />
      </Profiler>,
    );
    await userEvent.click(caseButton("A"));
    // React may render once more after the first same-value update before it bails out early.
    reportHeight(300);
    reportHeight(300);
    const commitsBefore = commits;

    reportHeight(300);
    reportHeight(300);

    expect(commits).toBe(commitsBefore);
  });
});

describe("verdicts", () => {
  const results = createResults([
    { id: "A", analyst: FALSE_POSITIVE, agent: BENIGN },
    { id: "B", analyst: TRUE_POSITIVE, agent: FALSE_POSITIVE },
    { id: "C", analyst: BENIGN, agent: UNKNOWN },
  ]);

  function rowOf(caseId: string) {
    return within(caseButton(caseId).closest("tr")!);
  }

  it("styles an analyst false positive as a false positive", () => {
    render(<Cases view={DEMO_VIEW} results={results} />);

    expect(rowOf("A").getByText("False positive").dataset.verdict).toBe("false_positive");
  });

  it("styles an agent benign verdict as benign", () => {
    render(<Cases view={DEMO_VIEW} results={results} />);

    expect(rowOf("A").getByText("Benign").dataset.verdict).toBe("benign");
  });

  it("styles an analyst true positive as a true positive", () => {
    render(<Cases view={DEMO_VIEW} results={results} />);

    expect(rowOf("B").getByText("True positive").dataset.verdict).toBe("true_positive");
  });

  it("styles a missing verdict as unknown", () => {
    render(<Cases view={DEMO_VIEW} results={results} />);

    expect(rowOf("C").getByText("(no verdict)").dataset.verdict).toBe("unknown");
  });

  it("marks a dangerous false close in words", () => {
    render(<Cases view={DEMO_VIEW} results={results} />);

    expect(rowOf("B").queryByText("Dangerous false close")).not.toBeNull();
  });

  it("marks a disagreement in words", () => {
    render(<Cases view={DEMO_VIEW} results={results} />);

    expect(rowOf("A").queryByText("Disagree")).not.toBeNull();
  });

  it("marks a case with a missing verdict as not compared", () => {
    render(<Cases view={DEMO_VIEW} results={results} />);

    expect(rowOf("C").queryByText("Not compared")).not.toBeNull();
  });
});

// The payloads of tests/test_dashboard_security.py, with the text each must show as.
const HOSTILE = [
  ["script", "</script><script>alert(1)</script>", "</script><script>alert(1)</script>"],
  ["img", "<img src=x onerror=alert(1)>", "<img src=x onerror=alert(1)>"],
  ["attribute", '" onmouseover="x', '" onmouseover="x'],
  ["separators", "a b c", "a\\u2028b\\u2029c"],
  ["control and bidi", "a\x1b[31m‮b\x07", "a\\x1b[31m\\u202eb\\x07"],
  ["entity", "&lt;script&gt;alert(1)&lt;/script&gt;", "&lt;script&gt;alert(1)&lt;/script&gt;"],
] as const;

describe.each(HOSTILE)("a hostile %s payload", (_, payload, shown) => {
  const results = createResults([{ id: `case${payload}`, analyst: TRUE_POSITIVE, agent: BENIGN }], {
    names: [`class${payload}`, `version${payload}`, `item${payload}`],
    details: [
      {
        case_id: `case${payload}`,
        calls: [{ tool: `tool${payload}`, status: "success", duration_ms: 5, arguments: `arg${payload}` }],
        outcomes: [{ item: `item${payload}`, status: "missed", reason: "not_called", failed_rule: null }],
      },
    ],
  });

  async function renderOpened(): Promise<void> {
    render(<Cases view={DEMO_VIEW} results={results} />);
    await userEvent.click(caseButton(`case${shown}`));
  }

  it("shows the case ID as text", async () => {
    await renderOpened();

    expect(caseButton(`case${shown}`).getAttribute("aria-expanded")).toBe("true");
  });

  it.each(["class", "version", "tool", "arg", "item"])("shows the %s as text", async (prefix) => {
    await renderOpened();

    expect(screen.queryByText(`${prefix}${shown}`)).not.toBeNull();
  });

  it("adds no element", async () => {
    await renderOpened();

    expect(document.querySelector("img, script")).toBeNull();
  });
});
