import { cleanup, render, screen } from "@testing-library/react";
import { afterEach, expect, it } from "vitest";

import { DEMO_VIEW } from "../test-fixtures";
import { SWATCH_VIEW_BOX } from "./SeriesSwatch";
import { TrendLegend } from "./TrendLegend";

const LINES = DEMO_VIEW.classes[0]!.trend.agreement.lines;

afterEach(cleanup);

function renderLegend() {
  render(<TrendLegend lines={LINES} fewLegendText="Hollow marker: fewer than 10 cases" alertClassName="impossible_travel" />);
}

function swatchOf(label: string): Element | null | undefined {
  return screen.getByText(label).closest("li")?.querySelector("svg");
}

it("keys the few-case marker with a hollow circle", () => {
  renderLegend();

  expect(swatchOf("Hollow marker: fewer than 10 cases")?.querySelector("circle.series-marker.is-few")).not.toBeNull();
});

it("draws the version rule taller than its swatch box", () => {
  renderLegend();
  const rule = swatchOf("First week of a version")?.querySelector("line");

  expect(Number(rule?.getAttribute("y2")) - Number(rule?.getAttribute("y1"))).toBeGreaterThan(SWATCH_VIEW_BOX.height);
});
