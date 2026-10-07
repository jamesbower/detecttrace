import { cleanup, render, screen } from "@testing-library/react";
import { afterEach, expect, it, vi } from "vitest";

import { App } from "../App";
import { DEMO_VIEW } from "../test-fixtures";
// The registry is module state: this relies on Vitest isolating modules per test file.
import "./index";

import type { View } from "../view";

// The built-in pages as `./index` registers them, on a waiting page in each mode.
const WAITING = { counts: [], notes: [], next_step_text: null };
const SERVED = { generation: 1, updated_at: "2026-10-05T12:00:00.000000Z", held_back_text: null };
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

function renderWaitingPage(mode: View["mode"], hash: string) {
  vi.stubGlobal(
    "fetch",
    vi.fn(() => Promise.resolve(new Response(JSON.stringify(UI_STATE)))),
  );
  window.history.replaceState(null, "", hash);
  const served = mode === "offline" ? null : SERVED;
  render(<App data={{ view: { ...DEMO_VIEW, mode, served, waiting: WAITING }, waiting: WAITING }} />);
}

afterEach(() => {
  cleanup();
  vi.unstubAllGlobals();
  window.history.replaceState(null, "", "/");
});

it("shows the Help page on an offline waiting page", () => {
  renderWaitingPage("offline", "#/help");

  expect(screen.getByRole("heading", { level: 1 }).textContent).toBe("How DetectTrace works");
});

it("shows the Help page on a served waiting page", () => {
  renderWaitingPage("served", "#/help");

  expect(screen.getByRole("heading", { level: 1 }).textContent).toBe("How DetectTrace works");
});

it("shows the Help page on a ui waiting page", () => {
  renderWaitingPage("ui", "#/help");

  expect(screen.getByRole("heading", { level: 1 }).textContent).toBe("How DetectTrace works");
});

it("shows the upload step on a ui waiting page's Data page", async () => {
  renderWaitingPage("ui", "#/data");

  expect(await screen.findByRole("heading", { name: "1 · Upload" })).not.toBeNull();
});

it("shows what has arrived in place of the Data page on a served waiting page", () => {
  renderWaitingPage("served", "#/data");

  expect(screen.getByRole("heading", { level: 1 }).textContent).toBe("Nothing to score yet");
});

it("shows what has arrived in place of the Data page on an offline waiting page", () => {
  renderWaitingPage("offline", "#/data");

  expect(screen.getByRole("heading", { level: 1 }).textContent).toBe("Nothing to score yet");
});
