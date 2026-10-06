import { useMemo } from "react";

import { countLine, decodeRows, filterRows, indexDetails, orderRows } from "../case-rows";
import { CaseFilters } from "../components/CaseFilters";
import { CaseTable } from "../components/CaseTable";
import { PageHead } from "../components/PageHead";
import { useRoute } from "../router";
import "./Cases.css";

import type { PageProps } from "../registry";

export function Cases({ view, results }: PageProps) {
  const { query } = useRoute();
  const rows = useMemo(() => orderRows(decodeRows(results.case_rows)), [results]);
  const details = useMemo(() => indexDetails(results.case_detail), [results]);

  // An anchor no class filter has, such as a stale link's, shows every class.
  const classFilter = view.cases.class_filters.find((filter) => filter.anchor === query.get("class"));
  const classIndex = classFilter?.class_index ?? null;
  const isDangerousOnly = query.get("dangerous") === "1";
  const search = query.get("q") ?? "";
  const matching = useMemo(
    () => filterRows(rows, { classIndex, isDangerousOnly, search }),
    [rows, classIndex, isDangerousOnly, search],
  );

  return (
    <>
      <PageHead
        eyebrow="Cases"
        title="Cases"
        description="Open a row to see its tool calls and the checklist steps it did not satisfy."
      />
      <section className="cases-page" aria-label="Case table">
        <CaseFilters
          classFilters={view.cases.class_filters}
          classAnchor={classFilter?.anchor ?? ""}
          isDangerousOnly={isDangerousOnly}
          search={search}
        />
        <div className="cases-summary">
          <p className="cases-count" aria-live="polite">
            {countLine(matching.length, rows.length)}
          </p>
          <p>{view.cases.detail_sentence}</p>
        </div>
        <CaseTable rows={matching} details={details} view={view} results={results} />
      </section>
    </>
  );
}
