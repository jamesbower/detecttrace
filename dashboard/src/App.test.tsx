import { cleanup, render, screen } from "@testing-library/react";
import { afterEach, beforeEach, expect, it } from "vitest";

import { App } from "./App";
import { registerPage, resetRegistryForTests } from "./registry";
import { DEMO_RESULTS, DEMO_VIEW } from "./test-fixtures";

import type { PageProps } from "./registry";

const DATA = { view: DEMO_VIEW, results: DEMO_RESULTS };

function HomePage() {
  return <h1>Home</h1>;
}

function CasesPage({ results }: PageProps) {
  return <h1>{`${results.case_rows.columns.case_id.length} cases`}</h1>;
}

beforeEach(() => {
  resetRegistryForTests();
  registerPage({ path: "/", title: "Home", icon: "", order: 0, component: HomePage });
  registerPage({ path: "/cases", title: "Cases", icon: "", order: 1, component: CasesPage });
});

afterEach(() => {
  cleanup();
  window.history.replaceState(null, "", "#");
});

it("names each registered page in the navigation", () => {
  render(<App data={DATA} />);

  expect(screen.getByRole("navigation", { name: "Pages" }).textContent).toBe("HomeCases");
});

it("renders the page the hash names, with the page data", () => {
  window.history.replaceState(null, "", "#/cases");

  render(<App data={DATA} />);

  expect(screen.getByRole("heading", { level: 1 }).textContent).toBe("201 cases");
});

it("renders the home page for an unknown hash", () => {
  window.history.replaceState(null, "", "#/nowhere");

  render(<App data={DATA} />);

  expect(screen.getByRole("heading", { level: 1 }).textContent).toBe("Home");
});

it("shows why the page data could not be read", () => {
  render(<App data={{ error: 'The page has no "dt-view" data block.' }} />);

  expect(screen.getByRole("alert").textContent).toContain('The page has no "dt-view" data block.');
});

it("shows no navigation when the page data could not be read", () => {
  render(<App data={{ error: "broken" }} />);

  expect(screen.queryByRole("navigation")).toBeNull();
});
