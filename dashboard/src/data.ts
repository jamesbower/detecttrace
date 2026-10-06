// Reads the two JSON blocks the Python side embeds in the page.
import type { Results } from "./results";
import type { View, WaitingView } from "./view";

export type PageData = { readonly view: View; readonly results: Results };
/** The page `detecttrace serve` shows until a case can be scored: it has no results. */
export type WaitingData = { readonly view: View; readonly waiting: WaitingView };
export type PageDataError = { readonly error: string };

export const VIEW_BLOCK_ID = "dt-view";
export const RESULTS_BLOCK_ID = "dt-results";
const SUPPORTED_VERSION = 1;

/**
 * The page's view and results, the view alone on a waiting page (whose results block stays
 * empty), or why they can't be read. Never throws.
 */
export function readPageData(doc: Document): PageData | WaitingData | PageDataError {
  const viewBlock = readBlock(doc, VIEW_BLOCK_ID);
  if ("error" in viewBlock) {
    return viewBlock;
  }
  const viewError = checkVersion(viewBlock.value, VIEW_BLOCK_ID, "view_version");
  if (viewError !== null) {
    return viewError;
  }
  // The Python side builds both objects; past the version checks their shape is trusted.
  const view = viewBlock.value as View;
  if (view.waiting) {
    return { view, waiting: view.waiting };
  }
  const results = readBlock(doc, RESULTS_BLOCK_ID);
  if ("error" in results) {
    return results;
  }
  const resultsError = checkVersion(results.value, RESULTS_BLOCK_ID, "schema_version");
  if (resultsError !== null) {
    return resultsError;
  }
  return { view, results: results.value as Results };
}

export function isPageDataError(data: PageData | WaitingData | PageDataError): data is PageDataError {
  return "error" in data;
}

export function isWaitingData(data: PageData | WaitingData): data is WaitingData {
  return "waiting" in data;
}

// Wrapped in `value`, so a block whose JSON has an `error` key is never taken for an error.
function readBlock(doc: Document, id: string): { value: object } | PageDataError {
  const block = doc.getElementById(id);
  if (block === null) {
    return { error: `The page has no "${id}" data block.` };
  }
  const text = block.textContent ?? "";
  if (text.trim() === "") {
    return { error: `The "${id}" data block is empty.` };
  }
  let parsed: unknown;
  try {
    parsed = JSON.parse(text);
  } catch {
    return { error: `The "${id}" data block is not valid JSON.` };
  }
  if (typeof parsed !== "object" || parsed === null || Array.isArray(parsed)) {
    return { error: `The "${id}" data block is not a JSON object.` };
  }
  return { value: parsed };
}

function checkVersion(value: object, id: string, key: string): PageDataError | null {
  const version: unknown = (value as Record<string, unknown>)[key];
  if (version === SUPPORTED_VERSION) {
    return null;
  }
  const found = version === undefined ? `no ${key}` : `${key} ${JSON.stringify(version)}`;
  return { error: `The "${id}" data block has ${found}; this page reads version ${SUPPORTED_VERSION}.` };
}
