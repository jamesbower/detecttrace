import { cleanup, render } from "@testing-library/react";
import { afterEach, expect, it } from "vitest";

import { SeriesSwatch } from "./SeriesSwatch";

afterEach(cleanup);

it("draws all versions as the broad band", () => {
  const { container } = render(<SeriesSwatch style="all" />);

  expect(container.querySelector(".series-line-all")).not.toBeNull();
});

it("draws all versions without a marker", () => {
  const { container } = render(<SeriesSwatch style="all" />);

  expect(container.querySelector(".series-marker")).toBeNull();
});
