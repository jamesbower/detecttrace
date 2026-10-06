import { describe, expect, it } from "vitest";

import {
  checklistLabel,
  countLine,
  DETAIL_MISSING,
  decodeRows,
  filterRows,
  formatCount,
  formatDuration,
  indexDetails,
  isDangerous,
  isDisagreement,
  orderRows,
  toDetailModel,
  toOutcomes,
  toVisibleText,
  verdictLabel,
  versionLabel,
} from "./case-rows";
import { DEMO_RESULTS } from "./test-fixtures";

import type { CaseFilter, CaseRow } from "./case-rows";
import type { CaseDetail, CaseRows } from "./results";

const demoRows = decodeRows(DEMO_RESULTS.case_rows);
const demoDetails = indexDetails(DEMO_RESULTS.case_detail);
const NO_FILTER: CaseFilter = { classIndex: null, isDangerousOnly: false, search: "" };

function findRow(rows: readonly CaseRow[], caseId: string): CaseRow {
  return rows.find((row) => row.caseId === caseId)!;
}

// Three cases of one class with a four-step checklist; case C has no version or agent verdict.
const SYNTHETIC: CaseRows = {
  verdict_codes: ["true_positive", "false_positive", "benign"],
  unknown_verdict_code: -1,
  checklists: [{ class_index: 0, items: [1, 2, 3, 4] }],
  strings: ["phishing", "headers", "sandbox", "url_rep", "mailbox", "2026-W10", "v1", "2026-W11"],
  columns: {
    case_id: ["A", "B", "C"],
    class_index: [0, 0, 0],
    week: [5, 7, 5],
    version: [6, 6, null],
    analyst: [0, 1, 0],
    agent: [2, 1, -1],
    satisfied: [1, 4, null],
    not_called_items: [[3], [], []],
    wrong_argument_items: [[0], [], []],
    failed_items: [[1], [], []],
  },
};
const [rowA, rowB, rowC] = decodeRows(SYNTHETIC) as [CaseRow, CaseRow, CaseRow];

const FAILED_CALL_DETAIL: CaseDetail = {
  case_id: "A",
  calls: [
    { tool: "sandbox", arguments: null, status: "failed", duration_ms: 5 },
    { tool: "headers", arguments: "{}", status: "success", duration_ms: 12 },
  ],
  outcomes: [],
};

describe("decoding", () => {
  it("decodes a demo row from the columns and the strings table", () => {
    const row = findRow(demoRows, "DT-IT-0003");

    expect([row.alertClass, row.week, row.version, row.analyst, row.agent, row.satisfied]).toEqual([
      "impossible_travel",
      "2026-W32",
      "v1",
      "benign",
      "false_positive",
      4,
    ]);
  });

  it("decodes every demo case", () => {
    expect(demoRows).toHaveLength(201);
  });

  it("decodes a row's checklist item names", () => {
    expect(rowA.checklist).toEqual(["headers", "sandbox", "url_rep", "mailbox"]);
  });

  it("decodes the unknown verdict code as null", () => {
    expect(rowC.agent).toBeNull();
  });

  it("orders rows newest week first, then by case ID", () => {
    expect(orderRows([rowA, rowB, rowC]).map((row) => row.caseId)).toEqual(["B", "A", "C"]);
  });
});

describe("tool results", () => {
  it("never appear in the demo results' tool calls", () => {
    const keys = new Set(DEMO_RESULTS.case_detail.flatMap((detail) => detail.calls.flatMap(Object.keys)));

    expect([...keys].sort()).toEqual(["arguments", "duration_ms", "status", "tool"]);
  });
});

