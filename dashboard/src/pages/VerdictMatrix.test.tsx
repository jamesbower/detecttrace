import { cleanup, render, screen } from "@testing-library/react";
import { afterEach, expect, it } from "vitest";

import edgeView from "../../../tests/fixtures/edge/versions/more_than_six/expected-view.json";
import { DEMO_RESULTS, DEMO_VIEW } from "../test-fixtures";
import { VerdictMatrix } from "./VerdictMatrix";

import type { View } from "../view";

const EDGE_VIEW = edgeView as unknown as View;

afterEach(cleanup);

it("shows a matrix for every class at once", () => {
  render(<VerdictMatrix view={DEMO_VIEW} results={DEMO_RESULTS} />);

  expect(screen.getAllByRole("heading", { level: 2 }).map((heading) => heading.firstChild?.textContent)).toEqual([
    "impossible_travel",
    "oauth_consent",
  ]);
});

it("puts the case count and dangerous closes in a class's heading", () => {
  render(<VerdictMatrix view={DEMO_VIEW} results={DEMO_RESULTS} />);

  expect(
    screen.queryByRole("heading", {
      level: 2,
      name: "impossible_travel All versions · n = 106 · 3 dangerous false closes",
    }),
  ).not.toBeNull();
});

it("sets each class's name apart in its heading, so it keeps its case", () => {
  render(<VerdictMatrix view={DEMO_VIEW} results={DEMO_RESULTS} />);

  expect(
    screen.getAllByRole("heading", { level: 2 }).map((heading) => heading.querySelector(".class-name")?.textContent),
  ).toEqual(["impossible_travel", "oauth_consent"]);
});

it("labels every dangerous cell across classes", () => {
  render(<VerdictMatrix view={DEMO_VIEW} results={DEMO_RESULTS} />);

  expect(screen.getAllByText("Dangerous")).toHaveLength(4);
});

it("shows the edge fixture's single class", () => {
  render(<VerdictMatrix view={EDGE_VIEW} results={DEMO_RESULTS} />);

  expect(screen.queryByRole("region", { name: "impossible_travel All versions · n = 33 · 1 dangerous false close" })).not.toBeNull();
});
