// The case table. Each row opens with a real button; above WINDOW_THRESHOLD rows only the
// rows near the scroll position are drawn. Which rows are open is kept by case, not by DOM
// node, so an opened row is still open when it scrolls out of the window and back. The row
// holding focus and the rows either side of it stay drawn wherever the table scrolls, so Tab
// always has a next row to reach. aria-rowcount and aria-rowindex tell a screen reader where
// each drawn row sits in the whole table.
import { Fragment, useCallback, useEffect, useId, useLayoutEffect, useMemo, useRef, useState } from "react";

import {
  checklistLabel,
  isDangerous,
  isDisagreement,
  toVisibleText,
  verdictLabel,
  versionLabel,
} from "../case-rows";
import { computeWindow, DETAIL_ESTIMATE_PX, toRowIndex, toWindowParts } from "../case-window";
import { CaseDetail } from "./CaseDetail";
import { PanelSlot } from "./PanelSlot";
import { WarnIcon } from "./WarnIcon";
import "./CaseTable.css";

import type * as React from "react";
import type { CaseRow } from "../case-rows";
import type { DetailExtent } from "../case-window";
import type { PanelProps } from "../registry";
import type { CaseDetail as CaseDetailData } from "../results";

const COLUMN_COUNT = 8;
// Until the first scroll reports the real height; tall enough for a large screen's 70vh.
const FALLBACK_VIEWPORT_PX = 800;
const KNOWN_VERDICTS = new Set(["true_positive", "false_positive", "benign"]);

type CaseTableProps = PanelProps & {
  rows: readonly CaseRow[];
  details: ReadonlyMap<string, CaseDetailData>;
};

type ScrollState = { topPx: number; viewportPx: number };

export function CaseTable({ rows, details, view, results }: CaseTableProps) {
  const idPrefix = useId();
  const scrollRef = useRef<HTMLDivElement>(null);
  const [openCases, setOpenCases] = useState<ReadonlySet<number>>(() => new Set());
  const [detailHeights, setDetailHeights] = useState<ReadonlyMap<number, number>>(() => new Map());
  const [scroll, setScroll] = useState<ScrollState>({ topPx: 0, viewportPx: FALLBACK_VIEWPORT_PX });
  const [shownRows, setShownRows] = useState(rows);
  const [observer] = useState(() => createHeightObserver(setDetailHeights));
  const [focusedCase, setFocusedCase] = useState<number | null>(null);

  // New filters start the table at its top.
  if (shownRows !== rows) {
    setShownRows(rows);
    setScroll((current) => ({ ...current, topPx: 0 }));
  }
  useLayoutEffect(() => {
    if (scrollRef.current !== null) {
      scrollRef.current.scrollTop = 0;
    }
  }, [rows]);
  useEffect(() => () => observer?.disconnect(), [observer]);
  // One callback for every detail row: a new one each render would make React detach and
  // reattach it, and each new observe() reports a size, which renders again.
  const observeDetail = useCallback(
    (element: HTMLTableRowElement) => {
      if (observer === null) {
        return;
      }
      observer.observe(element);
      return () => observer.unobserve(element);
    },
    [observer],
  );

  const extents = useMemo<DetailExtent[]>(
    () =>
      rows.flatMap((row, position) =>
        openCases.has(row.index)
          ? [{ position, heightPx: detailHeights.get(row.index) ?? DETAIL_ESTIMATE_PX }]
          : [],
      ),
    [rows, openCases, detailHeights],
  );
  const range = computeWindow(rows.length, extents, scroll.topPx, scroll.viewportPx);
  const focusedPosition = useMemo(
    () => (focusedCase === null ? -1 : rows.findIndex((row) => row.index === focusedCase)),
    [rows, focusedCase],
  );
  const parts = toWindowParts(range, rows.length, extents, focusedPosition === -1 ? null : focusedPosition);

  function handleScroll(event: React.UIEvent<HTMLDivElement>) {
    const { scrollTop, clientHeight } = event.currentTarget;
    setScroll({ topPx: scrollTop, viewportPx: clientHeight > 0 ? clientHeight : FALLBACK_VIEWPORT_PX });
  }

  function handleFocus(event: React.FocusEvent<HTMLTableSectionElement>) {
    const caseIndex = (event.target as Element).closest("tr[data-case-index]")?.getAttribute("data-case-index");
    setFocusedCase(caseIndex === null || caseIndex === undefined ? null : Number(caseIndex));
  }

  // Focus that leaves for no element, as when the window loses focus, keeps the row drawn.
  function handleBlur(event: React.FocusEvent<HTMLTableSectionElement>) {
    if (event.relatedTarget !== null && !event.currentTarget.contains(event.relatedTarget)) {
      setFocusedCase(null);
    }
  }

  function toggleCase(index: number) {
    setOpenCases((current) => {
      const next = new Set(current);
      if (!next.delete(index)) {
        next.add(index);
      }
      return next;
    });
  }

  function renderRow(row: CaseRow, position: number) {
    const isOpen = openCases.has(row.index);
    const rowIndex = toRowIndex(position, extents);
    const detailId = `${idPrefix}-detail-${row.index}`;
    const caseText = toVisibleText(row.caseId);
    return (
      <Fragment key={row.index}>
        <tr
          className="case-row"
          data-case-index={row.index}
          data-dangerous={isDangerous(row)}
          aria-rowindex={rowIndex}
        >
          <td>
            <button
              type="button"
              className="case-toggle"
              aria-expanded={isOpen}
              // Set only while open: aria-controls must name an element that exists.
              aria-controls={isOpen ? detailId : undefined}
              title={caseText}
              onClick={() => toggleCase(row.index)}
            >
              <span className="case-cell-text">{caseText}</span>
            </button>
          </td>
          <td>
            <code className="case-cell-text">{toVisibleText(row.alertClass)}</code>
          </td>
          <td>{toVisibleText(row.week)}</td>
          <td>
            <span className="case-cell-text">{versionLabel(row.version)}</span>
          </td>
          <td>
            {renderVerdict(row.analyst)}
          </td>
          <td>
            {renderVerdict(row.agent)}
          </td>
          <td>
            {renderResult(row)}
          </td>
          <td className="case-num">{checklistLabel(row)}</td>
        </tr>
        {isOpen && (
          <tr
            id={detailId}
            className="case-detail-row"
            data-case-index={row.index}
            aria-rowindex={rowIndex + 1}
            ref={observeDetail}
          >
            <td colSpan={COLUMN_COUNT}>
              <CaseDetail row={row} detail={details.get(row.caseId) ?? null} />
              <PanelSlot name="case-detail" view={view} results={results} caseId={row.caseId} />
            </td>
          </tr>
        )}
      </Fragment>
    );
  }

  const captionId = `${idPrefix}-caption`;
  return (
    <div className="panel">
      <div
        ref={scrollRef}
        className="case-table-scroll"
        role="region"
        aria-labelledby={captionId}
        tabIndex={0}
        onScroll={handleScroll}
      >
      <table className="case-table" aria-rowcount={rows.length === 0 ? 2 : rows.length + extents.length + 1}>
        <caption id={captionId}>Cases, newest week first, then by case ID.</caption>
        <thead>
          <tr aria-rowindex={1}>
            <th scope="col">Case</th>
            <th scope="col">Alert class</th>
            <th scope="col">Week</th>
            <th scope="col">Version</th>
            <th scope="col">Analyst</th>
            <th scope="col">Agent</th>
            <th scope="col">Result</th>
            <th scope="col" className="case-num">
              Checklist
            </th>
          </tr>
        </thead>
        <tbody onFocus={handleFocus} onBlur={handleBlur}>
          {rows.length === 0 && (
            <tr aria-rowindex={2}>
              <td colSpan={COLUMN_COUNT} className="case-table-empty">
                No cases match these filters.
              </td>
            </tr>
          )}
          {/* One flat keyed list, so a row that moves between parts is kept, not remounted. */}
          {parts.flatMap((part, partIndex) =>
            part.kind === "spacer"
              ? [renderSpacer(part.heightPx, partIndex)]
              : rows.slice(part.start, part.end).map((row, offset) => renderRow(row, part.start + offset)),
          )}
        </tbody>
      </table>
      </div>
    </div>
  );
}

