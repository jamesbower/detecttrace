import { useRef } from "react";

import { Sidebar } from "./Sidebar";
import { StatusBar } from "./StatusBar";
import "./Shell.css";

import type * as React from "react";
import type { PageDef } from "../registry";
import type { ServedView } from "../view";

type ShellProps = {
  pages: readonly PageDef[];
  currentPath: string;
  classAnchor?: string | null;
  /** Set only on a page from `detecttrace serve`. */
  served?: ServedView | null;
  children: React.ReactNode;
};

export const MAIN_ID = "main-content";

export function Shell({ pages, currentPath, classAnchor = null, served = null, children }: ShellProps) {
  const mainRef = useRef<HTMLElement>(null);

  // The hash holds the route, so following the link's own #main-content would leave the page.
  function handleSkip(event: React.MouseEvent<HTMLAnchorElement>) {
    event.preventDefault();
    mainRef.current?.focus();
  }

  return (
    <div className="shell">
      <a className="skip-link" href={`#${MAIN_ID}`} onClick={handleSkip}>
        Skip to content
      </a>
      <Sidebar pages={pages} currentPath={currentPath} classAnchor={classAnchor} />
      <main id={MAIN_ID} className="shell-main" ref={mainRef} tabIndex={-1}>
        <div className="shell-corner" aria-hidden="true" />
        {served !== null && served.held_back_text !== null && <p className="shell-note">{served.held_back_text}</p>}
        {children}
        <StatusBar served={served} />
      </main>
    </div>
  );
}
