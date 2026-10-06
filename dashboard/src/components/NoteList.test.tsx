import { cleanup, render, screen } from "@testing-library/react";
import { afterEach, expect, it } from "vitest";

import { NoteList } from "./NoteList";

import type { NoteView } from "../view";

const NOTE: NoteView = {
  severity: "warning",
  severity_label: "Warning",
  count_text: "3",
  message: "traces have no case ID.",
  hint: "",
  examples: [],
  more_text: null,
};

afterEach(cleanup);

it("titles each note with a second-level heading by default", () => {
  render(<NoteList notes={[NOTE]} />);

  expect(screen.getByRole("heading", { level: 2 }).textContent).toBe("3 traces have no case ID.");
});

it("titles each note at the level it is given", () => {
  render(<NoteList notes={[NOTE]} headingLevel={4} />);

  expect(screen.getByRole("heading", { level: 4 }).textContent).toBe("3 traces have no case ID.");
});
