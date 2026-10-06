import { StrictMode } from "react";
import { createRoot } from "react-dom/client";

// The global styles come first, so each component's styles follow them in the bundle.
import "./tokens.css";
import "./base.css";
import { App } from "./App";
import { isPageDataError, isWaitingData, readPageData } from "./data";
import "./pages";
import { DATA_PATH, replaceEmptyHash } from "./router";

const root = document.getElementById("root");
if (root === null) {
  throw new Error('The page has no element with id "root".');
}
const data = readPageData(document);
// A fresh ui app has nothing to show until the reader uploads, so it opens on the Data page.
if (!isPageDataError(data) && isWaitingData(data) && data.view.mode === "ui") {
  replaceEmptyHash(DATA_PATH);
}
createRoot(root).render(
  <StrictMode>
    <App data={data} />
  </StrictMode>,
);
