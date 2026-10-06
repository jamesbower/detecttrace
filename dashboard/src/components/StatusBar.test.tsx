import { cleanup, render, screen } from "@testing-library/react";
import { afterEach, expect, it } from "vitest";

import { StatusBar } from "./StatusBar";

afterEach(() => {
  cleanup();
});

it("is a polite live region", () => {
  render(<StatusBar />);

  expect(screen.getByRole("status").getAttribute("aria-live")).toBe("polite");
});

it("starts empty", () => {
  render(<StatusBar />);

  expect(screen.getByRole("status").textContent).toBe("");
});
