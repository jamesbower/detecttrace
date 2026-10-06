// Served pages only: asks the server that sent the page whether newer results exist, and says
// so. A `detecttrace serve` page never reloads by itself, so filters and scroll position stay;
// a `detecttrace ui` page does (see StatusBar). It never reads more into the status than "newer
// or not" and "did the last update fail".

export const POLL_MS = 30_000;
// The ui app runs on this computer and the reader has just acted, so it is asked more often.
export const UI_POLL_MS = 5_000;
export const NEW_DATA_TEXT = "New data is available.";
const NO_TIME_TEXT = "an earlier update";

/** What the page knows about its own snapshot. */
export type PageSnapshot = { readonly generation: number; readonly updatedAt: string | null };

/** What the status bar shows: an offer to reload, a failure sentence, both or neither. */
export type Bar = { readonly isNewData: boolean; readonly errorText: string | null };

export const EMPTY_BAR: Bar = { isNewData: false, errorText: null };

// `/api/status` from src/detecttrace/serve/app.py; only these fields are read, and each is
// checked, since the body is whatever the server (or a proxy in front of it) sent.
// `last_error_text` comes from the ui app only (src/detecttrace/serve/ui.py): its own sentence
// about the failure, with paths inside its data folder made relative.
type Status = {
  readonly generation?: unknown;
  readonly updated_at?: unknown;
  readonly last_error?: unknown;
  readonly last_error_text?: unknown;
};

// A newer snapshot can share the generation: a case that settles later is added without any new
// write, so the time it was computed breaks the tie.
export function hasNewerResults(status: Status, page: PageSnapshot): boolean {
  if (typeof status.generation !== "number") {
    return false;
  }
  if (status.generation !== page.generation) {
    return status.generation > page.generation;
  }
  const statusTime = parseTime(status.updated_at);
  const pageTime = parseTime(page.updatedAt);
  return statusTime !== null && pageTime !== null && statusTime > pageTime;
}

/** A time in UTC to the minute, in a fixed format, so every reader sees the same text whatever their locale. */
export function formatTime(text: unknown): string {
  const ms = parseTime(text);
  if (ms === null) {
    return NO_TIME_TEXT;
  }
  return `${new Date(ms).toISOString().slice(0, 16).replace("T", " ")} UTC`;
}

/** The bar for a status. `serve`'s error text is never shown: it may name server paths. */
export function decideBar(status: Status, page: PageSnapshot): Bar {
  return { isNewData: hasNewerResults(status, page), errorText: decideErrorText(status, page) };
}

/** Equal for bars with the same content, so an unchanged bar is not drawn again. */
export function barKey(bar: Bar): string {
  return `${String(bar.isNewData)}\n${String(bar.errorText)}`;
}

/**
 * One check of the server's status. Returns a function that runs a check and gives a promise
 * that always settles, so the caller can schedule the next one either way. `show` is called
 * only when the bar's content changes; `warn` once per outage.
 */
export function createPoll(
  show: (bar: Bar) => void,
  page: PageSnapshot,
  fetchStatus: () => Promise<Response>,
  warn: (text: string) => void,
): () => Promise<void> {
  let shownKey = barKey(EMPTY_BAR);
  let hasLoggedFailure = false;
  return async function poll() {
    try {
      const response = await fetchStatus();
      if (!response.ok) {
        throw new Error(`HTTP ${response.status}`);
      }
      const status: unknown = await response.json();
      if (typeof status !== "object" || status === null) {
        throw new Error("the status is not a JSON object");
      }
      hasLoggedFailure = false;
      const bar = decideBar(status, page);
      const key = barKey(bar);
      // Unchanged text is not rewritten, so screen readers announce only real changes.
      if (key !== shownKey) {
        shownKey = key;
        show(bar);
      }
    } catch (error) {
      // A server restart or a dropped network is routine; one console line per outage.
      if (!hasLoggedFailure) {
        hasLoggedFailure = true;
        warn(`detecttrace: could not check for newer results: ${String(error)}`);
      }
    }
  };
}

/**
 * Checks every `intervalMs`, the first check one interval after the start. Each check is
 * scheduled after the previous one settles, so checks never overlap. Returns a function that
 * stops it.
 */
export function startPolling(poll: () => Promise<void>, intervalMs = POLL_MS): () => void {
  let timer: ReturnType<typeof setTimeout> | undefined;
  let isStopped = false;
  function pollAndWait() {
    void poll().then(() => {
      if (!isStopped) {
        timer = setTimeout(pollAndWait, intervalMs);
      }
    });
  }
  timer = setTimeout(pollAndWait, intervalMs);
  return () => {
    isStopped = true;
    clearTimeout(timer);
  };
}

/** The same-origin status request; the browser adds the credentials the page was loaded with. */
export function fetchStatus(): Promise<Response> {
  return fetch("/api/status", { credentials: "same-origin", cache: "no-store" });
}

function decideErrorText(status: Status, page: PageSnapshot): string | null {
  if (typeof status.last_error_text === "string" && status.last_error_text !== "") {
    return status.last_error_text;
  }
  if (typeof status.last_error === "string" && status.last_error !== "") {
    return `Showing data from ${formatTime(page.updatedAt)}; the last update failed.`;
  }
  return null;
}

// The server sends microseconds; Safari's Date.parse may refuse more than three digits.
function parseTime(text: unknown): number | null {
  const ms = typeof text === "string" ? Date.parse(text.replace(/(\.\d{3})\d+/, "$1")) : NaN;
  return Number.isNaN(ms) ? null : ms;
}
