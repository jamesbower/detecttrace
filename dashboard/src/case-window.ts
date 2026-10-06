// Windowing for the case table: above WINDOW_THRESHOLD rows only the rows near the scroll
// position are drawn, with spacers standing in for the rest. Every row is one fixed height;
// an opened row adds its detail's measured height below it.

/** Up to this many rows are all drawn. */
export const WINDOW_THRESHOLD = 100;
// NOTE: must equal --case-row-height in tokens.css, which CaseTable.css gives every row.
export const ROW_HEIGHT_PX = 52;
/** Used before an opened row's detail has been measured. */
export const DETAIL_ESTIMATE_PX = 240;
const OVERSCAN_ROWS = 8;

/** An opened row: its position in the shown rows and the height of its detail. */
export type DetailExtent = { readonly position: number; readonly heightPx: number };

export type WindowRange = {
  /** The first drawn row's position. */
  readonly start: number;
  /** One past the last drawn row's position. */
  readonly end: number;
  readonly topPx: number;
  readonly bottomPx: number;
};

/** A run of drawn rows by position, or a spacer standing in for rows that are not drawn. */
export type WindowPart =
  | { readonly kind: "rows"; readonly start: number; readonly end: number }
  | { readonly kind: "spacer"; readonly heightPx: number };

/** The rows to draw for a scroll position. `extents` must be sorted by position. */
export function computeWindow(
  rowCount: number,
  extents: readonly DetailExtent[],
  scrollTopPx: number,
  viewportPx: number,
): WindowRange {
  if (rowCount <= WINDOW_THRESHOLD) {
    return { start: 0, end: rowCount, topPx: 0, bottomPx: 0 };
  }
  const first = findFirstVisible(rowCount, extents, scrollTopPx);
  const start = Math.max(0, first - OVERSCAN_ROWS);
  // Opened details only take room from rows, so this many rows always fill the viewport.
  const end = Math.min(rowCount, first + Math.ceil(viewportPx / ROW_HEIGHT_PX) + 1 + OVERSCAN_ROWS);
  return {
    start,
    end,
    topPx: offsetOf(start, extents),
    bottomPx: offsetOf(rowCount, extents) - offsetOf(end, extents),
  };
}

/** The window's parts in table order. A pinned row outside the window, such as the one that
 * holds focus, is drawn in its own place too, so scrolling never removes the focused row. */
export function toWindowParts(
  range: WindowRange,
  rowCount: number,
  extents: readonly DetailExtent[],
  pinned: number | null,
): WindowPart[] {
  const parts: WindowPart[] = [];
  const addSpacer = (heightPx: number) => {
    if (heightPx > 0) {
      parts.push({ kind: "spacer", heightPx });
    }
  };
  const isOutside = pinned !== null && pinned >= 0 && pinned < rowCount && (pinned < range.start || pinned >= range.end);
  if (isOutside && pinned < range.start) {
    addSpacer(offsetOf(pinned, extents));
    parts.push({ kind: "rows", start: pinned, end: pinned + 1 });
    addSpacer(range.topPx - offsetOf(pinned + 1, extents));
  } else {
    addSpacer(range.topPx);
  }
  parts.push({ kind: "rows", start: range.start, end: range.end });
  if (isOutside && pinned >= range.end) {
    addSpacer(offsetOf(pinned, extents) - offsetOf(range.end, extents));
    parts.push({ kind: "rows", start: pinned, end: pinned + 1 });
    addSpacer(offsetOf(rowCount, extents) - offsetOf(pinned + 1, extents));
  } else {
    addSpacer(range.bottomPx);
  }
  return parts;
}

/** A row's aria-rowindex: the header is row 1, and each opened detail above it is a row too. */
export function toRowIndex(position: number, extents: readonly DetailExtent[]): number {
  let detailsAbove = 0;
  for (const extent of extents) {
    if (extent.position >= position) {
      break;
    }
    detailsAbove += 1;
  }
  return position + detailsAbove + 2;
}

// Where a row starts: the rows above it plus the details opened above it.
function offsetOf(position: number, extents: readonly DetailExtent[]): number {
  let offsetPx = position * ROW_HEIGHT_PX;
  for (const extent of extents) {
    if (extent.position >= position) {
      break;
    }
    offsetPx += extent.heightPx;
  }
  return offsetPx;
}

// A scroll position inside an opened detail counts as that detail's row.
function findFirstVisible(rowCount: number, extents: readonly DetailExtent[], scrollTopPx: number): number {
  let detailsAbovePx = 0;
  for (const extent of extents) {
    const detailTopPx = (extent.position + 1) * ROW_HEIGHT_PX + detailsAbovePx;
    if (detailTopPx > scrollTopPx) {
      break;
    }
    if (detailTopPx + extent.heightPx > scrollTopPx) {
      return extent.position;
    }
    detailsAbovePx += extent.heightPx;
  }
  const position = Math.floor((scrollTopPx - detailsAbovePx) / ROW_HEIGHT_PX);
  return Math.min(Math.max(position, 0), rowCount - 1);
}
