import { cleanup, fireEvent, render, screen } from "@testing-library/react";
import { afterEach, describe, expect, expectTypeOf, it } from "vitest";

import { startRouter } from "../router";
import { PageLink } from "./PageLink";

import type * as React from "react";

afterEach(() => {
  cleanup();
  Reflect.deleteProperty(Element.prototype, "scrollIntoView");
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

function renderSectionLink() {
  render(
    <PageLink path="/help" section="evidence-completeness">
      Evidence completeness
    </PageLink>,
  );
  return screen.getByRole("link", { name: "Evidence completeness" });
}

describe("with a section", () => {
  it("links to the section of the page's hash on an offline page", () => {
    startRouter("offline");

    expect(renderSectionLink().getAttribute("href")).toBe("#/help#evidence-completeness");
  });

  it("links to the section of the page's path on a served page", () => {
    startRouter("served");

    expect(renderSectionLink().getAttribute("href")).toBe("/help#evidence-completeness");
  });

  it("opens the section in place on a click on a ui page", () => {
    startRouter("ui");

    fireEvent.click(renderSectionLink());

    expect(`${window.location.pathname}${window.location.hash}`).toBe("/help#evidence-completeness");
  });
});

// A link to the section already open changes no address, so no route change moves focus.
function clickLinkToOpenSection() {
  // jsdom lays nothing out, so it has no scrollIntoView.
  Element.prototype.scrollIntoView = () => {};
  render(
    <>
      <PageLink path="/help" section="evidence-completeness">
        Back to the section
      </PageLink>
      <h2 id="evidence-completeness" tabIndex={-1}>
        Evidence completeness
      </h2>
    </>,
  );
  fireEvent.click(screen.getByRole("link", { name: "Back to the section" }));
  return screen.getByRole("heading", { name: "Evidence completeness" });
}

describe("with the section already open", () => {
  it("focuses the section again on a click on an offline page", () => {
    window.history.replaceState(null, "", "#/help#evidence-completeness");
    startRouter("offline");

    const section = clickLinkToOpenSection();

    expect(document.activeElement).toBe(section);
  });

  it("focuses the section again on a click on a served page", () => {
    window.history.replaceState(null, "", "/help#evidence-completeness");
    startRouter("served");

    const section = clickLinkToOpenSection();

    expect(document.activeElement).toBe(section);
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

// Checked by the type checker: a page link opens in place, so it takes neither a target nor a
// download, which only the browser's own handling of a click would honour.
it("takes no target", () => {
  expectTypeOf<React.ComponentProps<typeof PageLink>>().not.toHaveProperty("target");
});

it("takes no download", () => {
  expectTypeOf<React.ComponentProps<typeof PageLink>>().not.toHaveProperty("download");
});
