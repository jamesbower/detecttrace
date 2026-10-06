// The case table's filters. Each lives in the hash query (`class` by anchor, `dangerous`, `q`),
// so a link or a reload keeps them.
import { useId } from "react";

import { useRoute } from "../router";
import "./CaseFilters.css";

import type { ClassFilterView } from "../view";

type CaseFiltersProps = {
  classFilters: readonly ClassFilterView[];
  /** The selected class's anchor, or "" for every class. */
  classAnchor: string;
  isDangerousOnly: boolean;
  search: string;
};

export function CaseFilters({ classFilters, classAnchor, isDangerousOnly, search }: CaseFiltersProps) {
  const { setQuery } = useRoute();
  const classId = useId();
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
      <label className="case-filter-check">
        <input
          type="checkbox"
          checked={isDangerousOnly}
          onChange={(event) => setQuery({ dangerous: event.target.checked ? "1" : null })}
        />
        Dangerous false closes only
      </label>
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
