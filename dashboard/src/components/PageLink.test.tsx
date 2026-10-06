import { cleanup, fireEvent, render, screen } from "@testing-library/react";
import { afterEach, describe, expect, it } from "vitest";

import { startRouter } from "../router";
import { PageLink } from "./PageLink";

afterEach(() => {
  cleanup();
  startRouter("offline");
  window.history.replaceState(null, "", "/");
});

function renderLink() {
  render(
    <PageLink path="/cases" query={new URLSearchParams({ class: "class-1" })} aria-current="page">
      Cases
    </PageLink>,
  );
  return screen.getByRole("link", { name: "Cases" });
}

describe("on an offline page", () => {
  it("links to the page's hash", () => {
    startRouter("offline");

    expect(renderLink().getAttribute("href")).toBe("#/cases?class=class-1");
  });

  it("leaves a click to the browser", () => {
    startRouter("offline");

    expect(fireEvent.click(renderLink())).toBe(true);
  });
});

describe("on a served or ui page", () => {
  it("links to the page's path", () => {
    startRouter("served");

    expect(renderLink().getAttribute("href")).toBe("/cases?class=class-1");
  });

  it("keeps the attributes it is given", () => {
    startRouter("served");

    expect(renderLink().getAttribute("aria-current")).toBe("page");
  });

  it("opens the page in place on a click", () => {
    startRouter("ui");

    fireEvent.click(renderLink());

    expect(`${window.location.pathname}${window.location.search}`).toBe("/cases?class=class-1");
  });

  it("stops the browser loading the page again on a click", () => {
    startRouter("ui");

    expect(fireEvent.click(renderLink())).toBe(false);
  });

  it.each([
    { name: "Ctrl", init: { ctrlKey: true } },
    { name: "Cmd", init: { metaKey: true } },
    { name: "Shift", init: { shiftKey: true } },
    { name: "Alt", init: { altKey: true } },
    { name: "middle-button", init: { button: 1 } },
  ])("leaves a $name click to the browser, to open a new tab or window", ({ init }) => {
    startRouter("served");

    expect(fireEvent.click(renderLink(), init)).toBe(true);
  });

  it("leaves the address alone on a Ctrl click", () => {
    startRouter("served");

    fireEvent.click(renderLink(), { ctrlKey: true });

    expect(window.location.pathname).toBe("/");
  });
});
