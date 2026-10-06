// The case table's rows: decoded from the results JSON's columnar `case_rows`, filtered, and
// turned into the text an opened row shows. Case data comes from the results, not the view, so
// every string from it passes through toVisibleText before it is shown.
import type { CallData, CaseDetail, CaseRows } from "./results";

export type CaseRow = {
  /** The case's position in the results columns, which are ordered by case ID. */
  readonly index: number;
  readonly caseId: string;
  readonly classIndex: number;
  readonly alertClass: string;
  readonly week: string;
  readonly version: string | null;
  /** A verdict code such as "true_positive", or null when the verdict is unknown. */
  readonly analyst: string | null;
  readonly agent: string | null;
  readonly satisfied: number | null;
  /** The class checklist's item names, or null when the class has no checklist. */
  readonly checklist: readonly string[] | null;
  readonly notCalled: readonly number[];
  readonly wrongArguments: readonly number[];
  readonly failed: readonly number[];
};

export type CaseFilter = {
  /** Keeps only this class's cases; null keeps every class. */
  readonly classIndex: number | null;
  readonly isDangerousOnly: boolean;
  /** Keeps cases whose ID contains this text, ignoring case; empty keeps every case. */
  readonly search: string;
};

export type OutcomeModel = {
  readonly item: string;
  readonly status: "missed" | "failed";
  readonly why: string;
};

export type CallModel = {
  readonly isFailed: boolean;
  readonly statusLabel: string;
  readonly tool: string;
  readonly args: string;
  readonly duration: string;
};

export type DetailModel = {
  /** null when the case is not one of the notable cases the results carry calls for. */
  readonly calls: readonly CallModel[] | null;
  readonly callsNote: string | null;
  readonly steps: readonly (OutcomeModel & { readonly label: string })[];
  readonly stepsNote: string | null;
};

export const DETAIL_MISSING = "Details not included. Raise dashboard.max_detail_cases.";

const VERDICT_LABELS = new Map([
  ["true_positive", "True positive"],
  ["false_positive", "False positive"],
  ["benign", "Benign"],
]);

// Same rule as to_visible_text in summary.py, so a bidi override or line break cannot make
// one case ID read as another. The u flag matches code points: surrogate pairs stay whole.
export function toVisibleText(text: string): string {
  return text.replace(/[\p{C}\p{Zl}\p{Zp}]/gu, (char) => {
    const code = char.codePointAt(0)!;
    const hex = code.toString(16);
    if (code < 0x100) {
      return `\\x${hex.padStart(2, "0")}`;
    }
    if (code < 0x10000) {
      return `\\u${hex.padStart(4, "0")}`;
    }
    return `\\U${hex.padStart(8, "0")}`;
  });
}

export function decodeRows(caseRows: CaseRows): CaseRow[] {
  const { strings, columns } = caseRows;
  const checklists = new Map<number, string[]>();
  for (const checklist of caseRows.checklists) {
    checklists.set(
      checklist.class_index,
      checklist.items.map((item) => strings[item]!),
    );
  }
  const toVerdict = (code: number): string | null =>
    code === caseRows.unknown_verdict_code ? null : (caseRows.verdict_codes[code] ?? null);

  return columns.case_id.map((caseId, index) => {
    const classIndex = columns.class_index[index]!;
    const version = columns.version[index]!;
    return {
      index,
      caseId,
      classIndex,
      alertClass: strings[classIndex]!,
      week: strings[columns.week[index]!]!,
      version: version === null ? null : strings[version]!,
      analyst: toVerdict(columns.analyst[index]!),
      agent: toVerdict(columns.agent[index]!),
      satisfied: columns.satisfied[index]!,
      checklist: checklists.get(classIndex) ?? null,
      notCalled: columns.not_called_items[index]!,
      wrongArguments: columns.wrong_argument_items[index]!,
      failed: columns.failed_items[index]!,
    };
  });
}

// Newest week first, then case ID; the column order is already by case ID, so the index
// breaks ties and keeps the order stable in every engine.
export function orderRows(rows: readonly CaseRow[]): CaseRow[] {
  return [...rows].sort((a, b) => {
    if (a.week !== b.week) {
      return a.week < b.week ? 1 : -1;
    }
    return a.index - b.index;
  });
}

// Cases with an unknown verdict on either side are not compared, as in the metrics.
export function isDisagreement(row: CaseRow): boolean {
  return row.analyst !== null && row.agent !== null && row.analyst !== row.agent;
}

export function isDangerous(row: CaseRow): boolean {
  return row.analyst === "true_positive" && (row.agent === "false_positive" || row.agent === "benign");
}

