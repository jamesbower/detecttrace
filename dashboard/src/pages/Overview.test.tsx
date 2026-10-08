import { cleanup, render, screen, within } from "@testing-library/react";
import { userEvent } from "@testing-library/user-event";
import { afterEach, expect, it } from "vitest";

import { TERMS } from "../help/terms";
import { registerPanel, resetRegistryForTests } from "../registry";
import { createClasses, DEMO_RESULTS, DEMO_VIEW } from "../test-fixtures";
import { Overview } from "./Overview";

import type { View } from "../view";

const LOW_COVERAGE = "Only 40 of 201 verdicts matched a trace (20%). See Data.";

afterEach(() => {
  cleanup();
  resetRegistryForTests();
  window.history.replaceState(null, "", "#");
});

function renderOverview(view: View = DEMO_VIEW) {
  return render(<Overview view={view} results={DEMO_RESULTS} />);
}

function definitionFor(term: string): string | null {
  // By its whole text: a term that explains itself holds its name in a button.
  const termElement = screen.getAllByRole("term").find((element) => element.textContent === term);
  if (termElement === undefined) {
    throw new Error(`no term "${term}"`);
  }
  return termElement.parentElement!.querySelector("dd")!.textContent;
}

it("titles the page", () => {
  renderOverview();

  expect(screen.getByRole("heading", { level: 1 }).textContent).toBe("Agent vs. analyst");
});

it("shows the case count exactly as the view writes it", () => {
  renderOverview();

  expect(definitionFor("Cases")).toBe("201 in 2 alert classes");
});

it("shows the period exactly as the view writes it", () => {
  renderOverview();

  expect(definitionFor("Period")).toBe("2026-W32 to 2026-W37 (6 weeks, UTC)");
});

it("shows each coverage line", () => {
  renderOverview();

  expect(definitionFor("Coverage")).toBe(
    "201 of 201 verdicts matched a trace (100%).201 of 201 traces matched a verdict (100%).",
  );
});

it("shows each class's dangerous false closes", () => {
  renderOverview();

  expect(definitionFor("Dangerous false closes")).toBe(
    "impossible_travel: 3 of 14 true positivesoauth_consent: 1 of 16 true positives",
  );
});

it("shows each class's dangerous false closes as a share of its true positives", () => {
  const [first, ...rest] = DEMO_VIEW.classes;
  const confusion = { ...first!.confusion, dangerous_share_text: "6 of 40 true positives" };
  renderOverview({ ...DEMO_VIEW, classes: [{ ...first!, confusion }, ...rest] });

  const kpi = screen.getAllByRole("term").find((element) => element.textContent === "Dangerous false closes")!;

  expect(kpi.parentElement!.querySelector("li")!.textContent).toBe("impossible_travel: 6 of 40 true positives");
});

it("says what the dangerous false closes figure counts out of", () => {
  renderOverview();
  const kpi = screen.getAllByRole("term").find((element) => element.textContent === "Dangerous false closes")!;

  expect(kpi.parentElement!.querySelector(".kpi-context")!.textContent).toBe(
    "The analyst said true positive; the agent said false positive or benign. Shown out of the analyst's true positives that have an agent verdict.",
  );
});

it("shows the low-coverage warning as a link to the data notes", () => {
  renderOverview({ ...DEMO_VIEW, header: { ...DEMO_VIEW.header, low_coverage_text: LOW_COVERAGE } });

  expect(screen.getByRole("link", { name: LOW_COVERAGE }).getAttribute("href")).toBe("#/data?class=class-0");
});

it("shows no low-coverage warning when coverage is fine", () => {
  renderOverview();

  expect(screen.getAllByRole("link").map((link) => link.getAttribute("href"))).not.toContain(
    "#/data?class=class-0",
  );
});

it("shows no list of data sources", () => {
  renderOverview();

  expect(screen.queryByRole("list", { name: "Data sources" })).toBeNull();
});

it("labels the class panel by the selected tab", () => {
  renderOverview();

  expect(screen.queryByRole("tabpanel", { name: "impossible_travel" })).not.toBeNull();
});

it("shows the selected class's version table", () => {
  renderOverview();

  expect(screen.queryByRole("table", { name: /^impossible_travel:/ })).not.toBeNull();
});

it("shows another class's table after its tab is clicked", async () => {
  renderOverview();

  await userEvent.click(screen.getByRole("tab", { name: "oauth_consent" }));

  expect(screen.queryByRole("table", { name: /^oauth_consent:/ })).not.toBeNull();
});

it("links the tiles to their pages with the selected class", () => {
  window.history.replaceState(null, "", "#/?class=class-1");
  renderOverview();

  expect(
    within(screen.getByRole("navigation", { name: "More for oauth_consent" }))
      .getAllByRole("link")
      .map((link) => link.getAttribute("href")),
  ).toEqual(["#/skipped?class=class-1", "#/trends?class=class-1", "#/verdicts?class=class-1"]);
});

it("shows the class's figures on the tiles", () => {
  renderOverview();

  expect(screen.getByRole("link", { name: /^Verdict matrix/ }).textContent).toBe(
    "Verdict matrix3 dangerous false closesn = 106",
  );
});

it("names the panel by the class when a drop-down picks it", () => {
  renderOverview({ ...DEMO_VIEW, classes: createClasses(7) });

  expect(screen.queryByRole("region", { name: "class_0" })).not.toBeNull();
});

it("renders panels registered after the headline figures", () => {
  registerPanel("overview-after-kpis", () => <p>Extra panel</p>);
  renderOverview();

  expect(screen.queryByText("Extra panel")).not.toBeNull();
});

it("explains the measures in the version table", () => {
  renderOverview();
  const header = screen.getByRole("columnheader", { name: TERMS.completeness.label });

  expect(within(header).queryByRole("button", { name: TERMS.completeness.label })).not.toBeNull();
});

it("explains the dangerous false closes figure where it is named", () => {
  renderOverview();
  const term = screen.getAllByRole("term").find((element) => element.textContent === TERMS.dangerous.label)!;

  expect(within(term).queryByRole("button", { name: TERMS.dangerous.label })).not.toBeNull();
});
