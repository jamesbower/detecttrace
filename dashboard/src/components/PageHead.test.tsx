import { cleanup, render, screen } from "@testing-library/react";
import { afterEach, expect, it } from "vitest";

import { PageHead } from "./PageHead";

afterEach(() => {
  cleanup();
});

it("titles the page with a level-one heading", () => {
  render(<PageHead eyebrow="Versions" title="By version" />);

  expect(screen.getByRole("heading", { level: 1 }).textContent).toBe("By version");
});

it("shows the eyebrow", () => {
  render(<PageHead eyebrow="Versions" title="By version" />);

  expect(screen.getByText("Versions").tagName).toBe("P");
});

it("shows the description", () => {
  render(<PageHead eyebrow="Versions" title="By version" description="Per version." />);

  expect(screen.getByText("Per version.").tagName).toBe("P");
});
