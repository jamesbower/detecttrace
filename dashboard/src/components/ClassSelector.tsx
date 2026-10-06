// Picks the alert class a page shows. The choice lives in the hash's `class` parameter, by the
// class's anchor, so a link or a reload keeps it.
import { useId } from "react";

import { useRoute } from "../router";
import { useSelectedClass } from "../use-selected-class";
import "./ClassSelector.css";

import type * as React from "react";
import type { ClassView } from "../view";

/** Above this many classes a row of tabs wraps badly, so a drop-down replaces it. */
export const MAX_CLASS_TABS = 6;

type ClassSelectorProps = {
  classes: readonly ClassView[];
  /** The id of the page's element that shows the selected class. */
  panelId: string;
};

/** The id of a class's tab, for the panel's aria-labelledby. */
export function getClassTabId(anchor: string): string {
  return `class-tab-${anchor}`;
}

export function ClassSelector({ classes, panelId }: ClassSelectorProps) {
  const { setQuery } = useRoute();
  const selected = useSelectedClass(classes);
  const selectId = useId();

  if (selected === undefined) {
    return null;
  }

  if (classes.length > MAX_CLASS_TABS) {
    return (
      <div className="class-select">
        <label htmlFor={selectId}>Alert class</label>
        <select
          id={selectId}
          value={selected.anchor}
          aria-controls={panelId}
          onChange={(event) => setQuery({ class: event.target.value })}
        >
          {classes.map((alertClass) => (
            <option key={alertClass.anchor} value={alertClass.anchor}>
              {alertClass.name}
            </option>
          ))}
        </select>
      </div>
    );
  }

  function handleKeyDown(event: React.KeyboardEvent<HTMLButtonElement>, index: number) {
    const target = findTargetIndex(event.key, index, classes.length);
    if (target === null) {
      return;
    }
    event.preventDefault();
    const tabs = event.currentTarget.parentElement?.querySelectorAll<HTMLButtonElement>('[role="tab"]');
    tabs?.[target]?.focus();
    setQuery({ class: classes[target]!.anchor });
  }

  return (
    <div className="class-tabs" role="tablist" aria-label="Alert class">
      {classes.map((alertClass, index) => {
        const isSelected = alertClass.anchor === selected.anchor;
        return (
          <button
            key={alertClass.anchor}
            type="button"
            role="tab"
            id={getClassTabId(alertClass.anchor)}
            className="class-tab"
            aria-selected={isSelected}
            aria-controls={panelId}
            tabIndex={isSelected ? 0 : -1}
            onClick={() => setQuery({ class: alertClass.anchor })}
            onKeyDown={(event) => handleKeyDown(event, index)}
          >
            {alertClass.name}
          </button>
        );
      })}
    </div>
  );
}

// The ARIA tabs keys: arrows move one tab and wrap; Home and End jump to the ends.
function findTargetIndex(key: string, index: number, count: number): number | null {
  switch (key) {
    case "ArrowRight":
      return (index + 1) % count;
    case "ArrowLeft":
      return (index - 1 + count) % count;
    case "Home":
      return 0;
    case "End":
      return count - 1;
    default:
      return null;
  }
}
