import { cleanup, render, screen, within } from "@testing-library/react";
import { afterEach, beforeEach, expect, it } from "vitest";

import edgeView from "../../../tests/fixtures/edge/versions/more_than_six/expected-view.json";
import { registerPanel, resetRegistryForTests } from "../registry";
import { DEMO_RESULTS, DEMO_VIEW } from "../test-fixtures";
import { WeeklyTrend } from "./WeeklyTrend";

import type { View } from "../view";

const EDGE_VIEW = edgeView as unknown as View;
const LONG_LABEL = "v".repeat(200);

function FooterPanel() {
  return <p>Footer panel</p>;
}

// The demo's first class, with v1 renamed to a 200-character label in every place it shows.
function createLongLabelView(): View {
  const base = DEMO_VIEW.classes[0]!;
  const rename = <T extends { label: string }>(line: T): T => (line.label === "v1" ? { ...line, label: LONG_LABEL } : line);
  const trend = {
    ...base.trend,
    completeness: { ...base.trend.completeness, lines: base.trend.completeness.lines.map(rename) },
    agreement: { ...base.trend.agreement, lines: base.trend.agreement.lines.map(rename) },
    version_first_weeks: { [LONG_LABEL]: "2026-W32", v2: "2026-W34" },
  };
  return { ...DEMO_VIEW, classes: [{ ...base, trend }] };
}

beforeEach(() => {
  resetRegistryForTests();
});

afterEach(() => {
  cleanup();
  window.history.replaceState(null, "", "#");
});

it("labels the panel by the selected class's tab", () => {
  render(<WeeklyTrend view={DEMO_VIEW} results={DEMO_RESULTS} />);

  expect(screen.queryByRole("tabpanel", { name: "impossible_travel" })).not.toBeNull();
});

it("draws one chart per metric", () => {
  render(<WeeklyTrend view={DEMO_VIEW} results={DEMO_RESULTS} />);

  expect(screen.getAllByRole("figure").map((figure) => figure.getAttribute("aria-labelledby") !== null)).toEqual([
    true,
    true,
  ]);
});

it("names both charts by their metric", () => {
  render(<WeeklyTrend view={DEMO_VIEW} results={DEMO_RESULTS} />);

  expect(screen.getAllByRole("figure").map((figure) => figure.querySelector("figcaption")?.textContent)).toEqual([
    "Evidence completeness per week",
    "Verdict agreement per week",
  ]);
});

it("keys every series of eight versions in the legend", () => {
  render(<WeeklyTrend view={EDGE_VIEW} results={DEMO_RESULTS} />);

  const legend = screen.getByRole("list", { name: "Legend for impossible_travel" });
  expect(within(legend).getAllByRole("listitem").map((item) => item.textContent)).toEqual([
    "All versions",
    "v1",
    "v3",
    "v4",
    "v6",
    "v7",
    "v8",
    "(other versions: v2, v5)",
    "First week of a version",
    "Hollow marker: fewer than 10 cases",
  ]);
});

it("charts agreement for a class without a checklist", () => {
  render(<WeeklyTrend view={EDGE_VIEW} results={DEMO_RESULTS} />);

  expect(screen.queryByRole("figure", { name: "Verdict agreement per week" })?.querySelector("svg")).not.toBeNull();
});

it("shows a 200-character version label in the legend", () => {
  render(<WeeklyTrend view={createLongLabelView()} results={DEMO_RESULTS} />);

  const legend = screen.getByRole("list", { name: "Legend for impossible_travel" });
  expect(within(legend).queryByText(LONG_LABEL)).not.toBeNull();
});

it("renders the panels registered for the trend footer", () => {
  registerPanel("trend-footer", FooterPanel);

  render(<WeeklyTrend view={DEMO_VIEW} results={DEMO_RESULTS} />);

  expect(screen.queryByText("Footer panel")).not.toBeNull();
});
