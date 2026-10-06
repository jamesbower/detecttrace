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

// A renamed page's old path forwards to its new one, so old links and bookmarks keep working.
const PATH_ALIASES: ReadonlyMap<string, string> = new Map([["/data-notes", DATA_PATH]]);

type RouteMode = "hash" | "path";

// Chosen once, by startRouter, before the first render.
let routeMode: RouteMode = "hash";

export type ParsedAddress = { readonly path: string; readonly query: URLSearchParams };

export type Route = ParsedAddress & {
  /** Opens another page, adding a history entry. */
  navigate: (path: string, query?: Readonly<Record<string, string>>) => void;
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

/** The route an address names: a hash such as `#/cases?q=x`, or a path such as `/cases?q=x`.
 * An old path forwards to its new one; a path no page is registered at falls back to the home
 * page. */
export function parseAddress(address: string, knownPaths: readonly string[]): ParsedAddress {
  const { rawPath, search } = splitAddress(address);
  const trimmed = rawPath.length > 1 && rawPath.endsWith("/") ? rawPath.slice(0, -1) : rawPath;
  const path = PATH_ALIASES.get(trimmed) ?? trimmed;
  return { path: knownPaths.includes(path) ? path : HOME_PATH, query: new URLSearchParams(search) };
}

/** The link to a page: its hash on a page opened from disk, its path on a served one. */
export function toHref(path: string, query: URLSearchParams): string {
  const search = query.toString();
  const address = search === "" ? path : `${path}?${search}`;
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
      window.history.replaceState(window.history.state, "", toHref(parsed.path, parsed.query));
    }
  }, [address, parsed]);

  return { ...parsed, navigate, setQuery };
}

/** A page link's click handler. On a path-routed page, a plain click opens the page in place
 * instead of loading it again; a click with a modifier key or another button is left to the
 * browser, to open a new tab or window. A hash link needs no help. */
export function followLink(event: React.MouseEvent<HTMLAnchorElement>, path: string, query: URLSearchParams): void {
  const isPlainClick = event.button === 0 && !event.metaKey && !event.ctrlKey && !event.shiftKey && !event.altKey;
  if (routeMode === "hash" || event.defaultPrevented || !isPlainClick) {
    return;
  }
  event.preventDefault();
  navigate(path, query);
}

function navigate(path: string, query: Readonly<Record<string, string>> | URLSearchParams = {}): void {
  const href = toHref(path, new URLSearchParams(query));
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
  window.history.replaceState(window.history.state, "", toHref(current.path, query));
  notifyAddressChange();
}

function splitAddress(address: string): { rawPath: string; search: string } {
  const body = address.startsWith("#") ? address.slice(1) : address;
  const queryStart = body.indexOf("?");
  return queryStart === -1
    ? { rawPath: body, search: "" }
    : { rawPath: body.slice(0, queryStart), search: body.slice(queryStart + 1) };
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

// Replaced, not pushed: Back should not land on the old address and forward again.
function forwardHashRoute(): void {
  if (window.location.hash.startsWith("#/")) {
    window.history.replaceState(window.history.state, "", window.location.hash.slice(1));
  }
}

function listKnownPaths(): string[] {
  return listPages().map((page) => page.path);
}

function readAddress(): string {
  return routeMode === "hash" ? window.location.hash : `${window.location.pathname}${window.location.search}`;
}
