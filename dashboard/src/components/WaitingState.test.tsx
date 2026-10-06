import { cleanup, render, screen } from "@testing-library/react";
import { afterEach, expect, it } from "vitest";

import { WaitingState } from "./WaitingState";

const WAITING = {
  counts: [
    { label: "Spans received", value: "1,200" },
    { label: "Cases settled", value: "2" },
  ],
  notes: [{ message: "2 duplicate verdict rows.", hint: "Keep one row per case." }],
  next_step_text: null,
};
const NEXT_STEP_TEXT = "Upload traces and verdicts on the Data page to get started.";

afterEach(() => {
  cleanup();
});

it("pairs each count with its label", () => {
  const { container } = render(<WaitingState waiting={WAITING} />);

  expect(
    Array.from(container.querySelectorAll("dl dt"), (term) => `${term.textContent}=${term.nextElementSibling?.textContent}`),
  ).toEqual(["Spans received=1,200", "Cases settled=2"]);
});

it("lists each data note with its hint", () => {
  render(<WaitingState waiting={WAITING} />);

  expect(screen.getByRole("listitem").textContent).toBe("2 duplicate verdict rows.Keep one row per case.");
});

it("names the data notes section", () => {
  render(<WaitingState waiting={WAITING} />);

  expect(screen.getByRole("region", { name: "Data notes" })).toBeDefined();
});

it("leaves out the data notes section when there are none", () => {
  render(<WaitingState waiting={{ ...WAITING, notes: [] }} />);

  expect(screen.queryByRole("region", { name: "Data notes" })).toBeNull();
});

it("links the next step to the Data page", () => {
  render(<WaitingState waiting={{ ...WAITING, next_step_text: NEXT_STEP_TEXT }} />);

  expect(screen.getByRole("link", { name: NEXT_STEP_TEXT }).getAttribute("href")).toBe("#/data");
});

it("shows no next step when the view has none", () => {
  render(<WaitingState waiting={WAITING} />);

  expect(screen.queryByRole("link")).toBeNull();
});
