import { StrictMode } from "react";
import { createRoot } from "react-dom/client";

import { App } from "./App";
import "./tokens.css";
import "./base.css";
import "./pages";

const root = document.getElementById("root");
if (root === null) {
  throw new Error('The page has no element with id "root".');
}
createRoot(root).render(
  <StrictMode>
    <App />
  </StrictMode>,
);
