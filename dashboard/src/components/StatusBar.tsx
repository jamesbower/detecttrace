import { useEffect, useState } from "react";

import { EMPTY_BAR, NEW_DATA_TEXT, createPoll, fetchStatus, startPolling } from "../poller";
import "./StatusBar.css";

import type { Bar } from "../poller";
import type { ServedView } from "../view";

type StatusBarProps = {
  /** Set only on a page from `detecttrace serve`; without it the page never asks the network. */
  served?: ServedView | null;
};

// The live region exists from the first render, so later messages in it are announced.
export function StatusBar({ served = null }: StatusBarProps) {
  const bar = useServerNews(served);
  const isShown = bar.isNewData || bar.errorText !== null;
  return (
    <div className="status-bar" role="status" aria-live="polite">
      {isShown && (
        <div className="status-bar-box">
          <p>{[bar.errorText, bar.isNewData ? NEW_DATA_TEXT : null].filter((part) => part !== null).join(" ")}</p>
          {bar.isNewData && (
            <button type="button" className="status-bar-reload" onClick={() => location.reload()}>
              Reload
            </button>
          )}
        </div>
      )}
    </div>
  );
}

function useServerNews(served: ServedView | null): Bar {
  const [bar, setBar] = useState<Bar>(EMPTY_BAR);
  const generation = served?.generation;
  const updatedAt = served?.updated_at;
  useEffect(() => {
    if (generation === undefined || updatedAt === undefined) {
      return;
    }
    const poll = createPoll(setBar, { generation, updatedAt }, fetchStatus, (text) => console.warn(text));
    return startPolling(poll);
  }, [generation, updatedAt]);
  return bar;
}
