import { act, cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { App } from "./App";
import { Data } from "./pages/Data";
import { Help } from "./pages/Help";
import { POLL_MS } from "./poller";
import { registerHelpSection, registerPage, resetRegistryForTests } from "./registry";
import { startRouter } from "./router";
import { DEMO_RESULTS, DEMO_VIEW } from "./test-fixtures";

import type { PageProps } from "./registry";

const DATA = { view: DEMO_VIEW, results: DEMO_RESULTS };

function HomePage() {
  return <h1 tabIndex={-1}>Home</h1>;
}

function CasesPage({ results }: PageProps) {
  return <h1 tabIndex={-1}>{`${results.case_rows.columns.case_id.length} cases`}</h1>;
}

beforeEach(() => {
  resetRegistryForTests();
  registerPage({ path: "/", title: "Home", icon: "", order: 0, component: HomePage });
  registerPage({ path: "/cases", title: "Cases", icon: "", order: 1, component: CasesPage });
});

afterEach(() => {
  cleanup();
  vi.useRealTimers();
  vi.unstubAllGlobals();
  vi.restoreAllMocks();
  Reflect.deleteProperty(Element.prototype, "scrollIntoView");
  startRouter("offline");
  window.history.replaceState(null, "", "/");
});

it("names each registered page in the navigation", () => {
  render(<App data={DATA} />);

  expect(screen.getByRole("navigation", { name: "Pages" }).textContent).toBe("HomeCases");
});

it("renders the page the hash names, with the page data", () => {
  window.history.replaceState(null, "", "#/cases");

  render(<App data={DATA} />);

  expect(screen.getByRole("heading", { level: 1 }).textContent).toBe("201 cases");
});

it("renders the home page for an unknown hash", () => {
  window.history.replaceState(null, "", "#/nowhere");

  render(<App data={DATA} />);

  expect(screen.getByRole("heading", { level: 1 }).textContent).toBe("Home");
});

it("shows why the page data could not be read", () => {
  render(<App data={{ error: 'The page has no "dt-view" data block.' }} />);

  expect(screen.getByRole("alert").textContent).toContain('The page has no "dt-view" data block.');
});

it("shows no navigation when the page data could not be read", () => {
  render(<App data={{ error: "broken" }} />);

  expect(screen.queryByRole("navigation")).toBeNull();
});

function BrokenPage(): never {
  throw new Error("bad row");
}

it("shows an alert in place of a page that fails to render", () => {
  vi.spyOn(console, "error").mockImplementation(() => {});
  registerPage({ path: "/broken", title: "Broken", icon: "", order: 2, component: BrokenPage });
  window.history.replaceState(null, "", "#/broken");

  render(<App data={DATA} />);

  expect(screen.getByRole("alert").textContent).toContain("bad row");
});

it("keeps the navigation when a page fails to render", () => {
  vi.spyOn(console, "error").mockImplementation(() => {});
  registerPage({ path: "/broken", title: "Broken", icon: "", order: 2, component: BrokenPage });
  window.history.replaceState(null, "", "#/broken");

  render(<App data={DATA} />);

  expect(screen.getByRole("link", { name: "Cases" }).getAttribute("href")).toBe("#/cases");
});

it("titles the document after the page on first load", () => {
  window.history.replaceState(null, "", "#/cases");

  render(<App data={DATA} />);

  expect(document.title).toBe("Cases · DetectTrace");
});

it("leaves focus alone on first load", () => {
  window.history.replaceState(null, "", "#/cases");

  render(<App data={DATA} />);

  expect(document.activeElement).toBe(document.body);
});

it("titles the document after the page it navigates to", async () => {
  render(<App data={DATA} />);

  act(() => {
    window.location.hash = "#/cases";
  });

  await screen.findByText("201 cases");
  expect(document.title).toBe("Cases · DetectTrace");
});

it("moves focus to the new page's heading", async () => {
  render(<App data={DATA} />);

  act(() => {
    window.location.hash = "#/cases";
  });

  const heading = await screen.findByRole("heading", { name: "201 cases" });
  await waitFor(() => expect(document.activeElement).toBe(heading));
});

function HelpPage() {
  return (
    <>
      <h1 tabIndex={-1}>Help</h1>
      <h2 id="evidence-completeness" tabIndex={-1}>
        Evidence completeness
      </h2>
    </>
  );
}

function openHelpSection() {
  // jsdom lays nothing out, so it has no scrollIntoView.
  Element.prototype.scrollIntoView = () => {};
  registerPage({ path: "/help", title: "Help", icon: "", order: 2, component: HelpPage });
  render(<App data={DATA} />);
  act(() => {
    window.location.hash = "#/help#evidence-completeness";
  });
}

it("moves focus to the section a new page opens at", async () => {
  openHelpSection();

  const section = await screen.findByRole("heading", { name: "Evidence completeness" });
  await waitFor(() => expect(document.activeElement).toBe(section));
});

describe("on a new page opened at a section", () => {
  const focused: (string | null)[] = [];
  const onFocusIn = (event: FocusEvent) => focused.push((event.target as HTMLElement).textContent);

  beforeEach(() => {
    focused.length = 0;
    document.addEventListener("focusin", onFocusIn);
  });

  afterEach(() => {
    document.removeEventListener("focusin", onFocusIn);
  });

  it("never focuses the page's heading", async () => {
    openHelpSection();

    await screen.findByRole("heading", { name: "Evidence completeness" });
    expect(focused).toEqual(["Evidence completeness"]);
  });
});

// A heading with an id a link can name, as the verdict matrix's class headings have, but no
// tabIndex, so it can't take focus.
function VerdictsPage() {
  return (
    <>
      <h1 tabIndex={-1}>Verdicts</h1>
      <h2 id="verdicts-phishing">Phishing</h2>
    </>
  );
}

it("moves focus to the new page's heading when the section it opens at can't take focus", async () => {
  Element.prototype.scrollIntoView = () => {};
  registerPage({ path: "/verdicts", title: "Verdicts", icon: "", order: 2, component: VerdictsPage });
  render(<App data={DATA} />);

  act(() => {
    window.location.hash = "#/verdicts#verdicts-phishing";
  });

  const heading = await screen.findByRole("heading", { name: "Verdicts" });
  await waitFor(() => expect(document.activeElement).toBe(heading));
});

it("leaves focus alone when only the query changes", () => {
  window.history.replaceState(null, "", "#/cases");
  render(<App data={DATA} />);

  act(() => {
    window.history.replaceState(null, "", "#/cases?class=class-1");
    window.dispatchEvent(new HashChangeEvent("hashchange"));
  });

  expect(document.activeElement).toBe(document.body);
});

it("carries only the class parameter into the navigation links", () => {
  window.history.replaceState(null, "", "#/?class=class-1&dangerous=1&q=DT");

  render(<App data={DATA} />);

  expect(screen.getByRole("link", { name: "Cases" }).getAttribute("href")).toBe("#/cases?class=class-1");
});

const SERVED = { generation: 7, updated_at: "2026-10-05T12:00:00.000000Z", held_back_text: null };
const WAITING = {
  counts: [{ label: "Spans received", value: "1,200" }],
  notes: [{ message: "No verdict file yet.", hint: "Send verdicts to /v1/verdicts." }],
  next_step_text: null,
};
const WAITING_DATA = { view: { ...DEMO_VIEW, served: SERVED, waiting: WAITING }, waiting: WAITING };

it("shows what has arrived on a waiting page", () => {
  render(<App data={WAITING_DATA} />);

  expect(screen.getByRole("heading", { level: 1 }).textContent).toBe("Nothing to score yet");
});

it("shows no page whatever the hash names while waiting", () => {
  window.history.replaceState(null, "", "#/cases");

  render(<App data={WAITING_DATA} />);

  expect(screen.queryByText("201 cases")).toBeNull();
});

it("links to no page while waiting", () => {
  render(<App data={WAITING_DATA} />);

  expect(screen.queryAllByRole("link", { name: /Home|Cases/ })).toEqual([]);
});

it("titles the document as waiting while waiting", () => {
  render(<App data={WAITING_DATA} />);

  expect(document.title).toBe("Waiting for data · DetectTrace");
});

it("keeps the status bar on a waiting page", () => {
  render(<App data={WAITING_DATA} />);

  expect(screen.getByRole("status").getAttribute("aria-live")).toBe("polite");
});

const UI_WAITING = { ...WAITING, next_step_text: "Upload traces and verdicts on the Data page to get started." };
const UI_WAITING_DATA = {
  view: { ...DEMO_VIEW, mode: "ui" as const, served: SERVED, waiting: UI_WAITING },
  waiting: UI_WAITING,
};
const UI_STATE = {
  is_configured: false,
  can_configure: false,
  has_results: false,
  span_count_text: "No spans stored.",
  verdict_count_text: "No verdicts stored.",
  trace_family_text: null,
  checklist_classes: [],
  checklist_classes_text: "None yet",
  checklist_error_text: null,
};

function stubUiApp() {
  vi.stubGlobal(
    "fetch",
    vi.fn(() => Promise.resolve(new Response(JSON.stringify(UI_STATE)))),
  );
  registerPage({ path: "/data", title: "Data", icon: "", order: 2, component: Data });
}

it("shows the upload step on a ui waiting page's Data page", async () => {
  stubUiApp();
  window.history.replaceState(null, "", "#/data");

  render(<App data={UI_WAITING_DATA} />);

  expect(await screen.findByRole("heading", { name: "1 · Upload" })).not.toBeNull();
});

it("titles a ui waiting page's Data page after the page", async () => {
  stubUiApp();
  window.history.replaceState(null, "", "#/data");

  render(<App data={UI_WAITING_DATA} />);
  await screen.findByText("No spans stored.");

  expect(document.title).toBe("Data · DetectTrace");
});

it("links every page in the navigation on a ui waiting page", () => {
  stubUiApp();

  render(<App data={UI_WAITING_DATA} />);

  expect(screen.getByRole("navigation", { name: "Pages" }).textContent).toBe("HomeCasesData");
});

it("shows what has arrived on any other page of a ui waiting page", () => {
  stubUiApp();
  window.history.replaceState(null, "", "#/");

  render(<App data={UI_WAITING_DATA} />);

  expect(screen.getByRole("heading", { level: 1 }).textContent).toBe("Nothing to score yet");
});

it("links a ui waiting page's other pages to the Data page", () => {
  stubUiApp();
  window.history.replaceState(null, "", "#/cases");

  render(<App data={UI_WAITING_DATA} />);

  expect(
    screen.getByRole("link", { name: "Upload traces and verdicts on the Data page to get started." }).getAttribute("href"),
  ).toBe("#/data");
});

function registerHelp() {
  // jsdom lays nothing out, so it has no scrollIntoView.
  Element.prototype.scrollIntoView = () => {};
  registerPage({ path: "/help", title: "Help", icon: "", order: 3, component: Help });
  registerHelpSection({
    id: "evidence-completeness",
    title: "Evidence completeness",
    order: 0,
    body: () => <p>The share of checklist items satisfied.</p>,
  });
}

it("shows the Help page on a ui waiting page", () => {
  registerHelp();
  window.history.replaceState(null, "", "#/help");

  render(<App data={UI_WAITING_DATA} />);

  expect(screen.getByRole("heading", { level: 1 }).textContent).toBe("How DetectTrace works");
});

it("shows the Help page on a served waiting page", () => {
  registerHelp();
  window.history.replaceState(null, "", "#/help");

  render(<App data={WAITING_DATA} />);

  expect(screen.getByRole("heading", { level: 1 }).textContent).toBe("How DetectTrace works");
});

it("titles a waiting page's Help page after the page", () => {
  registerHelp();
  window.history.replaceState(null, "", "#/help");

  render(<App data={WAITING_DATA} />);

  expect(document.title).toBe("Help · DetectTrace");
});

it("links only the Help page on a served waiting page", () => {
  registerHelp();

  render(<App data={WAITING_DATA} />);

  expect(screen.getByRole("navigation", { name: "Pages" }).textContent).toBe("Help");
});

it("moves focus to the section a ui waiting page opens at", async () => {
  registerHelp();
  window.history.replaceState(null, "", "#/help#evidence-completeness");

  render(<App data={UI_WAITING_DATA} />);

  const section = screen.getByRole("heading", { name: "Evidence completeness" });
  await waitFor(() => expect(document.activeElement).toBe(section));
});

it("moves focus to the section a ui waiting page goes to", async () => {
  registerHelp();
  render(<App data={UI_WAITING_DATA} />);

  act(() => {
    window.location.hash = "#/help#evidence-completeness";
  });

  const section = await screen.findByRole("heading", { name: "Evidence completeness" });
  await waitFor(() => expect(document.activeElement).toBe(section));
});

it("shows how many cases are still settling on a served page", () => {
  const served = { ...SERVED, held_back_text: "3 cases still settling are not counted yet." };

  render(<App data={{ view: { ...DEMO_VIEW, served }, results: DEMO_RESULTS }} />);

  expect(screen.getByText("3 cases still settling are not counted yet.").tagName).toBe("P");
});

it("never asks the network on an offline page", async () => {
  vi.useFakeTimers();
  const fetchMock = vi.fn();
  vi.stubGlobal("fetch", fetchMock);
  render(<App data={DATA} />);

  await act(async () => {
    await vi.advanceTimersByTimeAsync(POLL_MS * 3);
  });

  expect(fetchMock).not.toHaveBeenCalled();
});

it("asks the server for its status on a served page", async () => {
  vi.useFakeTimers();
  const fetchMock = vi.fn(() => Promise.resolve(new Response(JSON.stringify({ generation: 7 }))));
  vi.stubGlobal("fetch", fetchMock);
  render(<App data={{ view: { ...DEMO_VIEW, served: SERVED }, results: DEMO_RESULTS }} />);

  await act(async () => {
    await vi.advanceTimersByTimeAsync(POLL_MS);
  });

  expect(fetchMock).toHaveBeenCalledOnce();
});

it("shows an alert, not a blank page, when the shell itself fails to render", () => {
  vi.spyOn(console, "error").mockImplementation(() => {});
  const brokenView = { ...DEMO_VIEW, header: undefined } as unknown as typeof DEMO_VIEW;

  render(<App data={{ view: brokenView, results: DEMO_RESULTS }} />);

  expect(screen.getByRole("alert").textContent).toMatch(/^The dashboard could not be shown: /);
});

it("opens a served page's sidebar link in place", async () => {
  startRouter("served");
  render(<App data={DATA} />);

  fireEvent.click(screen.getByRole("link", { name: "Cases" }));

  expect(await screen.findByRole("heading", { level: 1 })).toHaveProperty("textContent", "201 cases");
});

it("moves focus to the heading of the page a served page's link opens", async () => {
  startRouter("served");
  render(<App data={DATA} />);

  fireEvent.click(screen.getByRole("link", { name: "Cases" }));

  const heading = await screen.findByRole("heading", { name: "201 cases" });
  await waitFor(() => expect(document.activeElement).toBe(heading));
});

it("shows the earlier page when a served page goes back", async () => {
  startRouter("served");
  render(<App data={DATA} />);
  fireEvent.click(screen.getByRole("link", { name: "Cases" }));
  await screen.findByRole("heading", { name: "201 cases" });

  act(() => {
    window.history.replaceState(null, "", "/");
    window.dispatchEvent(new PopStateEvent("popstate"));
  });

  expect(await screen.findByRole("heading", { name: "Home" })).not.toBeNull();
});
