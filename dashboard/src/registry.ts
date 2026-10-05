// The build-time extension point: a page or panel registers itself when its module loads,
// so a downstream build can add views without editing the shell.
import type * as React from "react";

export type PageProps = { view: unknown; results: unknown }; // tightened in a later task
export type PanelProps = PageProps;
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

export function registerPage(page: PageDef): void {
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
