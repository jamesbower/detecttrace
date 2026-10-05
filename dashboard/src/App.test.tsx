import { cleanup, render, screen } from "@testing-library/react";
import { afterEach, beforeEach, expect, it } from "vitest";

import { App } from "./App";
import { registerPage, resetRegistryForTests } from "./registry";

beforeEach(() => {
  resetRegistryForTests();
});

afterEach(() => {
  cleanup();
});

it("names each registered page in the navigation", () => {
  registerPage({ path: "/cases", title: "Cases", icon: "", order: 1, component: () => null });

  render(<App />);

  expect(screen.getByRole("navigation", { name: "Pages" }).textContent).toBe("Cases");
});
