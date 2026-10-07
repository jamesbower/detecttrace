import { act, cleanup, render, screen, waitFor } from "@testing-library/react";
import { userEvent } from "@testing-library/user-event";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { registerPage, resetRegistryForTests } from "./registry";
import { parseAddress, replaceEmptyAddress, startRouter, toHref, useRoute, useSectionFocus } from "./router";

const PATHS = ["/", "/versions", "/cases", "/data", "/help"];
const SECTION = "evidence-completeness";

function RouteProbe() {
  const route = useRoute();
  return (
    <>
      <p data-testid="path">{route.path}</p>
      <p data-testid="query">{route.query.toString()}</p>
      <p data-testid="section">{route.section ?? "none"}</p>
      <button type="button" onClick={() => route.setQuery({ class: "class-1" })}>
        Pick class
      </button>
      <button type="button" onClick={() => route.setQuery({ dangerous: null })}>
        Clear dangerous
      </button>
      <button
        type="button"
        onClick={() => {
          route.setQuery({ class: "class-1" });
          route.setQuery({ dangerous: "1" });
        }}
      >
        Set both
      </button>
      <button type="button" onClick={() => route.navigate("/cases", { q: "DT-1" })}>
        Open cases
      </button>
    </>
  );
}

function SectionProbe() {
  const route = useRoute();
  useSectionFocus(route.section);
  return (
    <>
      <button type="button" onClick={() => route.navigate("/help", {}, SECTION)}>
        Open section
      </button>
      <h2 id={SECTION} tabIndex={-1}>
        Evidence completeness
      </h2>
    </>
  );
}

describe("parseAddress", () => {
  it("reads the path", () => {
    expect(parseAddress("#/versions?class=class-1", PATHS).path).toBe("/versions");
  });

  it("reads a query parameter", () => {
    expect(parseAddress("#/versions?class=class-1&q=a+b", PATHS).query.get("q")).toBe("a b");
  });

  it("treats an empty hash as the home page", () => {
    expect(parseAddress("", PATHS).path).toBe("/");
  });

  it("treats a bare # as the home page", () => {
    expect(parseAddress("#", PATHS).path).toBe("/");
  });

  it("ignores a trailing slash", () => {
    expect(parseAddress("#/versions/", PATHS).path).toBe("/versions");
  });

  it("falls back to the home page for an unknown path", () => {
    expect(parseAddress("#/nowhere?class=class-1", PATHS).path).toBe("/");
  });

  it("keeps the query when the path is unknown", () => {
    expect(parseAddress("#/nowhere?class=class-1", PATHS).query.get("class")).toBe("class-1");
  });

  it("forwards the old data notes path to the data page", () => {
    expect(parseAddress("#/data-notes?class=x", PATHS).path).toBe("/data");
  });

  it("keeps the query when forwarding an old path", () => {
    expect(parseAddress("#/data-notes?class=x", PATHS).query.get("class")).toBe("x");
  });

  it("reads the path before a section", () => {
    expect(parseAddress(`#/help#${SECTION}`, PATHS).path).toBe("/help");
  });

  it("reads the section", () => {
    expect(parseAddress(`#/help#${SECTION}`, PATHS).section).toBe(SECTION);
  });

  it("reads the query before a section", () => {
    expect(parseAddress(`#/help?class=x#${SECTION}`, PATHS).query.get("class")).toBe("x");
  });

  it("reads no section from an address without one", () => {
    expect(parseAddress("#/help", PATHS).section).toBeNull();
  });

  it.each(["#//x", "#Bad", "#a_b", "#"])("ignores a section %s that is not a plain id", (section) => {
    expect(parseAddress(`#/help${section}`, PATHS).section).toBeNull();
  });
});

