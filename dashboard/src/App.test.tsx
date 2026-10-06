import { act, cleanup, render, screen, waitFor } from "@testing-library/react";
import { afterEach, beforeEach, expect, it, vi } from "vitest";

import { App } from "./App";
import { POLL_MS } from "./poller";
import { registerPage, resetRegistryForTests } from "./registry";
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
  window.history.replaceState(null, "", "#");
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
