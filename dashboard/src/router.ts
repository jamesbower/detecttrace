// The route and its filters live in the address, so a link or a reload keeps them. A page from
// `detecttrace serve` or `detecttrace ui` routes on the path, as `/cases?class=class-1`: its
// server answers every page path with the page. A page opened from disk has no server to ask
// for `/cases`, so it routes on the hash instead, as `#/cases?class=class-1`.
import { useEffect, useMemo, useSyncExternalStore } from "react";

import { listPages } from "./registry";

import type * as React from "react";
import type { View } from "./view";

export const HOME_PATH = "/";
export const DATA_PATH = "/data";
export const HELP_PATH = "/help";

// A section is a heading's id on the page, as in `/help#evidence-completeness`. Only a plain id
// is kept: anything else in the address could name another page or origin.
const SECTION_PATTERN = /^[a-z][a-z-]*$/;

// A renamed page's old path forwards to its new one, so old links and bookmarks keep working.
const PATH_ALIASES: ReadonlyMap<string, string> = new Map([["/data-notes", DATA_PATH]]);

type RouteMode = "hash" | "path";

// Chosen once, by startRouter, before the first render.
let routeMode: RouteMode = "hash";

export type ParsedAddress = {
  readonly path: string;
  readonly query: URLSearchParams;
  /** The id of the heading the address names on the page, if any. */
  readonly section: string | null;
};

export type Route = ParsedAddress & {
  /** Opens another page, adding a history entry. */
  navigate: (path: string, query?: Readonly<Record<string, string>>, section?: string | null) => void;
  /** Sets (or, with null, removes) query parameters on this page, keeping the others. */
  setQuery: (updates: Readonly<Record<string, string | null>>) => void;
};

/** Routes on the path for a page a server sends, on the hash otherwise. Called once, before the
 * first render. On a path-routed page, an old hash address such as `/#/cases` moves to its path
 * without a history entry, so links and bookmarks from before keep working. */
export function startRouter(viewMode: View["mode"] | null, protocol: string = window.location.protocol): void {
  // A served page saved and opened from disk has no server behind it either.
  const isFromServer = protocol === "http:" || protocol === "https:";
  routeMode = isFromServer && (viewMode === "served" || viewMode === "ui") ? "path" : "hash";
  if (routeMode === "path") {
    forwardHashRoute();
  }
}

/** The route an address names: a hash such as `#/cases?q=x#id`, or a path such as
 * `/cases?q=x#id`. An old path forwards to its new one; a path no page is registered at falls
 * back to the home page. A section that is not a plain id is ignored. */
export function parseAddress(address: string, knownPaths: readonly string[]): ParsedAddress {
  const { rawPath, search, rawSection } = splitAddress(address);
  const trimmed = rawPath.length > 1 && rawPath.endsWith("/") ? rawPath.slice(0, -1) : rawPath;
  const path = PATH_ALIASES.get(trimmed) ?? trimmed;
  return {
    path: knownPaths.includes(path) ? path : HOME_PATH,
    query: new URLSearchParams(search),
    section: SECTION_PATTERN.test(rawSection) ? rawSection : null,
  };
}

/** The link to a page, or to a section of it: its hash on a page opened from disk, as
 * `#/help#id`, its path on a served one, as `/help#id`. */
export function toHref(path: string, query: URLSearchParams, section?: string | null): string {
  const search = query.toString();
  const address = `${path}${search === "" ? "" : `?${search}`}${section == null ? "" : `#${section}`}`;
  return routeMode === "hash" ? `#${address}` : address;
}

/** Opens `path` when the address names no page at all, without a history entry. Called before
 * the first render, so the page never shows the home route first. */
export function replaceEmptyAddress(path: string): void {
  const emptyPath = routeMode === "hash" ? "" : HOME_PATH;
  if (splitAddress(readAddress()).rawPath === emptyPath) {
    window.history.replaceState(window.history.state, "", toHref(path, new URLSearchParams()));
  }
}

export function useRoute(): Route {
  const address = useSyncExternalStore(subscribeToAddress, readAddress);
  const parsed = useMemo(() => parseAddress(address, listKnownPaths()), [address]);
  useEffect(() => {
    if (PATH_ALIASES.has(splitAddress(address).rawPath)) {
      // Replaced, not pushed: Back should not land on the old path and forward again.
      window.history.replaceState(window.history.state, "", toHref(parsed.path, parsed.query, parsed.section));
    }
  }, [address, parsed]);

  return { ...parsed, navigate, setQuery };
}

/** Moves focus to the section a route names, and scrolls it into view, when the section changes
 * or the page opens at one. The section's heading takes focus with `tabIndex={-1}`. The app
 * calls it once, after the page has rendered, so a page need not. */
