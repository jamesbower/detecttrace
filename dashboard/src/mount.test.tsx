// A custom build registers its pages and panels, then mounts the dashboard on the page.
import { screen, within } from "@testing-library/react";
import { act } from "react";
import { afterEach, beforeEach, expect, it } from "vitest";

import { mountDashboard } from "./mount";
import { registerPanel } from "./registry";
import { DEMO_RESULTS, DEMO_VIEW } from "./test-fixtures";

import type { Root } from "react-dom/client";

let root: Root | null = null;

function writePage() {
  document.body.innerHTML = [
    '<div id="root"></div>',
    `<script type="application/json" id="dt-view">${JSON.stringify(DEMO_VIEW)}</script>`,
    `<script type="application/json" id="dt-results">${JSON.stringify(DEMO_RESULTS)}</script>`,
  ].join("");
}

function AddedPanel() {
  return <p>Added panel</p>;
}

// Registered once, before any mount, as a custom build's main module does. No registry reset:
// the CE pages register when `./pages` is first imported and would not come back.
registerPanel("overview-after-kpis", AddedPanel);

beforeEach(() => {
  window.history.replaceState(null, "", "#");
  writePage();
});

afterEach(() => {
  act(() => root?.unmount());
  root = null;
  document.body.innerHTML = "";
});

it("renders the dashboard into the page's root", async () => {
  act(() => {
    root = mountDashboard();
  });
  const rootElement = document.getElementById("root") as HTMLElement;
  const brand = await within(rootElement).findByText(DEMO_VIEW.header.title, { selector: ".shell-brand" });
  expect(brand.tagName).toBe("P");
});

it("shows a panel registered before mounting", async () => {
  act(() => {
    root = mountDashboard();
  });
  expect((await screen.findByText("Added panel")).tagName).toBe("P");
});