// Stands in for the rows outside the window, so the scrollbar keeps the whole table's length.
function renderSpacer(heightPx: number, partIndex: number) {
  return (
    <tr key={`spacer-${partIndex}`} className="case-table-spacer" aria-hidden="true" style={{ height: heightPx }}>
      <td colSpan={COLUMN_COUNT} />
    </tr>
  );
}

// Styled by the verdict the pill names, so a false positive never looks like a true positive.
function renderVerdict(verdict: string | null) {
  const style = verdict === null ? "unknown" : KNOWN_VERDICTS.has(verdict) ? verdict : "other";
  return (
    <span className="verdict-pill" data-verdict={style}>
      {verdictLabel(verdict)}
    </span>
  );
}

// Words carry the result; the warning icon and colour only repeat them.
function renderResult(row: CaseRow) {
  if (isDangerous(row)) {
    return (
      <span className="case-result" data-result="dangerous">
        <WarnIcon className="case-result-icon" />
        Dangerous false close
      </span>
    );
  }
  if (isDisagreement(row)) {
    return (
      <span className="case-result" data-result="disagree">
        Disagree
      </span>
    );
  }
  if (row.analyst === null || row.agent === null) {
    return (
      <span className="case-result" data-result="unknown">
        Not compared
      </span>
    );
  }
  return (
    <span className="case-result" data-result="agree">
      Agree
    </span>
  );
}

// Opened details vary in height, so each is measured as it renders and resizes. Browsers
// without ResizeObserver, and the test DOM, keep the estimate.
function createHeightObserver(
  setDetailHeights: React.Dispatch<React.SetStateAction<ReadonlyMap<number, number>>>,
): ResizeObserver | null {
  if (typeof ResizeObserver === "undefined") {
    return null;
  }
  return new ResizeObserver((entries) => {
    setDetailHeights((current) => {
      let next: Map<number, number> | null = null;
      for (const entry of entries) {
        const index = Number(entry.target.getAttribute("data-case-index"));
        const heightPx = entry.borderBoxSize[0]?.blockSize ?? entry.contentRect.height;
        if (heightPx > 0 && current.get(index) !== heightPx) {
          next ??= new Map(current);
          next.set(index, heightPx);
        }
      }
      // The same map when nothing changed, so React skips the render.
      return next ?? current;
    });
  });
}
