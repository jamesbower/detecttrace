import { cleanup, render, screen } from "@testing-library/react";
import { afterEach, expect, it } from "vitest";

import { DEMO_RESULTS, DEMO_VIEW } from "../test-fixtures";
import { Limits } from "./Limits";

afterEach(cleanup);

it("titles the page", () => {
  render(<Limits view={DEMO_VIEW} results={DEMO_RESULTS} />);

  expect(screen.getByRole("heading", { level: 1 }).textContent).toBe("Limits");
});

it("names each limit the view lists", () => {
  render(<Limits view={DEMO_VIEW} results={DEMO_RESULTS} />);

  expect(screen.getAllByRole("term").map((term) => term.textContent)).toEqual([
    "Why the numbers changed.",
    "Whether the agent's claims match the evidence.",
    "When to act.",
  ]);
});

it("explains each limit in the view's words", () => {
  render(<Limits view={DEMO_VIEW} results={DEMO_RESULTS} />);

  expect(screen.getAllByRole("definition").map((text) => text.textContent)).toEqual(
    DEMO_VIEW.limits.map((limit) => limit.text),
  );
});