describe("toHref", () => {
  it("leaves out an empty query", () => {
    expect(toHref("/cases", new URLSearchParams())).toBe("#/cases");
  });

  it("appends a section to a hash link", () => {
    expect(toHref("/help", new URLSearchParams(), SECTION)).toBe(`#/help#${SECTION}`);
  });

  it("appends a section after the query", () => {
    expect(toHref("/help", new URLSearchParams({ class: "x" }), SECTION)).toBe(`#/help?class=x#${SECTION}`);
  });

  it("round-trips through parseAddress", () => {
    const hash = toHref("/cases", new URLSearchParams({ q: "a&b c" }));

    expect(parseAddress(hash, PATHS).query.get("q")).toBe("a&b c");
  });
});

describe("useRoute", () => {
  beforeEach(() => {
    resetRegistryForTests();
    for (const path of PATHS) {
      registerPage({ path, title: path, icon: "", order: 0, component: () => null });
    }
  });

  afterEach(() => {
    cleanup();
    window.history.replaceState(null, "", "#");
  });

  it("re-renders when the hash changes", async () => {
    window.history.replaceState(null, "", "#/");
    render(<RouteProbe />);

    act(() => {
      window.location.hash = "#/cases";
    });

    expect(await screen.findByText("/cases")).toBe(screen.getByTestId("path"));
  });

  it("adds a query parameter and keeps the others", async () => {
    window.history.replaceState(null, "", "#/versions?dangerous=1");
    render(<RouteProbe />);

    await userEvent.click(screen.getByRole("button", { name: "Pick class" }));

    expect(window.location.hash).toBe("#/versions?dangerous=1&class=class-1");
  });

  it("removes a query parameter set to null", async () => {
    window.history.replaceState(null, "", "#/versions?dangerous=1&class=class-0");
    render(<RouteProbe />);

    await userEvent.click(screen.getByRole("button", { name: "Clear dangerous" }));

    expect(window.location.hash).toBe("#/versions?class=class-0");
  });

  it("changes the query in place, adding no history entry", async () => {
    window.history.replaceState(null, "", "#/versions");
    const length = window.history.length;
    render(<RouteProbe />);

    await userEvent.click(screen.getByRole("button", { name: "Pick class" }));

    expect(window.history.length).toBe(length);
  });

  it("applies two query changes made in one event", async () => {
    window.history.replaceState(null, "", "#/versions");
    render(<RouteProbe />);

    await userEvent.click(screen.getByRole("button", { name: "Set both" }));

    expect(window.location.hash).toBe("#/versions?class=class-1&dangerous=1");
  });

  it("navigates to another page with a fresh query", async () => {
    window.history.replaceState(null, "", "#/versions?dangerous=1");
    render(<RouteProbe />);

    await userEvent.click(screen.getByRole("button", { name: "Open cases" }));

    expect(window.location.hash).toBe("#/cases?q=DT-1");
  });

  it("re-renders after a query change", async () => {
    window.history.replaceState(null, "", "#/versions");
    render(<RouteProbe />);

    await userEvent.click(screen.getByRole("button", { name: "Pick class" }));

    expect(screen.getByTestId("query").textContent).toBe("class=class-1");
  });

  it("rewrites an old path in the address to its new one, keeping the query", () => {
    window.history.replaceState(null, "", "#/data-notes?class=x");

    render(<RouteProbe />);

    expect(window.location.hash).toBe("#/data?class=x");
  });

  it("forwards an old path without adding a history entry", () => {
    window.history.replaceState(null, "", "#/data-notes");
    const length = window.history.length;

    render(<RouteProbe />);

    expect(window.history.length).toBe(length);
  });

  it("writes the home path when the query changes on an unknown path", async () => {
    window.history.replaceState(null, "", "#/nowhere");
    render(<RouteProbe />);

    await userEvent.click(screen.getByRole("button", { name: "Pick class" }));

    expect(window.location.hash).toBe("#/?class=class-1");
  });
});

