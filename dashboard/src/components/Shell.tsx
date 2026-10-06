import { useRef } from "react";

import { Sidebar } from "./Sidebar";
import { StatusBar } from "./StatusBar";
import "./Shell.css";

import type * as React from "react";
import type { PageDef } from "../registry";
import type { HeaderView, ServedView } from "../view";

type ShellProps = {
  /** Gives the brand and the footer's text. */
  header: HeaderView;
  pages: readonly PageDef[];
  currentPath: string;
  classAnchor?: string | null;
  /** Set only on a page from `detecttrace serve`. */
  served?: ServedView | null;
  /** Set in `detecttrace ui`, where the reader caused any newer results. */
  shouldReloadOnNewData?: boolean;
  children: React.ReactNode;
};

export const MAIN_ID = "main-content";

export function Shell({
  header,
  pages,
  currentPath,
  classAnchor = null,
  served = null,
  shouldReloadOnNewData = false,
  children,
}: ShellProps) {
  const mainRef = useRef<HTMLElement>(null);

  // On a page opened from disk the hash holds the route, so following the link's own
  // #main-content would leave the page. Focus lands on the page's heading, as it does when a
  // new page opens.
  function handleSkip(event: React.MouseEvent<HTMLAnchorElement>) {
    event.preventDefault();
    const main = mainRef.current;
    (main?.querySelector<HTMLElement>("h1") ?? main)?.focus();
  }

  return (
    <div className="shell">
      <a className="skip-link" href={`#${MAIN_ID}`} onClick={handleSkip}>
        Skip to content
      </a>
      <Sidebar title={header.title} pages={pages} currentPath={currentPath} classAnchor={classAnchor} />
      <main id={MAIN_ID} className="shell-main" ref={mainRef} tabIndex={-1}>
        <div className="shell-corner" aria-hidden="true" />
        {/* The sidebar's brand is hidden on a narrow screen, so the name shows here instead. */}
        <p className="shell-brand">{header.title}</p>
        {served !== null && served.held_back_text !== null && <p className="shell-note">{served.held_back_text}</p>}
        {children}
        <StatusBar served={served} shouldReloadOnNewData={shouldReloadOnNewData} />
      </main>
      <footer className="shell-footer">
        <p>{`${header.generator_text} ${header.footer_text}`}</p>
      </footer>
    </div>
  );
}
