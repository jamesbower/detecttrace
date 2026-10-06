import { useRoute } from "./router";

import type { ClassView } from "./view";

/** The class the hash's `class` parameter names by anchor, else the first class. */
export function useSelectedClass(classes: readonly ClassView[]): ClassView | undefined {
  const { query } = useRoute();
  const anchor = query.get("class");
  return classes.find((alertClass) => alertClass.anchor === anchor) ?? classes[0];
}
