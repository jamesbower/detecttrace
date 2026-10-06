import { cleanup, render, screen } from "@testing-library/react";
import { afterEach, expect, it } from "vitest";

import edgeView from "../../../tests/fixtures/edge/versions/more_than_six/expected-view.json";
import { DEMO_VIEW } from "../test-fixtures";
import { Heatmap } from "./Heatmap";

import type { SkipTableView, View } from "../view";

const DEMO_TABLE = DEMO_VIEW.classes[0]!.skipped;
const EDGE_TABLE = (edgeView as unknown as View).classes[0]!.skipped;
const LONG_LABEL = "v".repeat(200);
const LONG_TABLE: SkipTableView = {
  ...DEMO_TABLE,
  columns: DEMO_TABLE.columns.map((column, index) => (index === 0 ? { ...column, label: LONG_LABEL } : column)),
};

afterEach(cleanup);

it("shows a cell's share skipped as text", () => {
  render(<Heatmap table={DEMO_TABLE} alertClassName="impossible_travel" />);

  expect(screen.queryByText("73%")).not.toBeNull();
});

it("shows a cell's count as text", () => {
  render(<Heatmap table={DEMO_TABLE} alertClassName="impossible_travel" />);

  expect(screen.queryByText("25 of 70")).not.toBeNull();
});

it("names a row by its checklist step", () => {
  render(<Heatmap table={DEMO_TABLE} alertClassName="impossible_travel" />);

  expect(screen.getAllByRole("rowheader").map((header) => header.textContent)).toEqual([
    "signin_history",
    "signin_query",
    "mfa_check",
    "ip_reputation",
    "location_history",
  ]);
});

it("names the table after the class", () => {
  render(<Heatmap table={DEMO_TABLE} alertClassName="impossible_travel" />);

  expect(
    screen.queryByRole("table", { name: "impossible_travel: skipped-step rate per version, side by side." }),
  ).not.toBeNull();
});

it("tints a cell by its share skipped", () => {
  render(<Heatmap table={DEMO_TABLE} alertClassName="impossible_travel" />);

  expect(screen.getByRole("cell", { name: "73% 51 of 70" }).style.getPropertyValue("--heat-level")).toBe("72.9");
});

it("says a class has no checklist", () => {
  render(<Heatmap table={EDGE_TABLE} alertClassName="impossible_travel" />);

  expect(screen.queryByText("No checklist for this class.")).not.toBeNull();
});

it("shows no table for a class without a checklist", () => {
  render(<Heatmap table={EDGE_TABLE} alertClassName="impossible_travel" />);

  expect(screen.queryByRole("table")).toBeNull();
});

it("shows a 200-character version label in full", () => {
  render(<Heatmap table={LONG_TABLE} alertClassName="impossible_travel" />);

  expect(screen.queryByText(LONG_LABEL)).not.toBeNull();
});

it("keys each version column with its series swatch", () => {
  render(<Heatmap table={DEMO_TABLE} alertClassName="impossible_travel" />);

  expect(screen.getByRole("columnheader", { name: /^v2/ }).querySelector(".series-swatch .series-2")).not.toBeNull();
});
