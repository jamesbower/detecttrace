import { cleanup, render, screen } from "@testing-library/react";
import { afterEach, beforeEach, expect, it, vi } from "vitest";

import { registerPanel, resetRegistryForTests } from "../registry";
import { DEMO_RESULTS, DEMO_VIEW } from "../test-fixtures";
import { PanelSlot } from "./PanelSlot";

function BrokenPanel(): never {
  throw new Error("bad panel");
}

function WorkingPanel() {
  return <p>Working panel</p>;
}

beforeEach(() => {
  resetRegistryForTests();
  vi.spyOn(console, "error").mockImplementation(() => {});
  registerPanel("trend-footer", BrokenPanel);
  registerPanel("trend-footer", WorkingPanel);
});

afterEach(() => {
  cleanup();
  vi.restoreAllMocks();
});

it("shows an alert in place of a panel that fails to render", () => {
  render(<PanelSlot name="trend-footer" view={DEMO_VIEW} results={DEMO_RESULTS} />);

  expect(screen.getByRole("alert").textContent).toContain("bad panel");
});

it("still renders the slot's other panels", () => {
  render(<PanelSlot name="trend-footer" view={DEMO_VIEW} results={DEMO_RESULTS} />);

  expect(screen.getByText("Working panel").tagName).toBe("P");
});
