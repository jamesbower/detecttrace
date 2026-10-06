import { useEffect, useRef } from "react";

import { ErrorBoundary } from "./components/ErrorBoundary";
import { ErrorState } from "./components/ErrorState";
import { MAIN_ID, Shell } from "./components/Shell";
import { isPageDataError } from "./data";
import { listPages } from "./registry";
import { useRoute } from "./router";

import type { PageData, PageDataError } from "./data";

/** The dashboard. `data` is read once, before the first render, by the entry point. */
export function App({ data }: { data: PageData | PageDataError }) {
  const { path } = useRoute();
  useAnnouncePage(path);

  if (isPageDataError(data)) {
    return <ErrorState message={data.error} />;
  }

  const pages = listPages();
  const Page = pages.find((page) => page.path === path)?.component;
  return (
    <Shell pages={pages} currentPath={path}>
      {Page !== undefined && (
        // Keyed by path, so opening another page clears an earlier page's error.
        <ErrorBoundary key={path} subject="This page">
          <Page view={data.view} results={data.results} />
        </ErrorBoundary>
      )}
    </Shell>
  );
}

const SITE_TITLE = "DetectTrace";

// A new page names itself in the tab title and takes focus at its heading, as a page load
// would for a screen reader. The first page keeps focus where the browser put it.
function useAnnouncePage(path: string): void {
  const shownPath = useRef<string | null>(null);
  useEffect(() => {
    const page = listPages().find((candidate) => candidate.path === path);
    if (page !== undefined) {
      document.title = `${page.title} · ${SITE_TITLE}`;
    }
    if (shownPath.current !== null && shownPath.current !== path) {
      document.getElementById(MAIN_ID)?.querySelector<HTMLElement>("h1")?.focus();
    }
    shownPath.current = path;
  }, [path]);
}
