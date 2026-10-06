import { cleanup, render, screen } from "@testing-library/react";
import { afterEach, beforeEach, expect, it, vi } from "vitest";

import { ErrorBoundary } from "./ErrorBoundary";

function Thrower({ error }: { error: unknown }): never {
  throw error;
}

function renderFailure(error: unknown): string | null {
  render(
    <ErrorBoundary subject="This page">
      <Thrower error={error} />
    </ErrorBoundary>,
  );
  return screen.getByRole("alert").textContent;
}

beforeEach(() => {
  vi.spyOn(console, "error").mockImplementation(() => {});
});

afterEach(() => {
  cleanup();
  vi.restoreAllMocks();
});

it("gives the reason", () => {
  expect(renderFailure(new Error("bad data"))).toBe("This page could not be shown: bad data");
});

it("accepts a thrown non-error", () => {
  expect(renderFailure("boom")).toBe("This page could not be shown: boom");
});

it("shows control characters in the reason as escapes", () => {
  expect(renderFailure(new Error("a\u202eb"))).toBe("This page could not be shown: a\\u202eb");
});

it("shortens a long reason", () => {
  expect(renderFailure(new Error("x".repeat(500)))).toBe(`This page could not be shown: ${"x".repeat(119)}…`);
});
