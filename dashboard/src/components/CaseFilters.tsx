// The case table's filters. Each lives in the hash query (`class` by anchor, `result`, `q`),
// so a link or a reload keeps them.
import { useId } from "react";

import { useRoute } from "../router";
import "./CaseFilters.css";

import type { ResultFilter } from "../case-rows";
import type { ClassFilterView } from "../view";

type CaseFiltersProps = {
  classFilters: readonly ClassFilterView[];
  /** The selected class's anchor, or "" for every class. */
  classAnchor: string;
  result: ResultFilter;
  search: string;
};

export function CaseFilters({ classFilters, classAnchor, result, search }: CaseFiltersProps) {
  const { setQuery } = useRoute();
  const classId = useId();
  const resultId = useId();
  const searchId = useId();

  return (
    <div className="case-filters" role="group" aria-label="Filter cases">
      <div className="case-filter">
        <label htmlFor={classId}>Alert class</label>
        <select
          id={classId}
          value={classAnchor}
          onChange={(event) => setQuery({ class: event.target.value === "" ? null : event.target.value })}
        >
          <option value="">All classes</option>
          {classFilters.map((filter) => (
            <option key={filter.anchor} value={filter.anchor}>
              {filter.label}
            </option>
          ))}
        </select>
      </div>
      <div className="case-filter">
        <label htmlFor={resultId}>Result</label>
        <select
          id={resultId}
          value={result}
          // Drops an old link's `dangerous=1` too, so it cannot come back when `result` is cleared.
          onChange={(event) =>
            setQuery({ result: event.target.value === "all" ? null : event.target.value, dangerous: null })
          }
        >
          <option value="all">All results</option>
          <option value="disagree">Disagreements</option>
          <option value="dangerous">Dangerous false closes</option>
        </select>
      </div>
      <div className="case-filter">
        <label htmlFor={searchId}>Search case ID</label>
        <input
          id={searchId}
          type="search"
          value={search}
          autoComplete="off"
          spellCheck={false}
          onChange={(event) => setQuery({ q: event.target.value === "" ? null : event.target.value })}
        />
      </div>
    </div>
  );
}
