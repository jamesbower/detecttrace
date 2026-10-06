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

const root = document.getElementById("root");
if (root === null) {
  throw new Error('The page has no element with id "root".');
}
const data = readPageData(document);
startRouter(isPageDataError(data) ? null : data.view.mode);
openStartPage(data);
createRoot(root).render(
  <StrictMode>
    <App data={data} />
  </StrictMode>,
);
