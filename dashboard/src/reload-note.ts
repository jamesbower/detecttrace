// A reload the page makes by itself loses keyboard focus and anything a screen reader was
// about to hear. Before it, the page leaves itself a one-shot note in sessionStorage: which
// Data step heading to focus and whether to say the dashboard was updated. Storage can be
// missing or refuse access; the reload then happens without the note.

const NOTE_KEY = "detecttrace:reload-note";
/** Marks a Data step; its value is the id of the step's heading. */
const RELOAD_FOCUS_ATTRIBUTE = "data-reload-focus";

export type ReloadNote = {
  /** The id of the step heading to focus after the reload, or null to leave focus alone. */
  readonly focusId: string | null;
  readonly shouldAnnounce: boolean;
};

/** Reloads the page; on the ui app's Data page, first notes the step the focus was in. */
export function reloadPage(): void {
  if (document.querySelector(`[${RELOAD_FOCUS_ATTRIBUTE}]`) === null) {
    location.reload();
    return;
  }
  const step = document.activeElement?.closest(`[${RELOAD_FOCUS_ATTRIBUTE}]`);
  reloadWithNote({ focusId: step?.getAttribute(RELOAD_FOCUS_ATTRIBUTE) ?? null, shouldAnnounce: true });
}

export function reloadWithNote(note: ReloadNote): void {
  try {
    sessionStorage.setItem(NOTE_KEY, JSON.stringify(note));
  } catch {
    // Private browsing or blocked storage: reload without the note.
  }
  location.reload();
}

/** The note the page left before reloading, removed so it applies once, or null. */
export function takeReloadNote(): ReloadNote | null {
  let text: string | null;
  try {
    text = sessionStorage.getItem(NOTE_KEY);
    sessionStorage.removeItem(NOTE_KEY);
  } catch {
    return null;
  }
  if (text === null) {
    return null;
  }
  let value: unknown;
  try {
    value = JSON.parse(text);
  } catch {
    return null;
  }
  return isReloadNote(value) ? { focusId: value.focusId, shouldAnnounce: value.shouldAnnounce } : null;
}

function isReloadNote(value: unknown): value is ReloadNote {
  if (typeof value !== "object" || value === null) {
    return false;
  }
  const { focusId, shouldAnnounce } = value as Record<string, unknown>;
  return (focusId === null || typeof focusId === "string") && typeof shouldAnnounce === "boolean";
}