export function useSectionFocus(section: string | null): void {
  useEffect(() => {
    if (section !== null) {
      focusSection(section);
    }
  }, [section]);
}

/** A page link's click handler. On a path-routed page, a plain click opens the page in place
 * instead of loading it again; a click with a modifier key or another button is left to the
 * browser, to open a new tab or window. A hash link needs no help. */
export function followLink(
  event: React.MouseEvent<HTMLAnchorElement>,
  path: string,
  query: URLSearchParams,
  section?: string | null,
): void {
  const isPlainClick = event.button === 0 && !event.metaKey && !event.ctrlKey && !event.shiftKey && !event.altKey;
  if (event.defaultPrevented || !isPlainClick) {
    return;
  }
  // A link to the section already open changes no address, so no route change would move
  // focus there; a contents list relies on it doing so.
  if (section != null && toHref(path, query, section) === readAddress()) {
    event.preventDefault();
    focusSection(section);
    return;
  }
  if (routeMode === "hash") {
    return;
  }
  event.preventDefault();
  navigate(path, query, section);
}

function navigate(
  path: string,
  query: Readonly<Record<string, string>> | URLSearchParams = {},
  section?: string | null,
): void {
  const href = toHref(path, new URLSearchParams(query), section);
  if (routeMode === "hash") {
    window.location.hash = href;
    return;
  }
  // As with a hash link, following a link to the address already open adds no history entry.
  if (href !== readAddress()) {
    window.history.pushState(null, "", href);
    notifyAddressChange();
  }
}

// Reads the live address, not the last render's, so two changes in one event both apply.
function setQuery(updates: Readonly<Record<string, string | null>>): void {
  const current = parseAddress(readAddress(), listKnownPaths());
  const query = new URLSearchParams(current.query);
  for (const [key, value] of Object.entries(updates)) {
    if (value === null) {
      query.delete(key);
    } else {
      query.set(key, value);
    }
  }
  // Replaced, not pushed: Back should leave the page, not step through every filter change.
  window.history.replaceState(window.history.state, "", toHref(current.path, query, current.section));
  notifyAddressChange();
}

function focusSection(id: string): void {
  const target = document.getElementById(id);
  if (target !== null) {
    target.focus({ preventScroll: true });
    target.scrollIntoView({ block: "start" });
  }
}

// A query never holds a bare `#`: URLSearchParams writes it as `%23`.
function splitAddress(address: string): { rawPath: string; search: string; rawSection: string } {
  const body = address.startsWith("#") ? address.slice(1) : address;
  const sectionStart = body.indexOf("#");
  const route = sectionStart === -1 ? body : body.slice(0, sectionStart);
  const rawSection = sectionStart === -1 ? "" : body.slice(sectionStart + 1);
  const queryStart = route.indexOf("?");
  return queryStart === -1
    ? { rawPath: route, search: "", rawSection }
    : { rawPath: route.slice(0, queryStart), search: route.slice(queryStart + 1), rawSection };
}

// pushState and replaceState fire no event, so every subscriber is told here.
function notifyAddressChange(): void {
  window.dispatchEvent(routeMode === "hash" ? new HashChangeEvent("hashchange") : new PopStateEvent("popstate"));
}

function subscribeToAddress(onChange: () => void): () => void {
  if (routeMode === "hash") {
    window.addEventListener("hashchange", onChange);
    return () => window.removeEventListener("hashchange", onChange);
  }
  // An old hash address typed over the open page changes only the hash, so the page is not
  // loaded again and startRouter never sees it.
  function onHashChange() {
    forwardHashRoute();
    onChange();
  }
  window.addEventListener("popstate", onChange);
  window.addEventListener("hashchange", onHashChange);
  return () => {
    window.removeEventListener("popstate", onChange);
    window.removeEventListener("hashchange", onHashChange);
  };
}

// Replaced, not pushed: Back should not land on the old address and forward again. Only a hash
// naming a route moves, so a section such as `/help#id` stays. Written from the parsed route,
// never from the raw hash: `#//example.com/x` would name another origin, which replaceState
// refuses with an error that leaves the page blank. Needs the pages registered, as they are
// once the page modules have loaded.
function forwardHashRoute(): void {
  if (window.location.hash.startsWith("#/")) {
    const { path, query, section } = parseAddress(window.location.hash, listKnownPaths());
    window.history.replaceState(window.history.state, "", toHref(path, query, section));
  }
}

function listKnownPaths(): string[] {
  return listPages().map((page) => page.path);
}

// On a path-routed page the hash holds the section, so a change to it alone re-renders too.
function readAddress(): string {
  const { hash, pathname, search } = window.location;
  return routeMode === "hash" ? hash : `${pathname}${search}${hash}`;
}
