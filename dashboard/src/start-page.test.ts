import { afterEach, expect, it } from "vitest";

import { startRouter } from "./router";
import { openStartPage } from "./start-page";
import { DEMO_RESULTS, DEMO_VIEW } from "./test-fixtures";

const WAITING = { counts: [], notes: [], next_step_text: "Upload traces and verdicts on the Data page to get started." };
const UI_WAITING_DATA = { view: { ...DEMO_VIEW, mode: "ui" as const, waiting: WAITING }, waiting: WAITING };

function openWithHash(hash: string) {
  window.history.replaceState(null, "", hash === "" ? window.location.pathname : hash);
}

afterEach(() => {
  startRouter("offline");
  window.history.replaceState(null, "", "/");
});

it("opens a ui waiting page with no page named on the Data page", () => {
  openWithHash("");

  openStartPage(UI_WAITING_DATA);

  expect(window.location.hash).toBe("#/data");
});

it("keeps the page a ui waiting page's address names", () => {
  openWithHash("#/cases");

  openStartPage(UI_WAITING_DATA);

  expect(window.location.hash).toBe("#/cases");
});

it("opens a served waiting page where it was", () => {
  openWithHash("");

  openStartPage({ ...UI_WAITING_DATA, view: { ...UI_WAITING_DATA.view, mode: "served" } });

  expect(window.location.hash).toBe("");
});

it("opens a ui page with results where it was", () => {
  openWithHash("");

  openStartPage({ view: { ...DEMO_VIEW, mode: "ui" }, results: DEMO_RESULTS });

  expect(window.location.hash).toBe("");
});

it("leaves the address alone when the page data could not be read", () => {
  openWithHash("");

  openStartPage({ error: "broken" });

  expect(window.location.hash).toBe("");
});

it("opens a ui waiting page at the root on the Data page's path", () => {
  window.history.replaceState(null, "", "/");
  startRouter("ui");

  openStartPage(UI_WAITING_DATA);

  expect(window.location.pathname).toBe("/data");
});

it("keeps the page path a ui waiting page's address names", () => {
  window.history.replaceState(null, "", "/cases");
  startRouter("ui");

  openStartPage(UI_WAITING_DATA);

  expect(window.location.pathname).toBe("/cases");
});

it("opens a ui waiting page's old hash address at its path", () => {
  window.history.replaceState(null, "", "/#/cases");
  startRouter("ui");

  openStartPage(UI_WAITING_DATA);

  expect(window.location.pathname).toBe("/cases");
});
