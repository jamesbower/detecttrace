// Whether a scroll area is wider inside than out, so it is a tab stop only when the keyboard
// needs one to reach the hidden part. Measured before the first paint, then on every resize.
import { useLayoutEffect, useRef, useState } from "react";

import type * as React from "react";

export function useIsScrollable<T extends HTMLElement>(): [React.RefObject<T | null>, boolean] {
  const ref = useRef<T>(null);
  const [isScrollable, setIsScrollable] = useState(false);
  useLayoutEffect(() => {
    const element = ref.current;
    if (element === null) {
      return;
    }
    const measure = () => setIsScrollable(element.scrollWidth > element.clientWidth);
    measure();
    if (typeof ResizeObserver === "undefined") {
      return;
    }
    const observer = new ResizeObserver(measure);
    // The area and what it scrolls: either can change width on its own.
    observer.observe(element);
    if (element.firstElementChild !== null) {
      observer.observe(element.firstElementChild);
    }
    return () => observer.disconnect();
  }, []);
  return [ref, isScrollable];
}