describe("useSectionFocus", () => {
  const scrollIntoView = vi.fn();

  beforeEach(() => {
    resetRegistryForTests();
    for (const path of PATHS) {
      registerPage({ path, title: path, icon: "", order: 0, component: () => null });
    }
    // jsdom lays nothing out, so it has no scrollIntoView.
    Element.prototype.scrollIntoView = scrollIntoView;
  });

  afterEach(() => {
    cleanup();
    scrollIntoView.mockReset();
    Reflect.deleteProperty(Element.prototype, "scrollIntoView");
    startRouter("offline");
    window.history.replaceState(null, "", "/");
  });

  it("focuses the section a page opens at", () => {
    window.history.replaceState(null, "", `#/help#${SECTION}`);

    render(<SectionProbe />);

    expect(document.activeElement).toBe(screen.getByRole("heading", { name: "Evidence completeness" }));
  });

  it("focuses the section a link navigates to", async () => {
    window.history.replaceState(null, "", "#/help");
    render(<SectionProbe />);

    await userEvent.click(screen.getByRole("button", { name: "Open section" }));

    await waitFor(() =>
      expect(document.activeElement).toBe(screen.getByRole("heading", { name: "Evidence completeness" })),
    );
  });

  it("focuses the section a link navigates to on a served page", async () => {
    window.history.replaceState(null, "", "/help");
    startRouter("served");
    render(<SectionProbe />);

    await userEvent.click(screen.getByRole("button", { name: "Open section" }));

    expect(document.activeElement).toBe(screen.getByRole("heading", { name: "Evidence completeness" }));
  });

  it("scrolls the section's top into view", () => {
    window.history.replaceState(null, "", `#/help#${SECTION}`);

    render(<SectionProbe />);

    expect(scrollIntoView).toHaveBeenCalledWith({ block: "start" });
  });

  it("leaves focus alone without a section", () => {
    window.history.replaceState(null, "", "#/help");

    render(<SectionProbe />);

    expect(document.activeElement).toBe(document.body);
  });
});

describe("replaceEmptyAddress", () => {
  afterEach(() => {
    window.history.replaceState(null, "", "#");
  });

  it("opens the path when the address names no page", () => {
    window.history.replaceState(null, "", window.location.pathname);

    replaceEmptyAddress("/data");

    expect(window.location.hash).toBe("#/data");
  });

  it("adds no history entry", () => {
    window.history.replaceState(null, "", window.location.pathname);
    const length = window.history.length;

    replaceEmptyAddress("/data");

    expect(window.history.length).toBe(length);
  });

  it("keeps a path the address names", () => {
    window.history.replaceState(null, "", "#/");

    replaceEmptyAddress("/data");

    expect(window.location.hash).toBe("#/");
  });
});

describe("parseAddress of a path", () => {
  it("reads the path", () => {
    expect(parseAddress("/versions?class=class-1", PATHS).path).toBe("/versions");
  });

  it("reads a query parameter", () => {
    expect(parseAddress("/versions?class=class-1", PATHS).query.get("class")).toBe("class-1");
  });

  it("forwards the old data notes path to the data page", () => {
    expect(parseAddress("/data-notes", PATHS).path).toBe("/data");
  });

  it("falls back to the home page for an unknown path", () => {
    expect(parseAddress("/nowhere", PATHS).path).toBe("/");
  });
});

