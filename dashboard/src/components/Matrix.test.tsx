import { cleanup, render, screen } from "@testing-library/react";
import { afterEach, expect, it } from "vitest";

import { DEMO_VIEW } from "../test-fixtures";
import { Matrix } from "./Matrix";

const CONFUSION = DEMO_VIEW.classes[0]!.confusion;

afterEach(cleanup);

it("heads the columns with the agent verdicts", () => {
  render(<Matrix confusion={CONFUSION} />);

  expect(screen.getAllByRole("columnheader").map((header) => header.textContent)).toEqual([
    "Analyst verdict by agent verdict",
    "Agent: true positive",
    "Agent: false positive",
    "Agent: benign",
  ]);
});

it("heads the rows with the analyst verdicts", () => {
  render(<Matrix confusion={CONFUSION} />);

  expect(screen.getAllByRole("rowheader").map((header) => header.textContent)).toEqual([
    "Analyst: true positive",
    "Analyst: false positive",
    "Analyst: benign",
  ]);
});

it("shows each cell's count", () => {
  render(<Matrix confusion={CONFUSION} />);

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
  render(<Matrix confusion={CONFUSION} />);

  expect(screen.getAllByText("Dangerous")).toHaveLength(2);
});

it("marks a dangerous cell that holds cases as flagged", () => {
  render(<Matrix confusion={CONFUSION} />);

  expect(screen.getByRole("cell", { name: "3 Dangerous" }).classList.contains("is-flagged")).toBe(true);
});

it("leaves an empty dangerous cell unflagged", () => {
  render(<Matrix confusion={CONFUSION} />);

  expect(screen.getByRole("cell", { name: "0 Dangerous" }).classList.contains("is-flagged")).toBe(false);
});

it("colors a cell from its heat class", () => {
  render(<Matrix confusion={CONFUSION} />);

  expect(screen.getByRole("cell", { name: "37" }).classList.contains("matrix-h-ok-4")).toBe(true);
});

it("names the table by its caption", () => {
  render(<Matrix confusion={CONFUSION} />);

  expect(
    screen.queryByRole("table", { name: "Rows are the analyst verdict, columns the agent verdict." }),
  ).not.toBeNull();
});
