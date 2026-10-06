import { expect, it } from "vitest";

import { listPages } from "../registry";
import "./index";

it("registers the built-in pages in navigation order", () => {
  expect(listPages().map((page) => page.path)).toEqual([
    "/",
    "/versions",
    "/skipped",
    "/trends",
    "/verdicts",
    "/cases",
    "/data-notes",
    "/limits",
  ]);
});
