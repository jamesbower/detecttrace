import { act, cleanup, render, screen } from "@testing-library/react";
import { userEvent } from "@testing-library/user-event";
import { afterEach, beforeEach, describe, expect, it } from "vitest";

import { registerPage, resetRegistryForTests } from "./registry";
import { parseHash, replaceEmptyHash, toHash, useRoute } from "./router";

const PATHS = ["/", "/versions", "/cases", "/data"];

function RouteProbe() {
  const route = useRoute();
  return (
    <>
      <p data-testid="path">{route.path}</p>
      <p data-testid="query">{route.query.toString()}</p>
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

describe("parseHash", () => {
  it("reads the path", () => {
    expect(parseHash("#/versions?class=class-1", PATHS).path).toBe("/versions");
  });

  it("reads a query parameter", () => {
    expect(parseHash("#/versions?class=class-1&q=a+b", PATHS).query.get("q")).toBe("a b");
  });

  it("treats an empty hash as the home page", () => {
    expect(parseHash("", PATHS).path).toBe("/");
  });

  it("treats a bare # as the home page", () => {
    expect(parseHash("#", PATHS).path).toBe("/");
  });

  it("ignores a trailing slash", () => {
    expect(parseHash("#/versions/", PATHS).path).toBe("/versions");
  });

  it("falls back to the home page for an unknown path", () => {
    expect(parseHash("#/nowhere?class=class-1", PATHS).path).toBe("/");
  });

  it("keeps the query when the path is unknown", () => {
    expect(parseHash("#/nowhere?class=class-1", PATHS).query.get("class")).toBe("class-1");
  });

  it("forwards the old data notes path to the data page", () => {
    expect(parseHash("#/data-notes?class=x", PATHS).path).toBe("/data");
  });

  it("keeps the query when forwarding an old path", () => {
    expect(parseHash("#/data-notes?class=x", PATHS).query.get("class")).toBe("x");
  });
});

describe("toHash", () => {
  it("leaves out an empty query", () => {
    expect(toHash("/cases", new URLSearchParams())).toBe("#/cases");
  });

  it("round-trips through parseHash", () => {
    const hash = toHash("/cases", new URLSearchParams({ q: "a&b c" }));

    expect(parseHash(hash, PATHS).query.get("q")).toBe("a&b c");
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

describe("replaceEmptyHash", () => {
  afterEach(() => {
    window.history.replaceState(null, "", "#");
  });

  it("opens the path when the address names no page", () => {
    window.history.replaceState(null, "", window.location.pathname);

    replaceEmptyHash("/data");

    expect(window.location.hash).toBe("#/data");
  });

  it("keeps a path the address names", () => {
    window.history.replaceState(null, "", "#/");

    replaceEmptyHash("/data");

    expect(window.location.hash).toBe("#/");
  });
});
