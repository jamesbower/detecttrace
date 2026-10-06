// The registry is the extension point: a downstream build adds a page or a panel by
// registering it, without editing the shell.
import { cleanup, render, screen } from "@testing-library/react";
import { afterEach, beforeEach, expect, it } from "vitest";

import { App } from "./App";
import { PanelSlot } from "./components/PanelSlot";
import { registerPage, registerPanel, resetRegistryForTests } from "./registry";
import { DEMO_RESULTS, DEMO_VIEW } from "./test-fixtures";

import type { PageProps } from "./registry";

function ExtraPage(props: PageProps) {
  return (
    <>
      <h1>Extra</h1>
      <PanelSlot name="trend-footer" {...props} />
    </>
  );
}

function TitlePanel({ view }: PageProps) {
  return <p>{`Panel for ${view.header.title}`}</p>;
}

beforeEach(() => {
  resetRegistryForTests();
  registerPage({ path: "/extra", title: "Extra", icon: "", order: 99, component: ExtraPage });
  registerPanel("trend-footer", TitlePanel);
  window.history.replaceState(null, "", "#/extra");
});

afterEach(() => {
  cleanup();
  window.history.replaceState(null, "", "#");
});

it("links a registered page from the navigation", () => {
  render(<App data={{ view: DEMO_VIEW, results: DEMO_RESULTS }} />);

  expect(screen.getByRole("link", { name: "Extra" }).getAttribute("href")).toBe("#/extra");
});

it("renders a registered panel in its slot, with the page data", () => {
  render(<App data={{ view: DEMO_VIEW, results: DEMO_RESULTS }} />);

  expect(screen.getByText("Panel for DetectTrace").tagName).toBe("P");
});
