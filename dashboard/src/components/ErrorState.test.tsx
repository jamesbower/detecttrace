import { cleanup, render, screen } from "@testing-library/react";
import { afterEach, expect, it } from "vitest";

import { ErrorState } from "./ErrorState";

afterEach(() => {
  cleanup();
});

it("announces the message as an alert", () => {
  render(<ErrorState message='The "dt-view" data block is empty.' />);

  expect(screen.getByRole("alert").textContent).toContain('The "dt-view" data block is empty.');
});
