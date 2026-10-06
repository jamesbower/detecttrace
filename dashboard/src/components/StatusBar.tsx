import { useEffect, useState } from "react";

import { EMPTY_BAR, NEW_DATA_TEXT, POLL_MS, UI_POLL_MS, createPoll, fetchStatus, startPolling } from "../poller";
import { isReloadHeld, subscribeToReloadHold } from "../reload-hold";
import { reloadPage } from "../reload-note";
import "./StatusBar.css";

import type { Bar } from "../poller";
import type { ServedView } from "../view";

type StatusBarProps = {
  /** Set only on a page from `detecttrace serve`; without it the page never asks the network. */
  served?: ServedView | null;
  /** In `detecttrace ui` the reader's own upload or configuration made the newer results, so
   * the page reloads by itself instead of offering to, unless unsaved work holds the reload. */
  shouldReloadOnNewData?: boolean;
};

// The live region exists from the first render, so later messages in it are announced.
export function StatusBar({ served = null, shouldReloadOnNewData = false }: StatusBarProps) {
  const bar = useServerNews(served, shouldReloadOnNewData);
  const isShown = bar.isNewData || bar.errorText !== null;
  return (
    <div className="status-bar" role="status" aria-live="polite">
      {isShown && (
        <div className="status-bar-box">
          <p>{[bar.errorText, bar.isNewData ? NEW_DATA_TEXT : null].filter((part) => part !== null).join(" ")}</p>
          {bar.isNewData && (
            <button type="button" className="status-bar-reload" onClick={reloadPage}>
              Reload
            </button>
          )}
        </div>
      )}
    </div>
  );
}

function useServerNews(served: ServedView | null, shouldReloadOnNewData: boolean): Bar {
  const [bar, setBar] = useState<Bar>(EMPTY_BAR);
  const generation = served?.generation;
  const updatedAt = served?.updated_at;
  useEffect(() => {
    if (generation === undefined || updatedAt === undefined) {
      return;
    }
    // Newer results seen while the reload was held; the reload follows once the hold ends.
    let hasHeldNewData = false;
    function show(next: Bar) {
      hasHeldNewData = false;
      if (shouldReloadOnNewData && next.isNewData) {
        if (!isReloadHeld()) {
          reloadPage();
          return;
        }
        hasHeldNewData = true;
      }
      setBar(next);
    }
    const poll = createPoll(show, { generation, updatedAt }, fetchStatus, (text) => console.warn(text));
    const stopPolling = startPolling(poll, shouldReloadOnNewData ? UI_POLL_MS : POLL_MS);
    const stopListening = subscribeToReloadHold(() => {
      if (hasHeldNewData && !isReloadHeld()) {
        reloadPage();
      }
    });
    return () => {
      stopPolling();
      stopListening();
    };
  }, [generation, updatedAt, shouldReloadOnNewData]);
  return bar;
}