describe("in path mode", () => {
  beforeEach(() => {
    resetRegistryForTests();
    for (const path of PATHS) {
      registerPage({ path, title: path, icon: "", order: 0, component: () => null });
    }
  });

  afterEach(() => {
    cleanup();
    startRouter("offline");
    window.history.replaceState(null, "", "/");
  });

  function openAt(address: string, mode: "served" | "ui" = "served") {
    window.history.replaceState(null, "", address);
    startRouter(mode);
  }

  it("builds a link to a section of a page's path", () => {
    openAt("/");

    expect(toHref("/help", new URLSearchParams(), SECTION)).toBe(`/help#${SECTION}`);
  });

  it("renders the page a path with a section names", () => {
    openAt(`/help#${SECTION}`);

    render(<RouteProbe />);

    expect(screen.getByTestId("path").textContent).toBe("/help");
  });

  it("reads the section from the address", () => {
    openAt(`/help#${SECTION}`);

    render(<RouteProbe />);

    expect(screen.getByTestId("section").textContent).toBe(SECTION);
  });

  it.each(["#Bad", "#a_b"])("ignores a section %s that is not a plain id", (section) => {
    openAt(`/help${section}`);

    render(<RouteProbe />);

    expect(screen.getByTestId("section").textContent).toBe("none");
  });

  it("leaves a section of a page's path in the address", () => {
    openAt(`/help#${SECTION}`);

    expect(`${window.location.pathname}${window.location.hash}`).toBe(`/help#${SECTION}`);
  });

  it("keeps the section when it moves an old hash address", () => {
    openAt(`/#/help#${SECTION}`);

    expect(`${window.location.pathname}${window.location.hash}`).toBe(`/help#${SECTION}`);
  });

  it("re-renders when only the section changes", async () => {
    openAt("/help");
    render(<RouteProbe />);

    act(() => {
      window.location.hash = `#${SECTION}`;
    });

    expect(await screen.findByText(SECTION)).toBe(screen.getByTestId("section"));
  });

  it("builds a link to a page's path", () => {
    openAt("/");

    expect(toHref("/cases", new URLSearchParams({ class: "class-1" }))).toBe("/cases?class=class-1");
  });

  it("routes on the hash for a served page saved and opened from disk", () => {
    window.history.replaceState(null, "", "/");
    startRouter("served", "file:");

    expect(toHref("/cases", new URLSearchParams())).toBe("#/cases");
  });

  it("routes on the hash for an offline page, even one served over http", () => {
    window.history.replaceState(null, "", "/");
    startRouter("offline", "http:");

    expect(toHref("/cases", new URLSearchParams())).toBe("#/cases");
  });

  it("builds a link to a page's path in the ui app", () => {
    openAt("/", "ui");

    expect(toHref("/cases", new URLSearchParams())).toBe("/cases");
  });

  it("renders the page the path names", () => {
    openAt("/versions?class=class-1");

    render(<RouteProbe />);

    expect(screen.getByTestId("path").textContent).toBe("/versions");
  });

  it("reads the query from the address", () => {
    openAt("/versions?class=class-1");

    render(<RouteProbe />);

    expect(screen.getByTestId("query").textContent).toBe("class=class-1");
  });

  it("renders the home page for an unknown path", () => {
    openAt("/nowhere");

    render(<RouteProbe />);

    expect(screen.getByTestId("path").textContent).toBe("/");
  });

  it("leaves an unknown path in the address", () => {
    openAt("/nowhere");

    render(<RouteProbe />);

    expect(window.location.pathname).toBe("/nowhere");
  });

  it("navigates to another page's path with a fresh query", async () => {
    openAt("/versions?dangerous=1");
    render(<RouteProbe />);

    await userEvent.click(screen.getByRole("button", { name: "Open cases" }));

    expect(`${window.location.pathname}${window.location.search}`).toBe("/cases?q=DT-1");
  });

  it("adds a history entry when it navigates", async () => {
    openAt("/versions");
    const length = window.history.length;
    render(<RouteProbe />);

    await userEvent.click(screen.getByRole("button", { name: "Open cases" }));

    expect(window.history.length).toBe(length + 1);
  });

  it("re-renders when it navigates", async () => {
    openAt("/versions");
    render(<RouteProbe />);

    await userEvent.click(screen.getByRole("button", { name: "Open cases" }));

    expect(screen.getByTestId("path").textContent).toBe("/cases");
  });

  it("adds a query parameter and keeps the others", async () => {
    openAt("/versions?dangerous=1");
    render(<RouteProbe />);

    await userEvent.click(screen.getByRole("button", { name: "Pick class" }));

    expect(window.location.search).toBe("?dangerous=1&class=class-1");
  });

  it("changes the query in place, adding no history entry", async () => {
    openAt("/versions");
    const length = window.history.length;
    render(<RouteProbe />);

    await userEvent.click(screen.getByRole("button", { name: "Pick class" }));

    expect(window.history.length).toBe(length);
  });

  it("re-renders after a query change", async () => {
    openAt("/versions");
    render(<RouteProbe />);

    await userEvent.click(screen.getByRole("button", { name: "Pick class" }));

    expect(screen.getByTestId("query").textContent).toBe("class=class-1");
  });

  it("re-renders when the browser moves through its history", async () => {
    openAt("/versions");
    render(<RouteProbe />);

    act(() => {
      window.history.pushState(null, "", "/cases");
      window.dispatchEvent(new PopStateEvent("popstate"));
    });

    expect(await screen.findByText("/cases")).toBe(screen.getByTestId("path"));
  });

  it("moves an old hash address to its path", () => {
    openAt("/#/versions?class=x");

    expect(`${window.location.pathname}${window.location.search}`).toBe("/versions?class=x");
  });

  it("leaves no hash after moving an old hash address", () => {
    openAt("/#/versions?class=x");

    expect(window.location.hash).toBe("");
  });

  it("moves an old hash address without adding a history entry", () => {
    const length = window.history.length;

    openAt("/#/versions");

    expect(window.history.length).toBe(length);
  });

  it("moves an old hash address typed over the open page to its path", async () => {
    openAt("/");
    render(<RouteProbe />);

    act(() => {
      window.location.hash = "#/cases?q=x";
    });

    expect(await screen.findByText("/cases")).toBe(screen.getByTestId("path"));
  });

  it.each(["#//example.com/x", "#/\\example.com"])("keeps a crafted hash %s on this page on load", (hash) => {
    openAt(`/${hash}`);

    expect(`${window.location.pathname}${window.location.hash}`).toBe("/");
  });

  it.each(["#//example.com/x", "#/\\example.com"])("opens the home page for a crafted hash %s on load", (hash) => {
    openAt(`/${hash}`);

    render(<RouteProbe />);

    expect(screen.getByTestId("path").textContent).toBe("/");
  });

  it.each(["#//example.com/x", "#/\\example.com"])(
    "keeps a crafted hash %s typed over the open page on this page",
    async (hash) => {
      openAt("/cases");
      render(<RouteProbe />);

      act(() => {
        window.location.hash = hash;
      });

      // The browser fires hashchange in a task of its own, after this one.
      await waitFor(() => expect(`${window.location.pathname}${window.location.hash}`).toBe("/"));
    },
  );

  it("adds no history entry for a link to the address already open", async () => {
    openAt("/cases?q=DT-1");
    const length = window.history.length;
    render(<RouteProbe />);

    await userEvent.click(screen.getByRole("button", { name: "Open cases" }));

    expect(window.history.length).toBe(length);
  });

  it("leaves a hash that names no route alone", () => {
    openAt("/cases#main-content");

    expect(`${window.location.pathname}${window.location.hash}`).toBe("/cases#main-content");
  });

  it("rewrites the old data notes path to the data page's, keeping the query", () => {
    openAt("/data-notes?class=x");

    render(<RouteProbe />);

    expect(`${window.location.pathname}${window.location.search}`).toBe("/data?class=x");
  });

  it("rewrites an old data notes hash address to the data page's path", () => {
    openAt("/#/data-notes");

    render(<RouteProbe />);

    expect(window.location.pathname).toBe("/data");
  });

  it("opens the start path when the address names no page", () => {
    openAt("/", "ui");

    replaceEmptyAddress("/data");

    expect(window.location.pathname).toBe("/data");
  });

  it("keeps a page the address names", () => {
    openAt("/cases", "ui");

    replaceEmptyAddress("/data");

    expect(window.location.pathname).toBe("/cases");
  });
});
