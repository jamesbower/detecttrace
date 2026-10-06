// The case table. Each row opens with a real button; above WINDOW_THRESHOLD rows only the
// rows near the scroll position are drawn. Which rows are open is kept by case, not by DOM
// node, so an opened row is still open when it scrolls out of the window and back.
import { Fragment, useEffect, useId, useLayoutEffect, useMemo, useRef, useState } from "react";

import {
  checklistLabel,
  isDangerous,
  isDisagreement,
  toVisibleText,
  verdictLabel,
  versionLabel,
} from "../case-rows";
import { computeWindow, DETAIL_ESTIMATE_PX } from "../case-window";
import { CaseDetail } from "./CaseDetail";
import { PanelSlot } from "./PanelSlot";
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

  function handleScroll(event: React.UIEvent<HTMLDivElement>) {
    const { scrollTop, clientHeight } = event.currentTarget;
    setScroll({ topPx: scrollTop, viewportPx: clientHeight > 0 ? clientHeight : FALLBACK_VIEWPORT_PX });
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

  const captionId = `${idPrefix}-caption`;
  return (
    <div
      ref={scrollRef}
      className="case-table-scroll"
      role="region"
      aria-labelledby={captionId}
      tabIndex={0}
      onScroll={handleScroll}
    >
      <table className="case-table">
        <caption id={captionId}>Cases, newest week first, then by case ID.</caption>
        <thead>
          <tr>
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
        <tbody>
          {rows.length === 0 && (
            <tr>
              <td colSpan={COLUMN_COUNT} className="case-table-empty">
                No cases match these filters.
              </td>
            </tr>
          )}
          {range.topPx > 0 && renderSpacer(range.topPx)}
          {rows.slice(range.start, range.end).map((row) => {
            const isOpen = openCases.has(row.index);
            const detailId = `${idPrefix}-detail-${row.index}`;
            const caseText = toVisibleText(row.caseId);
            return (
              <Fragment key={row.index}>
                <tr className="case-row" data-dangerous={isDangerous(row)}>
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
                    ref={(element) => {
                      if (element === null || observer === null) {
                        return;
                      }
                      observer.observe(element);
                      return () => observer.unobserve(element);
                    }}
                  >
                    <td colSpan={COLUMN_COUNT}>
                      <CaseDetail row={row} detail={details.get(row.caseId) ?? null} />
                      <PanelSlot name="case-detail" view={view} results={results} />
                    </td>
                  </tr>
                )}
              </Fragment>
            );
          })}
          {range.bottomPx > 0 && renderSpacer(range.bottomPx)}
        </tbody>
      </table>
    </div>
  );
}

// Stands in for the rows outside the window, so the scrollbar keeps the whole table's length.
function renderSpacer(heightPx: number) {
  return (
    <tr className="case-table-spacer" aria-hidden="true" style={{ height: heightPx }}>
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
        <svg className="case-result-icon" viewBox="0 0 16 16" aria-hidden="true" focusable="false">
          <path d="M8 1.8 15 14H1z" />
          <path d="M8 6.2v3.6M8 11.8v.2" />
        </svg>
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
      const next = new Map(current);
      for (const entry of entries) {
        const index = Number(entry.target.getAttribute("data-case-index"));
        const heightPx = entry.borderBoxSize[0]?.blockSize ?? entry.contentRect.height;
        if (heightPx > 0) {
          next.set(index, heightPx);
        }
      }
      return next;
    });
  });
}
