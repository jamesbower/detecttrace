import { ErrorState } from "./components/ErrorState";
import { Shell } from "./components/Shell";
import { isPageDataError } from "./data";
import { listPages } from "./registry";
import { useRoute } from "./router";

import type { PageData, PageDataError } from "./data";

/** The dashboard. `data` is read once, before the first render, by the entry point. */
export function App({ data }: { data: PageData | PageDataError }) {
  const { path } = useRoute();

  if (isPageDataError(data)) {
    return <ErrorState message={data.error} />;
  }

  const pages = listPages();
  const Page = pages.find((page) => page.path === path)?.component;
  return (
    <Shell pages={pages} currentPath={path}>
      {Page !== undefined && <Page view={data.view} results={data.results} />}
    </Shell>
  );
}
