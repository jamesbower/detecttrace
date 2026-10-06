import { cleanup, render, screen, within } from "@testing-library/react";
import { afterEach, expect, it } from "vitest";

import edgeView from "../../../tests/fixtures/edge/versions/more_than_six/expected-view.json";
import { registerPanel, resetRegistryForTests } from "../registry";
import { DEMO_RESULTS, DEMO_VIEW } from "../test-fixtures";
import { VersionTable } from "./VersionTable";

import type { ClassView, View } from "../view";

const EDGE_VIEW = edgeView as unknown as View;
const DEMO_CLASS = DEMO_VIEW.classes[0]!;
const EDGE_CLASS = EDGE_VIEW.classes[0]!;
const LONG_LABEL = "v".repeat(200);
// Arabic text, a control character the Python side made visible, and a live right-to-left override.
const RTL_NAME = "تسجيل_الدخول \\u202e reversed‮ class";

afterEach(() => {
  cleanup();
  resetRegistryForTests();
});

function renderTable(alertClass: ClassView) {
  return render(<VersionTable alertClass={alertClass} view={DEMO_VIEW} results={DEMO_RESULTS} />);
}

function createLongClass(): ClassView {
  const [allRow, firstRow, ...rest] = DEMO_CLASS.rows;
  return { ...DEMO_CLASS, name: RTL_NAME, rows: [allRow!, { ...firstRow!, label: LONG_LABEL }, ...rest] };
}

function rowFor(label: string): HTMLElement {
  return screen.getByRole("rowheader", { name: new RegExp(`^${label}`) }).closest("tr")!;
}

it("names the table by the class in its caption", () => {
  renderTable(DEMO_CLASS);

  expect(screen.queryByRole("table", { name: /^impossible_travel: metrics per version/ })).not.toBeNull();
});

it("shows one row per version row in the view", () => {
  renderTable(DEMO_CLASS);

  expect(screen.getAllByRole("rowheader").map((header) => header.textContent)).toEqual(["All versions", "v1", "v2"]);
});

it("shows a metric's value, interval and n exactly as the view writes them", () => {
  renderTable(DEMO_CLASS);

  expect(within(rowFor("All versions")).getAllByRole("cell")[1]!.textContent).toBe("81%78–85%n = 106");
});

it("shows kappa exactly as the view writes it", () => {
  renderTable(DEMO_CLASS);

  expect(within(rowFor("v1")).getAllByRole("cell")[3]!.textContent).toBe("0.820.64–0.96n = 36");
});

it("marks a dangerous count with an icon as well as the number", () => {
  renderTable(DEMO_CLASS);

  expect(within(rowFor("v2")).getAllByRole("cell")[4]!.querySelector("svg")).not.toBeNull();
});

it("shows a zero dangerous count without the icon", () => {
  renderTable(DEMO_CLASS);

  expect(within(rowFor("v1")).getAllByRole("cell")[4]!.querySelector("svg")).toBeNull();
});

it("shows the view's note on true positives with no agent verdict", () => {
  const text = "2 true positives with no agent verdict: DT-1, DT-2";
  const [allRow, ...rest] = DEMO_CLASS.rows;
  renderTable({ ...DEMO_CLASS, rows: [{ ...allRow!, tp_without_agent_text: text }, ...rest] });

  expect(within(rowFor("All versions")).queryByText(text)).not.toBeNull();
});

it("renders every row of a class with more than six versions", () => {
  renderTable(EDGE_CLASS);

  expect(screen.getAllByRole("rowheader")).toHaveLength(8);
});

it("renders the pooled other-versions row", () => {
  renderTable(EDGE_CLASS);

  expect(screen.queryByRole("rowheader", { name: /^\(other versions: v2, v5\)/ })).not.toBeNull();
});

it("explains which versions were pooled", () => {
  renderTable(EDGE_CLASS);

  expect(screen.queryByText(EDGE_CLASS.pooled_note!)).not.toBeNull();
});

it("marks a row with few cases at the version", () => {
  renderTable(EDGE_CLASS);

  expect(screen.getByRole("rowheader", { name: /^v1/ }).textContent).toBe("v1Few cases.");
});

it("does not repeat a few-cases row's mark in its metric cells", () => {
  renderTable(EDGE_CLASS);

  expect(within(rowFor("v1")).getAllByText("Few cases.")).toHaveLength(1);
});

it("shows why a metric has no value", () => {
  renderTable(EDGE_CLASS);

  expect(within(rowFor("v1")).getAllByText("No checklist for this class.")).toHaveLength(1);
});

it("shows a 200-character version label in full", () => {
  renderTable(createLongClass());

  expect(screen.queryByRole("rowheader", { name: LONG_LABEL })).not.toBeNull();
});

it("keeps one row per version with a long label and a right-to-left class name", () => {
  renderTable(createLongClass());

  expect(screen.getAllByRole("row")).toHaveLength(DEMO_CLASS.rows.length + 1);
});

it("shows a right-to-left class name as written", () => {
  renderTable(createLongClass());

  expect(screen.getByRole("heading", { level: 2 }).textContent).toContain(RTL_NAME);
});

it("adds a detail row under each version when a panel is registered", () => {
  registerPanel("version-row-detail", () => <p>Row detail</p>);
  renderTable(DEMO_CLASS);

  expect(screen.getAllByText("Row detail")).toHaveLength(DEMO_CLASS.rows.length);
});

it("names the heading by the class, its cases and its versions", () => {
  renderTable(DEMO_CLASS);

  expect(screen.queryByRole("heading", { name: "impossible_travel 106 cases · v1, v2" })).not.toBeNull();
});

it("tells each version row's panel its class and version", () => {
  registerPanel("version-row-detail", ({ classAnchor, versionLabel }) => <p>{`Panel: ${classAnchor} ${versionLabel}`}</p>);
  renderTable(DEMO_CLASS);

  expect(screen.getAllByText(/^Panel: /).map((panel) => panel.textContent)).toEqual([
    "Panel: class-0 All versions",
    "Panel: class-0 v1",
    "Panel: class-0 v2",
  ]);
});
