// Reads the two JSON blocks the Python side embeds in the page.
import type { Results } from "./results";
import type { View } from "./view";

export type PageData = { readonly view: View; readonly results: Results };
export type PageDataError = { readonly error: string };

export const VIEW_BLOCK_ID = "dt-view";
export const RESULTS_BLOCK_ID = "dt-results";
const SUPPORTED_VIEW_VERSION = 1;

/** The page's view and results, or why they can't be read. Never throws. */
export function readPageData(doc: Document): PageData | PageDataError {
  const view = readBlock(doc, VIEW_BLOCK_ID);
  if ("error" in view) {
    return view;
  }
  const results = readBlock(doc, RESULTS_BLOCK_ID);
  if ("error" in results) {
    return results;
  }
  const version = (view.value as { view_version?: unknown }).view_version;
  if (version !== SUPPORTED_VIEW_VERSION) {
    const found = version === undefined ? "has no version" : `has version ${JSON.stringify(version)}`;
    return { error: `The view data ${found}; this page reads version ${SUPPORTED_VIEW_VERSION}.` };
  }
  // The Python side builds both objects; past the version check their shape is trusted.
  return { view: view.value as View, results: results.value as Results };
}

export function isPageDataError(data: PageData | PageDataError): data is PageDataError {
  return "error" in data;
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
