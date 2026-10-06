import { cleanup, render, screen } from "@testing-library/react";
import { afterEach, expect, it, vi } from "vitest";

import { DEMO_VIEW } from "../test-fixtures";
import { Matrix } from "./Matrix";

const CONFUSION = DEMO_VIEW.classes[0]!.confusion;

afterEach(() => {
  cleanup();
  vi.restoreAllMocks();
});

it("heads the columns with the agent verdicts", () => {
  render(<Matrix confusion={CONFUSION} alertClassName="impossible_travel" />);

  expect(screen.getAllByRole("columnheader").map((header) => header.textContent)).toEqual([
    "Analyst verdict by agent verdict",
    "Agent: true positive",
    "Agent: false positive",
    "Agent: benign",
  ]);
});

it("heads the rows with the analyst verdicts", () => {
  render(<Matrix confusion={CONFUSION} alertClassName="impossible_travel" />);

  expect(screen.getAllByRole("rowheader").map((header) => header.textContent)).toEqual([
    "Analyst: true positive",
    "Analyst: false positive",
    "Analyst: benign",
  ]);
});

it("shows each cell's count", () => {
  render(<Matrix confusion={CONFUSION} alertClassName="impossible_travel" />);

  expect(screen.getAllByRole("cell").map((cell) => cell.textContent)).toEqual([
    "11",
    "0 Dangerous",
    "3 Dangerous",
    "1",
    "37",
    "3",
    "0",
    "4",
    "47",
  ]);
});

it("labels each dangerous cell in text", () => {
  render(<Matrix confusion={CONFUSION} alertClassName="impossible_travel" />);

  expect(screen.getAllByText("Dangerous")).toHaveLength(2);
});

it("marks a dangerous cell that holds cases as flagged", () => {
  render(<Matrix confusion={CONFUSION} alertClassName="impossible_travel" />);

  expect(screen.getByRole("cell", { name: "3 Dangerous" }).classList.contains("is-flagged")).toBe(true);
});

it("leaves an empty dangerous cell unflagged", () => {
  render(<Matrix confusion={CONFUSION} alertClassName="impossible_travel" />);

  expect(screen.getByRole("cell", { name: "0 Dangerous" }).classList.contains("is-flagged")).toBe(false);
});

it("colors a cell from its heat class", () => {
  render(<Matrix confusion={CONFUSION} alertClassName="impossible_travel" />);

  expect(screen.getByRole("cell", { name: "37" }).classList.contains("matrix-h-4")).toBe(true);
});

it("marks the cells where agent and analyst agree", () => {
  const { container } = render(<Matrix confusion={CONFUSION} alertClassName="impossible_travel" />);

  expect([...container.querySelectorAll(".is-match")].map((cell) => cell.textContent)).toEqual(["11", "37", "47"]);
});

it("names the table by its caption, which names the class", () => {
  render(<Matrix confusion={CONFUSION} alertClassName="impossible_travel" />);

  expect(
    screen.queryByRole("table", {
      name: "impossible_travel: rows are the analyst verdict, columns the agent verdict.",
    }),
  ).not.toBeNull();
});

it("gives each class's scroll area its own name", () => {
  render(<Matrix confusion={CONFUSION} alertClassName="oauth_consent" />);

  expect(
    screen.queryByRole("region", { name: "oauth_consent: rows are the analyst verdict, columns the agent verdict." }),
  ).not.toBeNull();
});

function mockWidths(scrollWidth: number, clientWidth: number) {
  vi.spyOn(Element.prototype, "scrollWidth", "get").mockReturnValue(scrollWidth);
  vi.spyOn(Element.prototype, "clientWidth", "get").mockReturnValue(clientWidth);
}

it("makes a matrix wider than its scroll area a tab stop", () => {
  mockWidths(400, 300);

  render(<Matrix confusion={CONFUSION} alertClassName="impossible_travel" />);

  expect(screen.getByRole("region").getAttribute("tabindex")).toBe("0");
});

it("leaves a matrix that fits out of the tab order", () => {
  mockWidths(300, 300);

  render(<Matrix confusion={CONFUSION} alertClassName="impossible_travel" />);

  expect(screen.getByRole("region").hasAttribute("tabindex")).toBe(false);
});
