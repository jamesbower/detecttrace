import { cleanup, render, screen } from "@testing-library/react";
import { userEvent } from "@testing-library/user-event";
import { afterEach, expect, it } from "vitest";

import edgeView from "../../../tests/fixtures/edge/versions/more_than_six/expected-view.json";
import { DEMO_RESULTS, DEMO_VIEW } from "../test-fixtures";
import { Versions } from "./Versions";

import type { View } from "../view";

const EDGE_VIEW = edgeView as unknown as View;

afterEach(() => {
  cleanup();
  window.history.replaceState(null, "", "#");
});

function renderVersions(view: View = DEMO_VIEW) {
  return render(<Versions view={view} results={DEMO_RESULTS} />);
}

it("titles the page", () => {
  renderVersions();

  expect(screen.getByRole("heading", { level: 1 }).textContent).toBe("By version");
});

it("labels the class panel by the selected tab", () => {
  renderVersions();

  expect(screen.queryByRole("tabpanel", { name: "impossible_travel" })).not.toBeNull();
});

it("shows the class the hash names", () => {
  window.history.replaceState(null, "", "#/versions?class=class-1");
  renderVersions();

  expect(screen.queryByRole("table", { name: /^oauth_consent:/ })).not.toBeNull();
});

it("shows another class's rows after its tab is clicked", async () => {
  renderVersions();

  await userEvent.click(screen.getByRole("tab", { name: "oauth_consent" }));

  expect(screen.queryByRole("heading", { level: 2, name: /^oauth_consent 95 cases/ })).not.toBeNull();
});

it("shows every version of a class with more than six", () => {
  renderVersions(EDGE_VIEW);

  expect(screen.getAllByRole("rowheader").map((header) => header.textContent)).toEqual([
    "All versions",
    "v1Few cases.",
    "v3Few cases.",
    "v4Few cases.",
    "v6Few cases.",
    "v7Few cases.",
    "v8Few cases.",
    "(other versions: v2, v5)Few cases.",
  ]);
});

it("shows the view's reason for a missing value", () => {
  renderVersions(EDGE_VIEW);

  expect(screen.getAllByText("No checklist for this class.")).toHaveLength(8);
});