export function filterRows(rows: readonly CaseRow[], filter: CaseFilter): CaseRow[] {
  const search = filter.search.trim().toLowerCase();
  return rows.filter(
    (row) =>
      (filter.classIndex === null || row.classIndex === filter.classIndex) &&
      (!filter.isDangerousOnly || isDangerous(row)) &&
      (search === "" || row.caseId.toLowerCase().includes(search)),
  );
}

export function formatCount(count: number): string {
  return String(count).replace(/\B(?=(\d{3})+(?!\d))/g, ",");
}

export function countLine(matching: number, total: number): string {
  const noun = total === 1 ? "case" : "cases";
  return `Showing ${formatCount(matching)} of ${formatCount(total)} ${noun}.`;
}

export function versionLabel(version: string | null): string {
  return version === null ? "(no version)" : toVisibleText(version);
}

export function verdictLabel(verdict: string | null): string {
  return verdict === null ? "(no verdict)" : (VERDICT_LABELS.get(verdict) ?? toVisibleText(verdict));
}

export function checklistLabel(row: CaseRow): string {
  return row.satisfied === null || row.checklist === null
    ? "—"
    : `${row.satisfied} of ${row.checklist.length}`;
}

// Rounded before the unit is chosen, so 999.7 ms reads "1.0 s", not "1000 ms".
export function formatDuration(ms: number): string {
  const rounded = Math.round(ms);
  return rounded < 1000 ? `${rounded} ms` : `${(ms / 1000).toFixed(1)} s`;
}

// A Map, not a plain object: a case ID such as "__proto__" must not reach the prototype.
export function indexDetails(caseDetail: readonly CaseDetail[]): ReadonlyMap<string, CaseDetail> {
  return new Map(caseDetail.map((detail) => [detail.case_id, detail]));
}

/** The unsatisfied steps, in checklist order. Only detail cases carry the failing rule. */
export function toOutcomes(row: CaseRow, detail: CaseDetail | null): OutcomeModel[] {
  if (detail !== null) {
    return detail.outcomes.flatMap((outcome) =>
      outcome.status === "satisfied"
        ? []
        : [
            {
              item: toVisibleText(outcome.item),
              status: outcome.status,
              why: describeOutcome(outcome.status, outcome.reason, outcome.failed_rule),
            },
          ],
    );
  }
  const { checklist } = row;
  if (checklist === null) {
    return [];
  }
  const found: (readonly [number, "missed" | "failed", "not_called" | "wrong_arguments" | null])[] = [
    ...row.notCalled.map((position) => [position, "missed", "not_called"] as const),
    ...row.wrongArguments.map((position) => [position, "missed", "wrong_arguments"] as const),
    ...row.failed.map((position) => [position, "failed", null] as const),
  ];
  found.sort((a, b) => a[0] - b[0]);
  return found.map(([position, status, reason]) => ({
    item: toVisibleText(checklist[position]!),
    status,
    why: describeOutcome(status, reason, null),
  }));
}

/** What an opened row shows. A note replaces a list that is missing or empty. */
export function toDetailModel(row: CaseRow, detail: CaseDetail | null): DetailModel {
  const steps =
    row.checklist === null
      ? []
      : toOutcomes(row, detail).map((outcome) => ({
          ...outcome,
          label: outcome.status === "failed" ? "Failed" : "Missed",
        }));
  let callsNote: string | null = null;
  if (detail === null) {
    callsNote = DETAIL_MISSING;
  } else if (detail.calls.length === 0) {
    callsNote = "No tool calls.";
  }
  let stepsNote: string | null = null;
  if (row.checklist === null) {
    stepsNote = "This alert class has no checklist.";
  } else if (steps.length === 0) {
    stepsNote = "None. Every checklist step was satisfied.";
  }
  return { calls: detail === null ? null : detail.calls.map(toCallModel), callsNote, steps, stepsNote };
}

function describeOutcome(
  status: "missed" | "failed",
  reason: "not_called" | "wrong_arguments" | null,
  failedRule: string | null,
): string {
  if (status === "failed") {
    return "every call returned an error";
  }
  if (reason === "not_called") {
    return "not called";
  }
  return failedRule ? `wrong arguments (${toVisibleText(failedRule)})` : "wrong arguments";
}

// Arguments only: the results JSON never carries a tool's result, and nothing here reads one.
function toCallModel(call: CallData): CallModel {
  const isFailed = call.status !== "success";
  return {
    isFailed,
    statusLabel: isFailed ? "✕ Failed" : "✓ Success",
    tool: toVisibleText(call.tool),
    args: call.arguments === null ? "(no arguments)" : toVisibleText(call.arguments),
    duration: formatDuration(call.duration_ms),
  };
}