describe("filters", () => {
  it("no filter keeps every demo case", () => {
    expect(filterRows(demoRows, NO_FILTER)).toHaveLength(201);
  });

  it("the dangerous filter finds the demo dangerous false closes", () => {
    expect(filterRows(demoRows, { ...NO_FILTER, isDangerousOnly: true })).toHaveLength(4);
  });

  it("a class filter keeps the first demo class", () => {
    expect(filterRows(demoRows, { ...NO_FILTER, classIndex: 0 })).toHaveLength(106);
  });

  it("a class filter keeps the second demo class", () => {
    expect(filterRows(demoRows, { ...NO_FILTER, classIndex: 6 })).toHaveLength(95);
  });

  it("a search keeps the case IDs that contain it, ignoring case", () => {
    expect(filterRows(demoRows, { ...NO_FILTER, search: "dt-it-000" })).toHaveLength(9);
  });

  it("a search ignores surrounding spaces", () => {
    expect(filterRows(demoRows, { ...NO_FILTER, search: "  DT-IT-0003 " })).toHaveLength(1);
  });

  it("filters combine", () => {
    expect(filterRows(demoRows, { classIndex: 6, isDangerousOnly: true, search: "DT-IT" })).toHaveLength(0);
  });

  it("a case with an unknown agent verdict is not a disagreement", () => {
    expect(isDisagreement(rowC)).toBe(false);
  });

  it("a true positive closed as benign is dangerous", () => {
    expect(isDangerous({ ...rowA, analyst: "true_positive", agent: "benign" })).toBe(true);
  });

  it("a true positive with no agent verdict is not dangerous", () => {
    expect(isDangerous(rowC)).toBe(false);
  });
});

describe("labels", () => {
  it("the count line uses thousands separators", () => {
    expect(countLine(100, 4210)).toBe("Showing 100 of 4,210 cases.");
  });

  it("the count line is singular for one case", () => {
    expect(countLine(1, 1)).toBe("Showing 1 of 1 case.");
  });

  it("the count line is plural for no cases", () => {
    expect(countLine(0, 0)).toBe("Showing 0 of 0 cases.");
  });

  it("formats a count in the millions", () => {
    expect(formatCount(1234567)).toBe("1,234,567");
  });

  it("a null version is labeled (no version)", () => {
    expect(versionLabel(rowC.version)).toBe("(no version)");
  });

  it("a version named like a label keeps its own text", () => {
    expect(versionLabel("null")).toBe("null");
  });

  it("an unknown verdict is labeled (no verdict)", () => {
    expect(verdictLabel(null)).toBe("(no verdict)");
  });

  it("a known verdict is labeled in words", () => {
    expect(verdictLabel("false_positive")).toBe("False positive");
  });

  it("a case without a satisfied count shows a dash for its checklist", () => {
    expect(checklistLabel(rowC)).toBe("—");
  });

  it("a case with a satisfied count shows it out of the checklist's length", () => {
    expect(checklistLabel(rowA)).toBe("1 of 4");
  });

  it("a duration just under a second rounds up to seconds", () => {
    expect(formatDuration(999.7)).toBe("1.0 s");
  });

  it("a duration that rounds below a second stays in milliseconds", () => {
    expect(formatDuration(999.4)).toBe("999 ms");
  });
});

describe("visible text", () => {
  it("keeps plain text", () => {
    expect(toVisibleText("Café 'x' {y}")).toBe("Café 'x' {y}");
  });

  it("escapes an escape character as \\xNN", () => {
    expect(toVisibleText("a\x1b[2Kb")).toBe("a\\x1b[2Kb");
  });

  it("escapes line breaks", () => {
    expect(toVisibleText("a\r\nb")).toBe("a\\x0d\\x0ab");
  });

  it("escapes a bidi override as \\uNNNN", () => {
    expect(toVisibleText("a‮b")).toBe("a\\u202eb");
  });

  it("escapes the line and paragraph separators", () => {
    expect(toVisibleText("  ")).toBe("\\u2028\\u2029");
  });

  it("escapes an astral format character as \\UNNNNNNNN", () => {
    expect(toVisibleText("\u{e0001}")).toBe("\\U000e0001");
  });

  it("keeps an emoji's surrogate pair whole", () => {
    expect(toVisibleText("a\u{1f600}b")).toBe("a\u{1f600}b");
  });

  it("escapes a lone surrogate", () => {
    expect(toVisibleText("\ud800")).toBe("\\ud800");
  });

  it("a version shows through visible text", () => {
    expect(versionLabel("v1 v2")).toBe("v1\\u2028v2");
  });

  it("an unrecognised verdict shows through visible text", () => {
    expect(verdictLabel("tp​")).toBe("tp\\u200b");
  });
});

