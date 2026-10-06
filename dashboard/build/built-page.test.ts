// @vitest-environment node
// Checks the committed page, which is what the Python package ships.
import { readFileSync } from "node:fs";
import { describe, expect, it } from "vitest";

const PAGE = readFileSync(
  new URL("../../src/detecttrace/templates/dashboard.html", import.meta.url),
  "utf8",
);

// About 25% above the page's 303,000 bytes in October 2026, with its data blocks empty: room to
// grow, but a new dependency or an inlined asset fails here first.
const PAGE_BUDGET_BYTES = 380_000;

describe("the built dashboard page", () => {
  it("stays within its size budget", () => {
    expect(Buffer.byteLength(PAGE, "utf8")).toBeLessThanOrEqual(PAGE_BUDGET_BYTES);
  });

  it("keeps React's licence notice", () => {
    expect(PAGE).toMatch(/@license React\s+\*\s+react\.production\.js[\s\S]*?MIT license/);
  });

  it("keeps React DOM's licence notice", () => {
    expect(PAGE).toMatch(/@license React\s+\*\s+react-dom-client\.production\.js[\s\S]*?MIT license/);
  });
});
