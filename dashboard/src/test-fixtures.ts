// Test only: the real view and results the Python side builds for the demo data.
import demoResults from "../../tests/fixtures/demo/expected.json";
import demoView from "../../tests/fixtures/demo/expected-view.json";

import type { Results } from "./results";
import type { ClassView, View } from "./view";

// JSON imports widen literal types such as view_version, so the casts go through unknown.
export const DEMO_VIEW = demoView as unknown as View;
export const DEMO_RESULTS = demoResults as unknown as Results;

/** `count` copies of the demo's first class, with distinct anchors and names. */
export function createClasses(count: number): ClassView[] {
  const base = DEMO_VIEW.classes[0]!;
  return Array.from({ length: count }, (_, index) => ({
    ...base,
    anchor: `class-${index}`,
    name: `class_${index}`,
  }));
}

/**
 * jsdom's own styles hide every popover until it is shown, but jsdom cannot show one. This lets a
 * pop-up display as it does once a browser has shown it. Returns the undo.
 */
export function showPopoversForTests(): () => void {
  const style = document.createElement("style");
  style.textContent = "[popover] { display: block !important; }";
  document.head.append(style);
  return () => style.remove();
}
