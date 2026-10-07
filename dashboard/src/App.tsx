import { useEffect, useRef } from "react";

import { ErrorBoundary } from "./components/ErrorBoundary";
import { ErrorState } from "./components/ErrorState";
import { MAIN_ID, Shell } from "./components/Shell";
import { WaitingState } from "./components/WaitingState";
import { isPageDataError, isWaitingData } from "./data";
import { Data } from "./pages/Data";
import { listPages } from "./registry";
import { DATA_PATH, useRoute, useSectionFocus } from "./router";

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
  const { path, query, section } = useRoute();
  const isWaiting = !isPageDataError(data) && isWaitingData(data);
  const isUi = !isPageDataError(data) && data.view.mode === "ui";
  // The ui app's Data page is where the reader supplies the data, so it opens without results.
  const isWaitingOnPage = isWaiting && !(isUi && path === DATA_PATH);
  useAnnouncePage(path, isWaitingOnPage, section);
  useSectionFocus(section);

  if (isPageDataError(data)) {
    return <ErrorState message={data.error} />;
  }

  if (isWaitingData(data)) {
    // Every page but the ui app's Data page needs results, so a served waiting page links to none.
    return (
      <Shell
        header={data.view.header}
        pages={isUi ? listPages() : []}
        currentPath={path}
        served={data.view.served}
        shouldReloadOnNewData={isUi}
      >
        {isWaitingOnPage ? (
          <WaitingState waiting={data.waiting} />
        ) : (
          <ErrorBoundary key={path} subject="This page">
            <Data view={data.view} results={null} />
          </ErrorBoundary>
        )}
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
      shouldReloadOnNewData={isUi}
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
// would for a screen reader. The first page keeps focus where the browser put it, and a page
// opened at a section it has leaves focus to useSectionFocus.
function useAnnouncePage(path: string, isWaiting: boolean, section: string | null): void {
  const shownPath = useRef<string | null>(null);
  useEffect(() => {
    const title = isWaiting ? WAITING_TITLE : listPages().find((candidate) => candidate.path === path)?.title;
    if (title !== undefined) {
      document.title = `${title} · ${SITE_TITLE}`;
    }
    const hasSectionTarget = section !== null && document.getElementById(section) !== null;
    if (shownPath.current !== null && shownPath.current !== path && !hasSectionTarget) {
      document.getElementById(MAIN_ID)?.querySelector<HTMLElement>("h1")?.focus();
    }
    shownPath.current = path;
  }, [path, isWaiting, section]);
}
