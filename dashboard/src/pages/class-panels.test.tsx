// Every page with a class selector names its class panel the same way: by its tab, or, above
// MAX_CLASS_TABS classes where a drop-down replaces the tabs, by the class itself.
import { cleanup, render, screen } from "@testing-library/react";
import { afterEach, describe, expect, it } from "vitest";

import { createClasses, DEMO_RESULTS, DEMO_VIEW } from "../test-fixtures";
import { Overview } from "./Overview";
import { SkippedSteps } from "./SkippedSteps";
import { Versions } from "./Versions";
import { WeeklyTrend } from "./WeeklyTrend";

import type { PageProps } from "../registry";
import type * as React from "react";

const PAGES: [string, React.ComponentType<PageProps>][] = [
  ["Overview", Overview],
  ["Versions", Versions],
  ["Skipped steps", SkippedSteps],
  ["Weekly trend", WeeklyTrend],
];
const SEVEN_CLASSES = { ...DEMO_VIEW, classes: createClasses(7) };

afterEach(cleanup);

describe.each(PAGES)("%s with seven classes", (_, Page) => {
  it("names its class panel by the selected class", () => {
    render(<Page view={SEVEN_CLASSES} results={DEMO_RESULTS} />);

    expect(screen.queryByRole("region", { name: "class_0" })).not.toBeNull();
  });

  it("has no tab panel without tabs", () => {
    render(<Page view={SEVEN_CLASSES} results={DEMO_RESULTS} />);

    expect(screen.queryByRole("tabpanel")).toBeNull();
  });
});
