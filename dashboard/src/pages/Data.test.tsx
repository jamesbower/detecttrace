import { cleanup, render, screen } from "@testing-library/react";
import { afterEach, describe, expect, it } from "vitest";

import { registerPanel, resetRegistryForTests } from "../registry";
import { DEMO_RESULTS, DEMO_VIEW } from "../test-fixtures";
import { Data } from "./Data";

import type { View } from "../view";

const VIEW_WITH_NOTES: View = {
  ...DEMO_VIEW,
  coverage: [
    {
      text: "150 of 201 verdicts matched a trace (75%).",
      hint: "Check that detecttrace.yaml names the right trace folder.",
      is_low: true,
    },
  ],
  notes: [
    {
      severity: "invalid_input",
      severity_label: "Invalid input",
      count_text: "37",
      message: "verdict rows have an unknown label.",
      hint: "Add the labels to label_map in detecttrace.yaml.",
      examples: [
        { subject: "DT-IT-0001", detail: "label 'maybe'" },
        { subject: "</script><img src=x onerror=alert(1)>", detail: null },
      ],
      more_text: "2 of 37 shown.",
    },
    {
      severity: "warning",
      severity_label: "Warning",
      count_text: "3",
      message: "traces have no case ID.",
      hint: "",
      examples: [],
      more_text: null,
    },
  ],
};

afterEach(() => {
  cleanup();
  resetRegistryForTests();
});

describe("the demo", () => {
  it("says no input problems were found", () => {
    render(<Data view={DEMO_VIEW} results={DEMO_RESULTS} />);

    expect(screen.queryByText("No input problems were found.")).not.toBeNull();
  });

  it("lists the coverage lines", () => {
    render(<Data view={DEMO_VIEW} results={DEMO_RESULTS} />);

    expect(screen.getAllByRole("listitem").map((item) => item.textContent)).toEqual([
      "201 of 201 verdicts matched a trace (100%).",
      "201 of 201 traces matched a verdict (100%).",
    ]);
  });
});

describe("mode", () => {
  it("marks the coverage list with the page's mode", () => {
    render(<Data view={{ ...DEMO_VIEW, mode: "ui" }} results={DEMO_RESULTS} />);

    expect(screen.getByRole("list", { name: "Coverage" }).dataset.mode).toBe("ui");
  });
});

describe("notes", () => {
  it("shows each note's severity label", () => {
    render(<Data view={VIEW_WITH_NOTES} results={DEMO_RESULTS} />);

    expect(screen.getAllByText(/^(Invalid input|Warning)$/).map((label) => label.textContent)).toEqual([
      "Invalid input",
      "Warning",
    ]);
  });

  it("titles each note with its count and message", () => {
    render(<Data view={VIEW_WITH_NOTES} results={DEMO_RESULTS} />);

    expect(screen.getAllByRole("heading", { level: 2 }).map((heading) => heading.textContent)).toEqual([
      "37 verdict rows have an unknown label.",
      "3 traces have no case ID.",
    ]);
  });

  it("shows a note's fix hint", () => {
    render(<Data view={VIEW_WITH_NOTES} results={DEMO_RESULTS} />);

    expect(screen.queryByText("Add the labels to label_map in detecttrace.yaml.")).not.toBeNull();
  });

  it("lists a note's examples with their details", () => {
    render(<Data view={VIEW_WITH_NOTES} results={DEMO_RESULTS} />);

    expect(screen.getByRole("list", { name: "Examples" }).textContent).toBe(
      "DT-IT-0001: label 'maybe'</script><img src=x onerror=alert(1)>2 of 37 shown.",
    );
  });

  it("says how many examples of how many are shown", () => {
    render(<Data view={VIEW_WITH_NOTES} results={DEMO_RESULTS} />);

    expect(screen.queryByText("2 of 37 shown.")).not.toBeNull();
  });

  it("renders a hostile example as text, adding no element", () => {
    render(<Data view={VIEW_WITH_NOTES} results={DEMO_RESULTS} />);

    expect(document.querySelector("img, script")).toBeNull();
  });
});

describe("coverage", () => {
  it("marks low coverage with a warning in words", () => {
    render(<Data view={VIEW_WITH_NOTES} results={DEMO_RESULTS} />);

    expect(screen.getByRole("list", { name: "Coverage" }).textContent).toBe(
      "Warning: 150 of 201 verdicts matched a trace (75%).Check that detecttrace.yaml names the right trace folder.",
    );
  });
});

it("shows the notes-after panels", () => {
  registerPanel("notes-after", () => <p>Notes panel</p>);
  render(<Data view={DEMO_VIEW} results={DEMO_RESULTS} />);

  expect(screen.queryByText("Notes panel")).not.toBeNull();
});
