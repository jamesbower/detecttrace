import { expect, it } from "vitest";

import { computeWindow, ROW_HEIGHT_PX, toRowIndex, toWindowParts, WINDOW_THRESHOLD } from "./case-window";

const VIEWPORT_PX = 10 * ROW_HEIGHT_PX;

it("draws every row up to the threshold", () => {
  expect(computeWindow(WINDOW_THRESHOLD, [], 0, VIEWPORT_PX)).toEqual({
    start: 0,
    end: WINDOW_THRESHOLD,
  });
});

it("draws only the rows near the top above the threshold", () => {
  expect(computeWindow(5000, [], 0, VIEWPORT_PX).end).toBe(19);
});

it("keeps the scrollbar's length with a bottom spacer", () => {
  const range = computeWindow(5000, [], 0, VIEWPORT_PX);

  expect(toWindowParts(range, 5000, [], null).at(-1)).toEqual({ kind: "spacer", heightPx: (5000 - 19) * ROW_HEIGHT_PX });
});

it("starts the window a few rows above the scroll position", () => {
  expect(computeWindow(5000, [], 1000 * ROW_HEIGHT_PX, VIEWPORT_PX).start).toBe(992);
});

it("places the top spacer where the first drawn row starts", () => {
  const range = computeWindow(5000, [], 1000 * ROW_HEIGHT_PX, VIEWPORT_PX);

  expect(toWindowParts(range, 5000, [], null)[0]).toEqual({ kind: "spacer", heightPx: 992 * ROW_HEIGHT_PX });
});

it("counts an opened detail above the window in the top spacer", () => {
  const extents = [{ position: 10, heightPx: 300 }];

  const range = computeWindow(5000, extents, 1000 * ROW_HEIGHT_PX + 300, VIEWPORT_PX);

  expect(toWindowParts(range, 5000, extents, null)[0]).toEqual({ kind: "spacer", heightPx: 992 * ROW_HEIGHT_PX + 300 });
});

it("keeps an opened row drawn while its detail fills the viewport", () => {
  const extents = [{ position: 500, heightPx: 2000 }];

  expect(computeWindow(5000, extents, 501 * ROW_HEIGHT_PX + 1500, VIEWPORT_PX).start).toBe(492);
});

it("never starts past the last row", () => {
  expect(computeWindow(5000, [], 10_000 * ROW_HEIGHT_PX, VIEWPORT_PX).end).toBe(5000);
});

it("draws a pinned row above the window and its neighbours in their place, with spacers either side", () => {
  const range = computeWindow(5000, [], 2500 * ROW_HEIGHT_PX, VIEWPORT_PX);

  expect(toWindowParts(range, 5000, [], 10)).toEqual([
    { kind: "spacer", heightPx: 9 * ROW_HEIGHT_PX },
    { kind: "rows", start: 9, end: 12 },
    { kind: "spacer", heightPx: (range.start - 12) * ROW_HEIGHT_PX },
    { kind: "rows", start: range.start, end: range.end },
    { kind: "spacer", heightPx: (5000 - range.end) * ROW_HEIGHT_PX },
  ]);
});

it("draws a pinned row below the window and its neighbours in their place, with spacers either side", () => {
  const range = computeWindow(5000, [], 0, VIEWPORT_PX);

  expect(toWindowParts(range, 5000, [], 4000)).toEqual([
    { kind: "rows", start: 0, end: range.end },
    { kind: "spacer", heightPx: (3999 - range.end) * ROW_HEIGHT_PX },
    { kind: "rows", start: 3999, end: 4002 },
    { kind: "spacer", heightPx: 998 * ROW_HEIGHT_PX },
  ]);
});

it("extends the window to the row after a pinned last row", () => {
  const range = computeWindow(5000, [], 0, VIEWPORT_PX);

  expect(toWindowParts(range, 5000, [], range.end - 1)).toEqual([
    { kind: "rows", start: 0, end: range.end + 1 },
    { kind: "spacer", heightPx: (5000 - range.end - 1) * ROW_HEIGHT_PX },
  ]);
});

it("extends the window to the row before a pinned first row", () => {
  const range = computeWindow(5000, [], 2500 * ROW_HEIGHT_PX, VIEWPORT_PX);

  expect(toWindowParts(range, 5000, [], range.start)).toEqual([
    { kind: "spacer", heightPx: (range.start - 1) * ROW_HEIGHT_PX },
    { kind: "rows", start: range.start - 1, end: range.end },
    { kind: "spacer", heightPx: (5000 - range.end) * ROW_HEIGHT_PX },
  ]);
});

it("draws a pinned row inside the window only once", () => {
  const range = computeWindow(5000, [], 0, VIEWPORT_PX);

  expect(toWindowParts(range, 5000, [], 3)).toEqual([
    { kind: "rows", start: 0, end: range.end },
    { kind: "spacer", heightPx: (5000 - range.end) * ROW_HEIGHT_PX },
  ]);
});

it("counts the header and each opened detail above a row in its row index", () => {
  expect(toRowIndex(10, [{ position: 2, heightPx: 300 }, { position: 12, heightPx: 300 }])).toBe(13);
});
