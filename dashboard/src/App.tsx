import { useEffect, useRef } from "react";

import { ErrorBoundary } from "./components/ErrorBoundary";
import { ErrorState } from "./components/ErrorState";
import { MAIN_ID, Shell } from "./components/Shell";
import { WaitingState } from "./components/WaitingState";
import { isPageDataError, isWaitingData } from "./data";
import { Data } from "./pages/Data";
import { Help } from "./pages/Help";
import { listPages } from "./registry";
import { DATA_PATH, HELP_PATH, useRoute, useSectionFocus } from "./router";

import type * as React from "react";
import type { PageData, PageDataError, WaitingData } from "./data";
import type { View } from "./view";

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
  const PageWithoutResults = isPageDataError(data)
    ? undefined
    : findPageWithoutResults(path, data.view.mode)?.component;
  const isWaitingOnPage = isWaiting && PageWithoutResults === undefined;
  useAnnouncePage(path, isWaitingOnPage, section);
  useSectionFocus(section);

  if (isPageDataError(data)) {
    return <ErrorState message={data.error} />;
  }

  if (isWaitingData(data)) {
    // The ui app links every page, each of which says the Data page is where to start. A served
    // waiting page links only to the pages it can show.
    const mode = data.view.mode;
    const pages = isUi ? listPages() : listPages().filter((page) => findPageWithoutResults(page.path, mode));
    return (
      <Shell
        header={data.view.header}
        pages={pages}
        currentPath={path}
        served={data.view.served}
        shouldReloadOnNewData={isUi}
      >
        {PageWithoutResults === undefined ? (
          <WaitingState waiting={data.waiting} />
        ) : (
          <ErrorBoundary key={path} subject="This page">
            <PageWithoutResults view={data.view} results={null} />
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

type PageWithoutResults = {
  path: string;
  modes?: readonly View["mode"][];
  component: React.ComponentType<{ view: View; results: null }>;
};

// The pages a waiting page shows, as they need no results: Help in every mode, and the ui app's
// Data page, where the reader supplies the data.
const PAGES_WITHOUT_RESULTS: readonly PageWithoutResults[] = [
  { path: HELP_PATH, component: Help },
  { path: DATA_PATH, modes: ["ui"], component: Data },
];

function findPageWithoutResults(path: string, mode: View["mode"]): PageWithoutResults | undefined {
  return PAGES_WITHOUT_RESULTS.find((page) => page.path === path && (page.modes?.includes(mode) ?? true));
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
