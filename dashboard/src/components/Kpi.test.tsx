import { cleanup, render, screen } from "@testing-library/react";
import { afterEach, expect, it } from "vitest";

import { Kpi } from "./Kpi";

afterEach(cleanup);

it("shows the label as the term", () => {
  render(
    <dl>
      <Kpi label="Cases" value="201 in 2 alert classes" />
    </dl>,
  );

  expect(screen.getByRole("term").textContent).toBe("Cases");
});

it("shows the value and the context as its definitions", () => {
  render(
    <dl>
      <Kpi label="Cases" value="201 in 2 alert classes" context="Versions found: v1, v2" />
    </dl>,
  );

  expect(screen.getAllByRole("definition").map((item) => item.textContent)).toEqual([
    "201 in 2 alert classes",
    "Versions found: v1, v2",
  ]);
});

it("shows no context line without a context", () => {
  render(
    <dl>
      <Kpi label="Period" value="2026-W32 to 2026-W37 (6 weeks, UTC)" />
    </dl>,
  );

  expect(screen.getAllByRole("definition")).toHaveLength(1);
});
