import { useEffect, useRef } from "react";

import { ErrorBoundary } from "./components/ErrorBoundary";
import { ErrorState } from "./components/ErrorState";
import { MAIN_ID, Shell } from "./components/Shell";
import { WaitingState } from "./components/WaitingState";
import { isPageDataError, isWaitingData } from "./data";
import { listPages } from "./registry";
import { useRoute } from "./router";

import type { PageData, PageDataError, WaitingData } from "./data";

type AppProps = { data: PageData | WaitingData | PageDataError };

/** The dashboard. `data` is read once, before the first render, by the entry point. A render
 * failure anywhere, the shell included, shows an alert instead of a blank page. */
export function App({ data }: AppProps) {
  return (
    <ErrorBoundary subject="The dashboard">
      <Dashboard data={data} />
    </ErrorBoundary>
  );
}

function Dashboard({ data }: AppProps) {
  const { path, query } = useRoute();
  const isWaiting = !isPageDataError(data) && isWaitingData(data);
  useAnnouncePage(path, isWaiting);

  if (isPageDataError(data)) {
    return <ErrorState message={data.error} />;
  }

  // Every page needs results, so a waiting page links to none of them.
  if (isWaitingData(data)) {
    return (
      <Shell header={data.view.header} pages={[]} currentPath={path} served={data.view.served}>
        <WaitingState waiting={data.waiting} />
      </Shell>
    );
  }

  const pages = listPages();
  const Page = pages.find((page) => page.path === path)?.component;
  return (
    <Shell
      header={data.view.header}
      pages={pages}
      currentPath={path}
      classAnchor={query.get("class")}
      served={data.view.served}
    >
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
const WAITING_TITLE = "Waiting for data";

// A new page names itself in the tab title and takes focus at its heading, as a page load
// would for a screen reader. The first page keeps focus where the browser put it.
function useAnnouncePage(path: string, isWaiting: boolean): void {
  const shownPath = useRef<string | null>(null);
  useEffect(() => {
    const title = isWaiting ? WAITING_TITLE : listPages().find((candidate) => candidate.path === path)?.title;
    if (title !== undefined) {
      document.title = `${title} · ${SITE_TITLE}`;
    }
    if (shownPath.current !== null && shownPath.current !== path) {
      document.getElementById(MAIN_ID)?.querySelector<HTMLElement>("h1")?.focus();
    }
    shownPath.current = path;
  }, [path, isWaiting]);
}
