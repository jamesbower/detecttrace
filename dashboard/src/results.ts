// The parts of the results JSON (src/detecttrace/results.py) the case table reads.
// Hand-written, unlike view.d.ts: results.py documents the shape, and only these fields are typed.

/** Per-case columns, all the same length. Text columns hold indexes into `CaseRows.strings`. */
export type CaseColumns = {
  readonly case_id: ReadonlyArray<string>;
  readonly class_index: ReadonlyArray<number>;
  readonly week: ReadonlyArray<number>;
  /** null means the case has no prompt version. */
  readonly version: ReadonlyArray<number | null>;
  /** Indexes into `CaseRows.verdict_codes`, or `CaseRows.unknown_verdict_code`. */
  readonly analyst: ReadonlyArray<number>;
  readonly agent: ReadonlyArray<number>;
  /** null when the case's class has no checklist. */
  readonly satisfied: ReadonlyArray<number | null>;
  /** Positions in the class checklist, per case. */
  readonly not_called_items: ReadonlyArray<ReadonlyArray<number>>;
  readonly wrong_argument_items: ReadonlyArray<ReadonlyArray<number>>;
  readonly failed_items: ReadonlyArray<ReadonlyArray<number>>;
};

export type ChecklistRow = {
  readonly class_index: number;
  readonly items: ReadonlyArray<number>;
};

export type CaseRows = {
  readonly verdict_codes: ReadonlyArray<string>;
  readonly unknown_verdict_code: number;
  readonly checklists: ReadonlyArray<ChecklistRow>;
  readonly strings: ReadonlyArray<string>;
  readonly columns: CaseColumns;
};

export type CallData = {
  readonly tool: string;
  readonly status: "success" | "failed";
  readonly duration_ms: number;
  readonly arguments: string | null;
};

export type OutcomeData = {
  readonly item: string;
  readonly status: "satisfied" | "failed" | "missed";
  readonly reason: "not_called" | "wrong_arguments" | null;
  readonly failed_rule: string | null;
};

export type CaseDetail = {
  readonly case_id: string;
  readonly calls: ReadonlyArray<CallData>;
  readonly outcomes: ReadonlyArray<OutcomeData>;
};

export type Results = {
  readonly case_rows: CaseRows;
  readonly case_detail: ReadonlyArray<CaseDetail>;
};
