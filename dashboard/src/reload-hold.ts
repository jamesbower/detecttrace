// A ui page reloads by itself when newer results exist, except while the reader has work on it
// that a reload would lose: an open configuration form with unsaved edits, a file on its way,
// or upload results not yet cleared. Each holder has its own token, so one letting go never
// ends another's hold.
import { useEffect } from "react";

const holders = new Set<symbol>();
const listeners = new Set<() => void>();

/** Holds the reload until the returned function is called; calling it again does nothing. */
export function holdReload(): () => void {
  const token = Symbol("reload hold");
  const wasHeld = isReloadHeld();
  holders.add(token);
  if (!wasHeld) {
    notifyListeners();
  }
  return () => {
    if (holders.delete(token) && !isReloadHeld()) {
      notifyListeners();
    }
  };
}

/** Holds the reload while `isHolding` is true and the component is mounted. */
export function useReloadHold(isHolding: boolean): void {
  useEffect(() => (isHolding ? holdReload() : undefined), [isHolding]);
}

export function isReloadHeld(): boolean {
  return holders.size > 0;
}

/** Calls `listener` whenever the hold starts or ends. Returns a function that stops it. */
export function subscribeToReloadHold(listener: () => void): () => void {
  listeners.add(listener);
  return () => {
    listeners.delete(listener);
  };
}

function notifyListeners(): void {
  for (const listener of listeners) {
    listener();
  }
}