describe("outcomes", () => {
  it("from detail carry the failing rule", () => {
    const row = findRow(demoRows, "DT-IT-0073");

    expect(toOutcomes(row, demoDetails.get("DT-IT-0073")!)).toEqual([
      { item: "signin_history", status: "missed", why: "wrong arguments (range: min_duration)" },
      { item: "mfa_check", status: "missed", why: "not called" },
    ]);
  });

  it("from the columns give the reason without the rule", () => {
    const row = findRow(demoRows, "DT-IT-0073");

    expect(toOutcomes(row, null)).toEqual([
      { item: "signin_history", status: "missed", why: "wrong arguments" },
      { item: "mfa_check", status: "missed", why: "not called" },
    ]);
  });

  it("from the columns follow checklist order across reasons", () => {
    expect(toOutcomes(rowA, null)).toEqual([
      { item: "headers", status: "missed", why: "wrong arguments" },
      { item: "sandbox", status: "failed", why: "every call returned an error" },
      { item: "mailbox", status: "missed", why: "not called" },
    ]);
  });

  it("items from the columns show through visible text", () => {
    const row = { ...rowA, checklist: ["head‮ers", "sandbox", "url_rep", "mailbox"] };

    expect(toOutcomes(row, null)[0]!.item).toBe("head\\u202eers");
  });

  it("a failing rule from detail shows through visible text", () => {
    const detail: CaseDetail = {
      case_id: "A",
      calls: [],
      outcomes: [{ item: "headers", status: "missed", reason: "wrong_arguments", failed_rule: "eq‮" }],
    };

    expect(toOutcomes(rowA, detail)[0]!.why).toBe("wrong arguments (eq\\u202e)");
  });
});

describe("detail lookup", () => {
  it("finds nothing for an unknown case", () => {
    expect(demoDetails.get("DT-NOT-THERE")).toBeUndefined();
  });

  it("a __proto__ case ID without detail does not reach the prototype", () => {
    expect(indexDetails([]).get("__proto__")).toBeUndefined();
  });

  it("a __proto__ case ID with detail finds its own detail", () => {
    const detail: CaseDetail = { case_id: "__proto__", calls: [], outcomes: [] };

    expect(indexDetails([detail]).get("__proto__")).toBe(detail);
  });
});

describe("the detail model", () => {
  it("a case without detail says how to include it", () => {
    expect(toDetailModel(rowC, null).callsNote).toBe(DETAIL_MISSING);
  });

  it("a case without detail lists no calls", () => {
    expect(toDetailModel(rowC, null).calls).toBeNull();
  });

  it("a failed call is labeled as failed", () => {
    expect(toDetailModel(rowA, FAILED_CALL_DETAIL).calls![0]!.statusLabel).toBe("✕ Failed");
  });

  it("a successful call is labeled as a success", () => {
    expect(toDetailModel(rowA, FAILED_CALL_DETAIL).calls![1]!.statusLabel).toBe("✓ Success");
  });

  it("a call without arguments says so", () => {
    expect(toDetailModel(rowA, FAILED_CALL_DETAIL).calls![0]!.args).toBe("(no arguments)");
  });

  it("a detail case with no calls says so", () => {
    expect(toDetailModel(rowA, { ...FAILED_CALL_DETAIL, calls: [] }).callsNote).toBe("No tool calls.");
  });

  it("a failed step is labeled as failed", () => {
    expect(toDetailModel(rowA, null).steps[1]!.label).toBe("Failed");
  });

  it("a missed step is labeled as missed", () => {
    expect(toDetailModel(rowA, null).steps[0]!.label).toBe("Missed");
  });

  it("a case whose class has no checklist says so", () => {
    expect(toDetailModel({ ...rowA, checklist: null }, null).stepsNote).toBe(
      "This alert class has no checklist.",
    );
  });

  it("a case with every step satisfied says so", () => {
    expect(toDetailModel(rowB, null).stepsNote).toBe("None. Every checklist step was satisfied.");
  });
});
