import { cleanup, render, screen } from "@testing-library/react";
import { userEvent } from "@testing-library/user-event";
import { afterEach, expect, it } from "vitest";

import edgeView from "../../../tests/fixtures/edge/versions/more_than_six/expected-view.json";
import { DEMO_RESULTS, DEMO_VIEW } from "../test-fixtures";
import { SkippedSteps } from "./SkippedSteps";

import type { View } from "../view";

const EDGE_VIEW = edgeView as unknown as View;

afterEach(() => {
  cleanup();
  window.history.replaceState(null, "", "#");
});

it("labels the panel by the selected class's tab", () => {
  render(<SkippedSteps view={DEMO_VIEW} results={DEMO_RESULTS} />);

  expect(screen.queryByRole("tabpanel", { name: "impossible_travel" })).not.toBeNull();
});

it("points the class tabs at the panel", () => {
  render(<SkippedSteps view={DEMO_VIEW} results={DEMO_RESULTS} />);

  expect(screen.getByRole("tab", { selected: true }).getAttribute("aria-controls")).toBe(
    screen.getByRole("tabpanel").id,
  );
});

it("shows the selected class's steps", () => {
  render(<SkippedSteps view={DEMO_VIEW} results={DEMO_RESULTS} />);

  expect(screen.queryByText("signin_history")).not.toBeNull();
});

it("shows another class's table when its tab is chosen", async () => {
  render(<SkippedSteps view={DEMO_VIEW} results={DEMO_RESULTS} />);

  await userEvent.click(screen.getByRole("tab", { name: "oauth_consent" }));

  expect(screen.queryByRole("table", { name: /^oauth_consent: / })).not.toBeNull();
});

it("says a class has no checklist", () => {
  render(<SkippedSteps view={EDGE_VIEW} results={DEMO_RESULTS} />);

  expect(screen.queryByText("No checklist for this class.")).not.toBeNull();
});
