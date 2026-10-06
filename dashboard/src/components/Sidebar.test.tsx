import { cleanup, render, screen } from "@testing-library/react";
import { afterEach, expect, it } from "vitest";

import { Sidebar } from "./Sidebar";

import type { PageDef } from "../registry";

const PAGES: PageDef[] = [
  { path: "/", title: "Overview", icon: "M3 12l9-8 9 8", order: 0, component: () => null },
  { path: "/cases", title: "Cases", icon: "", order: 1, component: () => null },
];

afterEach(() => {
  cleanup();
});

it("links each page to its hash path", () => {
  render(<Sidebar pages={PAGES} currentPath="/" />);

  expect(screen.getByRole("link", { name: "Cases" }).getAttribute("href")).toBe("#/cases");
});

it("marks the current page's link", () => {
  render(<Sidebar pages={PAGES} currentPath="/cases" />);

  expect(screen.getByRole("link", { name: "Cases" }).getAttribute("aria-current")).toBe("page");
});

it("leaves other pages' links unmarked", () => {
  render(<Sidebar pages={PAGES} currentPath="/cases" />);

  expect(screen.getByRole("link", { name: "Overview" }).hasAttribute("aria-current")).toBe(false);
});

it("draws a page's icon from its path data", () => {
  render(<Sidebar pages={PAGES} currentPath="/" />);

  expect(screen.getByRole("link", { name: "Overview" }).querySelector("path")?.getAttribute("d")).toBe(
    "M3 12l9-8 9 8",
  );
});

it("labels the navigation", () => {
  render(<Sidebar pages={PAGES} currentPath="/" />);

  expect(screen.getByRole("navigation", { name: "Pages" }).tagName).toBe("NAV");
});
