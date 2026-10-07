import { cleanup, render, screen, within } from "@testing-library/react";
import { afterEach, expect, it } from "vitest";

import "../help/sections";
import { TERMS } from "../help/terms";
import { registerHelpSection } from "../registry";
import { DEMO_VIEW } from "../test-fixtures";
import { Help } from "./Help";

import type { View } from "../view";

// A section from outside the built-in set, as a custom build would add one.
registerHelpSection({ id: "extra-notes", title: "Extra notes", order: 20, body: () => <p>Site notes.</p> });

const OFFLINE_VIEW: View = { ...DEMO_VIEW, mode: "offline" };
const UI_VIEW: View = { ...DEMO_VIEW, mode: "ui" };

afterEach(cleanup);

function listHeadingIds(): string[] {
  return screen.getAllByRole("heading", { level: 2 }).map((heading) => heading.id);
}

it("titles the page", () => {
  render(<Help view={OFFLINE_VIEW} />);

  expect(screen.getByRole("heading", { level: 1 }).textContent).toBe("How DetectTrace works");
});

it("renders every section under a heading with its id", () => {
  render(<Help view={UI_VIEW} />);

  expect(listHeadingIds()).toEqual([
    "how-it-works",
    "pages",
    "evidence-completeness",
    "verdict-agreement",
    "chance-corrected-agreement",
    "dangerous-false-closes",
    "reading-the-numbers",
    "using-the-app",
    "more",
    "extra-notes",
  ]);
});

it("labels each section by its heading", () => {
  render(<Help view={UI_VIEW} />);

  expect(screen.getAllByRole("region").map((region) => region.getAttribute("aria-labelledby"))).toEqual(
    listHeadingIds(),
  );
});

it("links each contents entry to its section's heading", () => {
  render(<Help view={OFFLINE_VIEW} />);
  const contents = screen.getByRole("navigation", { name: "On this page" });

  expect(
    within(contents)
      .getAllByRole("link")
      .map((link) => link.getAttribute("href")),
  ).toEqual(listHeadingIds().map((id) => `#/help#${id}`));
});

it("explains using the app in the ui app", () => {
  render(<Help view={UI_VIEW} />);

  expect(screen.queryByRole("heading", { name: "Using the app" })).not.toBeNull();
});

it("leaves out using the app outside the ui app", () => {
  render(<Help view={OFFLINE_VIEW} />);

  expect(screen.queryByRole("heading", { name: "Using the app" })).toBeNull();
});

it("has a section for each term, titled with the term's label", () => {
  render(<Help view={OFFLINE_VIEW} />);

  expect(Object.values(TERMS).map((term) => document.getElementById(term.anchor)?.textContent)).toEqual(
    Object.values(TERMS).map((term) => term.label),
  );
});

it("shows a section a test module registers", () => {
  render(<Help view={OFFLINE_VIEW} />);

  expect(screen.getByRole("region", { name: "Extra notes" }).textContent).toContain("Site notes.");
});

it("opens the documentation links without a referrer", () => {
  render(<Help view={OFFLINE_VIEW} />);
  const more = screen.getByRole("region", { name: "More" });

  expect(
    new Set(
      within(more)
        .getAllByRole("link")
        .filter((link) => link.getAttribute("href")?.startsWith("https://"))
        .map((link) => link.getAttribute("rel")),
    ),
  ).toEqual(new Set(["noreferrer"]));
});

it("names GitHub in each documentation link", () => {
  render(<Help view={OFFLINE_VIEW} />);
  const more = screen.getByRole("region", { name: "More" });

  expect(
    within(more)
      .getAllByRole("link")
      .filter((link) => link.getAttribute("href")?.startsWith("https://"))
      .map((link) => link.textContent),
  ).toEqual([
    "Metrics (GitHub)",
    "Trace attributes (GitHub)",
    "Checklists (GitHub)",
    "Running locally in your browser (GitHub)",
    "Running as a service (GitHub)",
  ]);
});

it("says the documentation links need a network connection", () => {
  render(<Help view={OFFLINE_VIEW} />);

  expect(
    within(screen.getByRole("region", { name: "More" })).queryByText(
      "These open on GitHub and need a network connection:",
    ),
  ).not.toBeNull();
});

it("titles the first section apart from the page", () => {
  render(<Help view={OFFLINE_VIEW} />);

  expect(screen.getAllByRole("heading", { level: 2 })[0]?.textContent).toBe("Cases, alert classes and checklists");
});

it("links to the Limits page", () => {
  render(<Help view={OFFLINE_VIEW} />);

  expect(screen.getByRole("link", { name: "Limits" }).getAttribute("href")).toBe("#/limits");
});

it("limits the one-case and all-equal interval rule to evidence completeness", () => {
  render(<Help view={OFFLINE_VIEW} />);
  const reading = screen.getByRole("region", { name: "Reading the numbers" });

  expect(within(reading).getByText(/one case, or all its cases have the same value/).textContent).toMatch(
    /^an evidence completeness value/,
  );
});
