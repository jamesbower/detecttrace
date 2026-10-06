import { cleanup, render, screen } from "@testing-library/react";
import { userEvent } from "@testing-library/user-event";
import { afterEach, expect, it } from "vitest";

import { Shell } from "./Shell";

afterEach(() => {
  cleanup();
  window.history.replaceState(null, "", "#");
});

it("moves focus to the main content from the skip link", async () => {
  render(<Shell pages={[]} currentPath="/">content</Shell>);

  await userEvent.click(screen.getByRole("link", { name: "Skip to content" }));

  expect(document.activeElement).toBe(screen.getByRole("main"));
});

it("keeps the route when the skip link is followed", async () => {
  window.history.replaceState(null, "", "#/cases?class=class-1");
  render(<Shell pages={[]} currentPath="/cases">content</Shell>);

  await userEvent.click(screen.getByRole("link", { name: "Skip to content" }));

  expect(window.location.hash).toBe("#/cases?class=class-1");
});

it("puts the skip link first in the tab order", async () => {
  render(<Shell pages={[]} currentPath="/">content</Shell>);

  await userEvent.tab();

  expect(document.activeElement).toBe(screen.getByRole("link", { name: "Skip to content" }));
});

it("renders the page inside the main landmark", () => {
  render(
    <Shell pages={[]} currentPath="/">
      <h1>Overview</h1>
    </Shell>,
  );

  expect(screen.getByRole("main").contains(screen.getByRole("heading", { name: "Overview" }))).toBe(true);
});
