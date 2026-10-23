import { StrictMode } from "react";
import { createRoot } from "react-dom/client";

// The global styles come first, so each component's styles follow them in the bundle.
import "./tokens.css";
import "./base.css";
import { App } from "./App";
import { isPageDataError, readPageData } from "./data";
import "./pages";
import { startRouter } from "./router";
import { openStartPage } from "./start-page";

import type { Root } from "react-dom/client";

/** Read the page data and render the dashboard into the page's #root. A custom build imports
 * this source, registers its own pages and panels, then calls this. */
export function mountDashboard(): Root {
  const element = document.getElementById("root");
  if (element === null) {
    throw new Error('The page has no element with id "root".');
  }
  const data = readPageData(document);
  startRouter(isPageDataError(data) ? null : data.view.mode);
  openStartPage(data);
  const root = createRoot(element);
  root.render(
    <StrictMode>
      <App data={data} />
    </StrictMode>,
  );
  return root;
}
