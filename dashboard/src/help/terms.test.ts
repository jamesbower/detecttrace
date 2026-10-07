import { describe, expect, it } from "vitest";

import { TERMS } from "./terms";

const ANCHORS = Object.values(TERMS).map((term) => term.anchor);

describe("TERMS", () => {
  it("gives every term its own anchor", () => {
    expect(new Set(ANCHORS).size).toBe(ANCHORS.length);
  });

  it.each(ANCHORS)("uses %s, a lowercase-and-hyphens anchor", (anchor) => {
    expect(anchor).toMatch(/^[a-z][a-z-]*$/);
  });
});
