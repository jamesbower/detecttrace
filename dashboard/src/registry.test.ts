import { beforeEach, describe, expect, it } from "vitest";

import {
  listPages,
  listPanels,
  registerPage,
  registerPanel,
  resetRegistryForTests,
} from "./registry";

import type { PageDef } from "./registry";

function NoOp() {
  return null;
}

function OtherNoOp() {
  return null;
}

function createPage(path: string, order: number): PageDef {
  return { path, title: path, icon: "", order, component: NoOp };
}

describe("registry", () => {
  beforeEach(() => {
    resetRegistryForTests();
  });

  it("refuses a second page at the same path", () => {
    registerPage(createPage("/cases", 1));

    expect(() => registerPage(createPage("/cases", 2))).toThrow('"/cases"');
  });

  it("lists pages by order, then by path", () => {
    registerPage(createPage("/b", 2));
    registerPage(createPage("/z", 1));
    registerPage(createPage("/a", 2));

    expect(listPages().map((page) => page.path)).toEqual(["/z", "/a", "/b"]);
  });

  it("lists a slot's panels in registration order", () => {
    registerPanel("case-detail", OtherNoOp);
    registerPanel("case-detail", NoOp);

    expect(listPanels("case-detail")).toEqual([OtherNoOp, NoOp]);
  });

  it("keeps each slot's panels apart", () => {
    registerPanel("case-detail", NoOp);

    expect(listPanels("trend-footer")).toEqual([]);
  });
});
