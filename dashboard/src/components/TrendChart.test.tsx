import { cleanup, render, screen } from "@testing-library/react";
import { userEvent } from "@testing-library/user-event";
import { afterEach, expect, it } from "vitest";

import edgeView from "../../../tests/fixtures/edge/versions/more_than_six/expected-view.json";
import { DEMO_VIEW } from "../test-fixtures";
import { TrendChart } from "./TrendChart";

import type { TrendView, View } from "../view";

const DEMO_TREND = DEMO_VIEW.classes[0]!.trend;
const EDGE_TREND = (edgeView as unknown as View).classes[0]!.trend;
const LONG_LABEL = "v".repeat(200);
const LONG_TREND: TrendView = {
  ...DEMO_TREND,
  completeness: {
    ...DEMO_TREND.completeness,
    lines: DEMO_TREND.completeness.lines.map((line) => (line.label === "v1" ? { ...line, label: LONG_LABEL } : line)),
  },
  version_first_weeks: { [LONG_LABEL]: "2026-W32", v2: "2026-W34" },
};

afterEach(cleanup);

function renderDemo() {
  return render(<TrendChart metric={DEMO_TREND.completeness} trend={DEMO_TREND} alertClassName="impossible_travel" />);
}

function renderEdge() {
  return render(<TrendChart metric={EDGE_TREND.agreement} trend={EDGE_TREND} alertClassName="impossible_travel" />);
}

it("captions the chart with the metric", () => {
  renderDemo();

  expect(screen.queryByRole("figure", { name: "Evidence completeness per week" })).not.toBeNull();
});

it("moves Tab to the first point, named by week, series and value", async () => {
  renderDemo();

  await userEvent.tab();

  expect(document.activeElement).toBe(screen.getByRole("img", { name: "2026-W32, All versions: 94% (n 17)" }));
});

it("reaches a version's point by Tab", async () => {
  renderDemo();

  // Six all-versions weeks come first, then v1's two weeks.
  await userEvent.tab();
  await userEvent.tab();
  await userEvent.tab();
  await userEvent.tab();
  await userEvent.tab();
  await userEvent.tab();
  await userEvent.tab();

  expect(document.activeElement?.getAttribute("aria-label")).toBe("2026-W32, v1: 94% (n 17)");
});

it("gives every week with a value a focusable point", () => {
  renderDemo();

  expect(screen.getAllByRole("img").filter((point) => point.tabIndex === 0)).toHaveLength(12);
});

it("shows the focused point's name beside the chart", async () => {
  renderDemo();

  await userEvent.tab();

  expect(screen.queryByText("2026-W32, All versions: 94% (n 17)")).not.toBeNull();
});

it("marks each version's first week", () => {
  renderDemo();

  expect(screen.getAllByText("v2", { selector: "text" })).toHaveLength(1);
});

it("hides the table until asked", () => {
  renderDemo();

  expect(screen.queryByRole("table")).toBeNull();
});

it("collapses the table toggle at first", () => {
  renderDemo();

  expect(screen.getByRole("button", { name: /^Show table/ }).getAttribute("aria-expanded")).toBe("false");
});

it("expands the toggle when pressed", async () => {
  renderDemo();

  await userEvent.click(screen.getByRole("button", { name: /^Show table/ }));

  expect(screen.getByRole("button", { name: /^Hide table/ }).getAttribute("aria-expanded")).toBe("true");
});

it("shows the table with the view's cell text when the toggle is pressed", async () => {
  renderDemo();

  await userEvent.click(screen.getByRole("button", { name: /^Show table/ }));

  expect(screen.getAllByRole("cell", { name: "92% (n 19)" })).toHaveLength(2);
});

it("names the table by class, metric and period", async () => {
  renderDemo();

  await userEvent.click(screen.getByRole("button", { name: /^Show table/ }));

  expect(
    screen.queryByRole("table", { name: "impossible_travel: Evidence completeness per week, 2026-W32 to 2026-W37" }),
  ).not.toBeNull();
});

it("hides the table again on a second press", async () => {
  renderDemo();
  await userEvent.click(screen.getByRole("button", { name: /^Show table/ }));

  await userEvent.click(screen.getByRole("button", { name: /^Hide table/ }));

  expect(screen.queryByRole("table")).toBeNull();
});

it("says why a metric has no chart", () => {
  render(<TrendChart metric={EDGE_TREND.completeness} trend={EDGE_TREND} alertClassName="impossible_travel" />);

  expect(screen.queryByText("No checklist for this class.")).not.toBeNull();
});

it("names a few-case point's value as the view gives it", () => {
  renderEdge();

  expect(screen.queryByRole("img", { name: "2026-W32, v1: 100% (n 3) Few cases." })).not.toBeNull();
});

it("draws a hollow marker for each version week with few cases", () => {
  const { container } = renderEdge();

  expect(container.querySelectorAll(".trend-marker.is-few")).toHaveLength(8);
});

it("draws the other-versions series", () => {
  renderEdge();

  expect(screen.getAllByRole("img", { name: /^2026-W\d+, \(other versions: v2, v5\): / })).toHaveLength(2);
});

it("draws a point for a 200-character version label", () => {
  render(<TrendChart metric={LONG_TREND.completeness} trend={LONG_TREND} alertClassName="impossible_travel" />);

  expect(screen.queryByRole("img", { name: `2026-W32, ${LONG_LABEL}: 94% (n 17)` })).not.toBeNull();
});

it("marks the first week of a version with a 200-character label", () => {
  render(<TrendChart metric={LONG_TREND.completeness} trend={LONG_TREND} alertClassName="impossible_travel" />);

  expect(screen.getAllByText(LONG_LABEL, { selector: "text" })).toHaveLength(1);
});
