// Hash routing: the page is one offline file, so the route and its filters live after the `#`,
// as `#/versions?class=class-1&dangerous=1`.
import { useMemo, useSyncExternalStore } from "react";

import { listPages } from "./registry";

export const HOME_PATH = "/";

export type ParsedHash = { readonly path: string; readonly query: URLSearchParams };

export type Route = ParsedHash & {
  /** Opens another page, adding a history entry. */
  navigate: (path: string, query?: Readonly<Record<string, string>>) => void;
  /** Sets (or, with null, removes) query parameters on this page, keeping the others. */
  setQuery: (updates: Readonly<Record<string, string | null>>) => void;
};

/** The route a hash names. A path no page is registered at falls back to the home page. */
export function parseHash(hash: string, knownPaths: readonly string[]): ParsedHash {
  const body = hash.startsWith("#") ? hash.slice(1) : hash;
  const queryStart = body.indexOf("?");
  const rawPath = queryStart === -1 ? body : body.slice(0, queryStart);
  const query = new URLSearchParams(queryStart === -1 ? "" : body.slice(queryStart + 1));
  const path = rawPath.length > 1 && rawPath.endsWith("/") ? rawPath.slice(0, -1) : rawPath;
  return { path: knownPaths.includes(path) ? path : HOME_PATH, query };
}

export function toHash(path: string, query: URLSearchParams): string {
  const search = query.toString();
  return search === "" ? `#${path}` : `#${path}?${search}`;
}

export function useRoute(): Route {
  const hash = useSyncExternalStore(subscribeToHash, readHash);
  const parsed = useMemo(() => parseHash(hash, listKnownPaths()), [hash]);

  return { ...parsed, navigate, setQuery };
}

// Reads the live hash, not the last render's, so two changes in one event both apply.
function setQuery(updates: Readonly<Record<string, string | null>>): void {
  const current = parseHash(readHash(), listKnownPaths());
  const query = new URLSearchParams(current.query);
  for (const [key, value] of Object.entries(updates)) {
    if (value === null) {
      query.delete(key);
    } else {
      query.set(key, value);
    }
  }
  // Replaced, not pushed: Back should leave the page, not step through every filter change.
  window.history.replaceState(window.history.state, "", toHash(current.path, query));
  // replaceState fires no hashchange, so every subscriber is told here.
  window.dispatchEvent(new HashChangeEvent("hashchange"));
}

function navigate(path: string, query: Readonly<Record<string, string>> = {}): void {
  window.location.hash = toHash(path, new URLSearchParams(query));
}

function subscribeToHash(onChange: () => void): () => void {
  window.addEventListener("hashchange", onChange);
  return () => window.removeEventListener("hashchange", onChange);
}

function listKnownPaths(): string[] {
  return listPages().map((page) => page.path);
}

function readHash(): string {
  return window.location.hash;
}
