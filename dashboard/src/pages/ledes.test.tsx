// Each page's lede keeps the sentences of the single-file page it replaces.
import { cleanup, render, screen } from "@testing-library/react";
import { afterEach, expect, it } from "vitest";

import { DEMO_RESULTS, DEMO_VIEW } from "../test-fixtures";
import { Cases } from "./Cases";
import { Data } from "./Data";
import { SkippedSteps } from "./SkippedSteps";
import { VerdictMatrix } from "./VerdictMatrix";
import { WeeklyTrend } from "./WeeklyTrend";

import type { PageProps } from "../registry";
import type * as React from "react";

afterEach(cleanup);

it.each<[string, React.ComponentType<PageProps>, string]>([
  ["Cases", Cases, "Filters apply to all 201 cases."],
  ["Data", Data, "Up to three examples each."],
  ["Skipped steps", SkippedSteps, "Read across a row to compare versions."],
  ["Verdict matrix", VerdictMatrix, "Cells with any such case are also flagged."],
  [
    "Weekly trend",
    WeeklyTrend,
    "A dashed rule marks the first week of each version. A version that appears again later is a rollback.",
  ],
])("the %s lede says it", (_, Page, sentence) => {
  render(<Page view={DEMO_VIEW} results={DEMO_RESULTS} />);

  expect(screen.queryByText((text) => text.includes(sentence))).not.toBeNull();
});
