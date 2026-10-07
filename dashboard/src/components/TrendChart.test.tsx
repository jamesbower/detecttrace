import { cleanup, render, screen, within } from "@testing-library/react";
import { userEvent } from "@testing-library/user-event";
import { afterEach, expect, it, vi } from "vitest";

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

afterEach(() => {
  cleanup();
  vi.restoreAllMocks();
});

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

it("names the chart once, by its figure's caption", () => {
  const { container } = renderDemo();
  const captionId = container.querySelector("figcaption")?.id ?? "";

  expect(
    [...container.querySelectorAll(`[aria-labelledby="${captionId}"]`)].map((element) => element.tagName),
  ).toEqual(["FIGURE"]);
});

it("gives the chart's drawing no group of its own", () => {
  const { container } = renderDemo();

  expect(container.querySelector("svg")?.hasAttribute("role")).toBe(false);
});

it("leaves a chart that fits out of the tab order and out of the landmarks", () => {
  renderDemo();

  expect(screen.queryByRole("region")).toBeNull();
});

it("makes a chart wider than its area a tab stop named for scrolling", () => {
  vi.spyOn(Element.prototype, "scrollWidth", "get").mockReturnValue(640);
  vi.spyOn(Element.prototype, "clientWidth", "get").mockReturnValue(300);

  renderDemo();

  expect(screen.getByRole("region", { name: "Chart, scrolls sideways" }).getAttribute("tabindex")).toBe("0");
});

it("moves Tab to the first point, named by week, series and value", async () => {
  renderDemo();

  await userEvent.tab();

  expect(document.activeElement).toBe(screen.getByRole("img", { name: "2026-W32, All versions: 94% (n 17)" }));
});

function focusedName(): string | null | undefined {
  return document.activeElement?.getAttribute("aria-label");
}

it("makes the chart one tab stop", () => {
  renderDemo();

  expect(screen.getAllByRole("img").filter((point) => point.tabIndex === 0)).toHaveLength(1);
});

it("leaves the chart on the second Tab", async () => {
  renderDemo();

  await userEvent.tab();
  await userEvent.tab();

  expect(document.activeElement).toBe(screen.getByRole("button", { name: /^Show table/ }));
});

it("moves ArrowRight to the series' next week", async () => {
  renderDemo();
  await userEvent.tab();

  await userEvent.keyboard("{ArrowRight}");

  expect(focusedName()).toBe("2026-W33, All versions: 92% (n 19)");
});

it("moves ArrowLeft back to the series' previous week", async () => {
  renderDemo();
  await userEvent.tab();
  await userEvent.keyboard("{End}");

  await userEvent.keyboard("{ArrowLeft}");

  expect(focusedName()).toBe("2026-W36, All versions: 79% (n 20)");
});

it("moves End to the series' last week", async () => {
  renderDemo();
  await userEvent.tab();

  await userEvent.keyboard("{End}");

  expect(focusedName()).toBe("2026-W37, All versions: 73% (n 14)");
});

it("moves ArrowDown to the next series in the same week", async () => {
  renderDemo();
  await userEvent.tab();

  await userEvent.keyboard("{ArrowDown}");

  expect(focusedName()).toBe("2026-W32, v1: 94% (n 17)");
});

it("moves ArrowDown to the next series' nearest week when it has none that week", async () => {
  renderDemo();
  await userEvent.tab();
  await userEvent.keyboard("{ArrowDown}{ArrowRight}");

  await userEvent.keyboard("{ArrowDown}");

  expect(focusedName()).toBe("2026-W34, v2: 74% (n 18)");
});

it("moves ArrowUp to the previous series' nearest week", async () => {
  renderDemo();
  await userEvent.tab();
  await userEvent.keyboard("{ArrowDown}{ArrowDown}");

  await userEvent.keyboard("{ArrowUp}");

  expect(focusedName()).toBe("2026-W33, v1: 92% (n 19)");
});

it("returns Tab to the last point moved to", async () => {
  renderDemo();
  await userEvent.tab();
  await userEvent.keyboard("{ArrowRight}");
  await userEvent.tab();

  await userEvent.tab({ shift: true });

  expect(focusedName()).toBe("2026-W33, All versions: 92% (n 19)");
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

  expect(container.querySelectorAll(".series-marker.is-few")).toHaveLength(8);
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

// All versions has a point only in the middle week; v1 only in the weeks either side of it.
const TIE_METRIC = {
  title: "Verdict agreement",
  lines: [
    { style: "all", label: "All versions", values: [null, 0.5, null], counts: [0, 20, 0], few: [false, false, false] },
    { style: "1", label: "v1", values: [0.4, null, 0.6], counts: [20, 0, 20], few: [false, false, false] },
  ],
  table_rows: [
    { week: "2026-W01", cells: ["no cases", "40% (n 20)"] },
    { week: "2026-W02", cells: ["50% (n 20)", "no cases"] },
    { week: "2026-W03", cells: ["no cases", "60% (n 20)"] },
  ],
  empty_text: null,
};
const TIE_TREND: TrendView = {
  ...DEMO_TREND,
  weeks: ["2026-W01", "2026-W02", "2026-W03"],
  agreement: TIE_METRIC,
  version_first_weeks: { v1: "2026-W01" },
};

it("breaks an ArrowDown tie between two nearest weeks toward the earlier one", async () => {
  render(<TrendChart metric={TIE_METRIC} trend={TIE_TREND} alertClassName="impossible_travel" />);
  await userEvent.tab();

  await userEvent.keyboard("{ArrowDown}");

  expect(focusedName()).toBe("2026-W01, v1: 40% (n 20)");
});

it("keeps one tab stop, on the first point, when the point moved to is gone", async () => {
  const { rerender } = renderDemo();
  await userEvent.tab();
  await userEvent.keyboard("{End}");

  rerender(<TrendChart metric={TIE_METRIC} trend={TIE_TREND} alertClassName="impossible_travel" />);

  expect(screen.getAllByRole("img").filter((point) => point.tabIndex === 0).map((point) => point.getAttribute("aria-label"))).toEqual([
    "2026-W02, All versions: 50% (n 20)",
  ]);
});

it("explains the measure in the caption when given its term", () => {
  render(
    <TrendChart metric={DEMO_TREND.completeness} trend={DEMO_TREND} alertClassName="impossible_travel" term="completeness" />,
  );

  expect(within(screen.getByRole("figure")).queryByRole("button", { name: "Evidence completeness" })).not.toBeNull();
});

it("keeps the caption's name when it explains the measure", () => {
  render(
    <TrendChart metric={DEMO_TREND.completeness} trend={DEMO_TREND} alertClassName="impossible_travel" term="completeness" />,
  );

  expect(screen.queryByRole("figure", { name: "Evidence completeness per week" })).not.toBeNull();
});

it("explains the measure in an empty chart's caption when given its term", () => {
  render(
    <TrendChart metric={EDGE_TREND.completeness} trend={EDGE_TREND} alertClassName="impossible_travel" term="completeness" />,
  );

  expect(within(screen.getByRole("figure")).queryByRole("button", { name: "Evidence completeness" })).not.toBeNull();
});

it("keeps a plain caption without a term", () => {
  renderDemo();

  expect(within(screen.getByRole("figure")).queryByRole("button", { name: "Evidence completeness" })).toBeNull();
});
