import { beforeEach, describe, expect, it } from "vitest";

import {
  listHelpSections,
  listPages,
  listPanels,
  registerHelpSection,
  registerPage,
  registerPanel,
  resetRegistryForTests,
} from "./registry";

import type { HelpSection, PageDef } from "./registry";

function NoOp() {
  return null;
}

function OtherNoOp() {
  return null;
}

function createPage(path: string, order: number): PageDef {
  return { path, title: path, icon: "", order, component: NoOp };
}

function createHelpSection(id: string, order: number, modes?: HelpSection["modes"]): HelpSection {
  return { id, title: id, order, body: NoOp, modes };
}

describe("registry", () => {
  beforeEach(() => {
    resetRegistryForTests();
  });

  it("refuses a second page at the same path", () => {
    registerPage(createPage("/cases", 1));

    expect(() => registerPage(createPage("/cases", 2))).toThrow('"/cases"');
  });

  it.each(["cases", "/Cases", "/cases/open", "/case_list", "/1cases", "/api", "/cases/"])(
    "refuses a page at %s, which the servers would not answer",
    (path) => {
      expect(() => registerPage(createPage(path, 1))).toThrow(`"${path}"`);
    },
  );

  it.each(["/", "/cases", "/skipped-steps"])("accepts a page at %s", (path) => {
    registerPage(createPage(path, 1));

    expect(listPages().map((page) => page.path)).toEqual([path]);
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

  it("refuses a second help section with the same id", () => {
    registerHelpSection(createHelpSection("glossary", 1));

    expect(() => registerHelpSection(createHelpSection("glossary", 2))).toThrow('"glossary"');
  });

  it.each(["Bad", "a_b", "1a"])(
    "refuses a help section with id %s, which can't be a fragment",
    (id) => {
      expect(() => registerHelpSection(createHelpSection(id, 1))).toThrow(`"${id}"`);
    },
  );

  it("lists help sections by order, then by id", () => {
    registerHelpSection(createHelpSection("b", 2));
    registerHelpSection(createHelpSection("z", 1));
    registerHelpSection(createHelpSection("a", 2));

    expect(listHelpSections("offline").map((section) => section.id)).toEqual(["z", "a", "b"]);
  });

  it("leaves a ui-only help section out of the offline page", () => {
    registerHelpSection(createHelpSection("labelling", 1, ["ui"]));

    expect(listHelpSections("offline")).toEqual([]);
  });

  it("lists a ui-only help section in ui mode", () => {
    registerHelpSection(createHelpSection("labelling", 1, ["ui"]));

    expect(listHelpSections("ui").map((section) => section.id)).toEqual(["labelling"]);
  });
});
