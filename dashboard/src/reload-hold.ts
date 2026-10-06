// A ui page reloads by itself when newer results exist, except while the reader has unsaved
// work on it: the configuration form holds the reload until it is saved or closed.

let isHeld = false;
const listeners = new Set<() => void>();

export function setReloadHeld(held: boolean): void {
  if (held === isHeld) {
    return;
  }
  isHeld = held;
  for (const listener of listeners) {
    listener();
  }
}

export function isReloadHeld(): boolean {
  return isHeld;
}

/** Calls `listener` whenever the hold changes. Returns a function that stops it. */
export function subscribeToReloadHold(listener: () => void): () => void {
  listeners.add(listener);
  return () => {
    listeners.delete(listener);
  };
}
