import { expect, it } from "vitest";

import { computeWindow, ROW_HEIGHT_PX, toRowIndex, toWindowParts, WINDOW_THRESHOLD } from "./case-window";

const VIEWPORT_PX = 10 * ROW_HEIGHT_PX;

it("draws every row up to the threshold", () => {
  expect(computeWindow(WINDOW_THRESHOLD, [], 0, VIEWPORT_PX)).toEqual({
    start: 0,
    end: WINDOW_THRESHOLD,
    topPx: 0,
    bottomPx: 0,
  });
});

it("draws only the rows near the top above the threshold", () => {
  expect(computeWindow(5000, [], 0, VIEWPORT_PX).end).toBe(19);
});

it("keeps the scrollbar's length with a bottom spacer", () => {
  expect(computeWindow(5000, [], 0, VIEWPORT_PX).bottomPx).toBe((5000 - 19) * ROW_HEIGHT_PX);
});

it("starts the window a few rows above the scroll position", () => {
  expect(computeWindow(5000, [], 1000 * ROW_HEIGHT_PX, VIEWPORT_PX).start).toBe(992);
});

it("places the top spacer where the first drawn row starts", () => {
  expect(computeWindow(5000, [], 1000 * ROW_HEIGHT_PX, VIEWPORT_PX).topPx).toBe(992 * ROW_HEIGHT_PX);
});

it("counts an opened detail above the window in the top spacer", () => {
  const extents = [{ position: 10, heightPx: 300 }];

  expect(computeWindow(5000, extents, 1000 * ROW_HEIGHT_PX + 300, VIEWPORT_PX).topPx).toBe(
    992 * ROW_HEIGHT_PX + 300,
  );
});

it("keeps an opened row drawn while its detail fills the viewport", () => {
  const extents = [{ position: 500, heightPx: 2000 }];

  expect(computeWindow(5000, extents, 501 * ROW_HEIGHT_PX + 1500, VIEWPORT_PX).start).toBe(492);
});

it("never starts past the last row", () => {
  expect(computeWindow(5000, [], 10_000 * ROW_HEIGHT_PX, VIEWPORT_PX).end).toBe(5000);
});

it("draws a pinned row above the window in its place, with spacers either side", () => {
  const range = computeWindow(5000, [], 2500 * ROW_HEIGHT_PX, VIEWPORT_PX);

  expect(toWindowParts(range, 5000, [], 10)).toEqual([
    { kind: "spacer", heightPx: 10 * ROW_HEIGHT_PX },
    { kind: "rows", start: 10, end: 11 },
    { kind: "spacer", heightPx: (range.start - 11) * ROW_HEIGHT_PX },
    { kind: "rows", start: range.start, end: range.end },
    { kind: "spacer", heightPx: range.bottomPx },
  ]);
});

it("draws a pinned row below the window in its place, with spacers either side", () => {
  const range = computeWindow(5000, [], 0, VIEWPORT_PX);

  expect(toWindowParts(range, 5000, [], 4000)).toEqual([
    { kind: "rows", start: 0, end: range.end },
    { kind: "spacer", heightPx: (4000 - range.end) * ROW_HEIGHT_PX },
    { kind: "rows", start: 4000, end: 4001 },
    { kind: "spacer", heightPx: 999 * ROW_HEIGHT_PX },
  ]);
});

it("draws a pinned row inside the window only once", () => {
  const range = computeWindow(5000, [], 0, VIEWPORT_PX);

  expect(toWindowParts(range, 5000, [], 3)).toEqual([
    { kind: "rows", start: 0, end: range.end },
    { kind: "spacer", heightPx: range.bottomPx },
  ]);
});

it("counts the header and each opened detail above a row in its row index", () => {
  expect(toRowIndex(10, [{ position: 2, heightPx: 300 }, { position: 12, heightPx: 300 }])).toBe(13);
});
