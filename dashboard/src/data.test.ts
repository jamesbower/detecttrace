import { describe, expect, it } from "vitest";

import { readPageData } from "./data";

const VIEW_JSON = JSON.stringify({ view_version: 2, header: { title: "DetectTrace" } });
const WAITING = { counts: [{ label: "Spans received", value: "12" }], notes: [] };
const WAITING_VIEW_JSON = JSON.stringify({ view_version: 2, waiting: WAITING });
const RESULTS_JSON = JSON.stringify({ schema_version: 1, case_rows: {}, case_detail: [] });

function createDocument(blocks: Record<string, string>): Document {
  const doc = document.implementation.createHTMLDocument("test");
  for (const [id, text] of Object.entries(blocks)) {
    const script = doc.createElement("script");
    script.type = "application/json";
    script.id = id;
    script.textContent = text;
    doc.body.append(script);
  }
  return doc;
}

describe("readPageData", () => {
  it("returns the view from the page", () => {
    const doc = createDocument({ "dt-view": VIEW_JSON, "dt-results": RESULTS_JSON });

    expect(readPageData(doc)).toMatchObject({ view: { header: { title: "DetectTrace" } } });
  });

  it("returns the results from the page", () => {
    const doc = createDocument({ "dt-view": VIEW_JSON, "dt-results": RESULTS_JSON });

    expect(readPageData(doc)).toMatchObject({ results: { case_detail: [] } });
  });

  it("reads a waiting page without its empty results block", () => {
    const doc = createDocument({ "dt-view": WAITING_VIEW_JSON, "dt-results": "" });

    expect(readPageData(doc)).toMatchObject({ waiting: WAITING });
  });

  it("checks a waiting page's view version", () => {
    const doc = createDocument({ "dt-view": JSON.stringify({ view_version: 1, waiting: WAITING }) });

    expect(readPageData(doc)).toEqual({
      error: 'The "dt-view" data block has view_version 1; this page reads version 2.',
    });
  });

  it("reads a page whose view is not waiting as one with results", () => {
    const doc = createDocument({
      "dt-view": JSON.stringify({ view_version: 2, waiting: null }),
      "dt-results": RESULTS_JSON,
    });

    expect(readPageData(doc)).toMatchObject({ results: { case_detail: [] } });
  });

  it("names a missing view block", () => {
    const doc = createDocument({ "dt-results": RESULTS_JSON });

    expect(readPageData(doc)).toEqual({ error: 'The page has no "dt-view" data block.' });
  });

  it("names a missing results block", () => {
    const doc = createDocument({ "dt-view": VIEW_JSON });

    expect(readPageData(doc)).toEqual({ error: 'The page has no "dt-results" data block.' });
  });

  it("names an empty block", () => {
    const doc = createDocument({ "dt-view": "  \n", "dt-results": RESULTS_JSON });

    expect(readPageData(doc)).toEqual({ error: 'The "dt-view" data block is empty.' });
  });

  it("names a block that is not JSON", () => {
    const doc = createDocument({ "dt-view": VIEW_JSON, "dt-results": "{not json" });

    expect(readPageData(doc)).toEqual({ error: 'The "dt-results" data block is not valid JSON.' });
  });

  it("names a block that is JSON but not an object", () => {
    const doc = createDocument({ "dt-view": "[1]", "dt-results": RESULTS_JSON });

    expect(readPageData(doc)).toEqual({ error: 'The "dt-view" data block is not a JSON object.' });
  });

  it("names an unsupported view version", () => {
    const doc = createDocument({
      "dt-view": JSON.stringify({ view_version: 1 }),
      "dt-results": RESULTS_JSON,
    });

    expect(readPageData(doc)).toEqual({
      error: 'The "dt-view" data block has view_version 1; this page reads version 2.',
    });
  });

  it("names a view with no version", () => {
    const doc = createDocument({ "dt-view": "{}", "dt-results": RESULTS_JSON });

    expect(readPageData(doc)).toEqual({
      error: 'The "dt-view" data block has no view_version; this page reads version 2.',
    });
  });

  it("names an unsupported results schema version", () => {
    const doc = createDocument({
      "dt-view": VIEW_JSON,
      "dt-results": JSON.stringify({ schema_version: 2, case_rows: {}, case_detail: [] }),
    });

    expect(readPageData(doc)).toEqual({
      error: 'The "dt-results" data block has schema_version 2; this page reads version 1.',
    });
  });

  it("names results with no schema version", () => {
    const doc = createDocument({ "dt-view": VIEW_JSON, "dt-results": "{}" });

    expect(readPageData(doc)).toEqual({
      error: 'The "dt-results" data block has no schema_version; this page reads version 1.',
    });
  });
});
