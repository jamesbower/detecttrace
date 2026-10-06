import { describe, expect, it } from "vitest";

import { findDisallowed, isAllowedLicence } from "./check-licences.mjs";

describe("isAllowedLicence", () => {
  it("allows a listed licence", () => {
    expect(isAllowedLicence("MIT")).toBe(true);
  });

  it("refuses an unlisted licence", () => {
    expect(isAllowedLicence("GPL-3.0-only")).toBe(false);
  });

  it("refuses a missing licence", () => {
    expect(isAllowedLicence(undefined)).toBe(false);
  });

  it("allows a choice that includes a listed licence", () => {
    expect(isAllowedLicence("(MIT OR GPL-3.0-only)")).toBe(true);
  });

  it("refuses a combination that includes an unlisted licence", () => {
    expect(isAllowedLicence("MIT AND GPL-3.0-only")).toBe(false);
  });

  it("refuses a nested expression for review by hand", () => {
    expect(isAllowedLicence("(MIT OR (Apache-2.0 AND GPL-3.0-only))")).toBe(false);
  });
});

describe("findDisallowed", () => {
  it("names each nested package with a disallowed licence once", () => {
    const tree = {
      dependencies: {
        a: {
          name: "a",
          version: "1.0.0",
          path: "/a",
          dependencies: { c: { name: "c", version: "2.0.0", path: "/c" } },
        },
        b: {
          name: "b",
          version: "1.0.0",
          path: "/b",
          dependencies: { c: { name: "c", version: "2.0.0", path: "/c" } },
        },
      },
    };
    const licences = { "/a": "MIT", "/b": "ISC", "/c": "GPL-3.0-only" };

    expect(findDisallowed(tree, (path) => licences[path])).toEqual(["c@2.0.0: GPL-3.0-only"]);
  });
});
