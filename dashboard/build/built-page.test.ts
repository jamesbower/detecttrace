// @vitest-environment node
// Checks the committed page, which is what the Python package ships.
import { readFileSync } from "node:fs";
import { describe, expect, it } from "vitest";

const PAGE = readFileSync(
  new URL("../../src/detecttrace/templates/dashboard.html", import.meta.url),
  "utf8",
);

describe("the built dashboard page", () => {
  it("keeps React's licence notice", () => {
    expect(PAGE).toMatch(/@license React\s+\*\s+react\.production\.js[\s\S]*?MIT license/);
  });

  it("keeps React DOM's licence notice", () => {
    expect(PAGE).toMatch(/@license React\s+\*\s+react-dom-client\.production\.js[\s\S]*?MIT license/);
  });
});
