import { isPageDataError, isWaitingData } from "./data";
import { DATA_PATH, replaceEmptyAddress } from "./router";

import type { PageData, PageDataError, WaitingData } from "./data";

/** A fresh ui app has nothing to show until the reader uploads, so with no page named in the
 * address it opens on the Data page. Called before the first render. */
export function openStartPage(data: PageData | WaitingData | PageDataError): void {
  if (!isPageDataError(data) && isWaitingData(data) && data.view.mode === "ui") {
    replaceEmptyAddress(DATA_PATH);
  }
}
