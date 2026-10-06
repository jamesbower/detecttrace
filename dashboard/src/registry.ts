// The build-time extension point: a page or panel registers itself when its module loads,
// so a custom build that imports this source can add views without editing the shell.
import type * as React from "react";

import type { Results } from "./results";
import type { View } from "./view";

export type PageProps = { view: View; results: Results };
/** A panel gets the page data, plus what its slot is about: the case in `case-detail`, the
 * class and version row in `version-row-detail`. */
export type PanelProps = PageProps & { caseId?: string; classAnchor?: string; versionLabel?: string };
export type PageDef = {
  path: string;
  title: string;
  icon: string;
  order: number;
  component: React.ComponentType<PageProps>;
};
export type PanelSlot =
  | "overview-after-kpis"
  | "version-row-detail"
  | "trend-footer"
  | "case-detail"
  | "notes-after";

const pages = new Map<string, PageDef>();
const panels = new Map<PanelSlot, React.ComponentType<PanelProps>[]>();

// What `detecttrace serve` and `detecttrace ui` answer with the page: one lowercase segment,
// not `api`, or the root. Keep in step with the page route in src/detecttrace/serve/app.py.
const PAGE_PATH = /^\/([a-z][a-z-]*)?$/;
const RESERVED_PATHS: ReadonlySet<string> = new Set(["/api"]);

export function registerPage(page: PageDef): void {
  if (!PAGE_PATH.test(page.path) || RESERVED_PATHS.has(page.path)) {
    throw new Error(
      `A page can't be registered at path "${page.path}": a page path is "/" or one segment of lowercase letters and hyphens, such as "/cases", and not "/api".`,
    );
  }
  if (pages.has(page.path)) {
    throw new Error(`A page is already registered at path "${page.path}".`);
  }
  pages.set(page.path, page);
}

export function registerPanel(slot: PanelSlot, component: React.ComponentType<PanelProps>): void {
  const registered = panels.get(slot) ?? [];
  panels.set(slot, [...registered, component]);
}

export function listPages(): readonly PageDef[] {
  return [...pages.values()].sort(
    (a, b) => a.order - b.order || (a.path < b.path ? -1 : a.path > b.path ? 1 : 0),
  );
}

export function listPanels(slot: PanelSlot): readonly React.ComponentType<PanelProps>[] {
  return panels.get(slot) ?? [];
}

/** Test only: forgets every registered page and panel. */
export function resetRegistryForTests(): void {
  pages.clear();
  panels.clear();
}
