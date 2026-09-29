import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import { createRequire } from "node:module";
import { test } from "node:test";

const require = createRequire(import.meta.url);
const table = require("../../src/detecttrace/templates/dashboard.js");
const demo = JSON.parse(readFileSync(new URL("../fixtures/demo/expected.json", import.meta.url), "utf8"));
const demoRows = table.decodeRows(demo.case_rows);
const demoDetails = table.indexDetails(demo.case_detail);

function findRow(rows, caseId) {
  return rows.find((row) => row.caseId === caseId);
}

// Three cases of one class with a four-step checklist; case C has no version or agent verdict.
const synthetic = {
  verdict_codes: ["true_positive", "false_positive", "benign"],
  unknown_verdict_code: -1,
  checklists: [{ class_index: 0, items: [1, 2, 3, 4] }],
  strings: ["phishing", "headers", "sandbox", "url_rep", "mailbox", "2026-W10", "v1", "2026-W11"],
  columns: {
    case_id: ["A", "B", "C"],
    class_index: [0, 0, 0],
    week: [5, 7, 5],
    version: [6, 6, null],
    analyst: [0, 1, 0],
    agent: [2, 1, -1],
    satisfied: [1, 4, null],
    not_called_items: [[3], [], []],
    wrong_argument_items: [[0], [], []],
    failed_items: [[1], [], []],
  },
};
const syntheticRows = table.decodeRows(synthetic);

test("decodes a demo row from the columns and the strings table", () => {
  const row = findRow(demoRows, "DT-IT-0003");
  assert.deepEqual(
    [row.alertClass, row.week, row.version, row.analyst, row.agent, row.satisfied],
    ["impossible_travel", "2026-W32", "v1", "benign", "false_positive", 4],
  );
});

test("decodes every demo case", () => {
  assert.equal(demoRows.length, 201);
});

test("decodes a synthetic row's checklist item names", () => {
  assert.deepEqual(syntheticRows[0].checklist, ["headers", "sandbox", "url_rep", "mailbox"]);
});

test("decodes the unknown verdict code as null", () => {
  assert.equal(syntheticRows[2].agent, null);
});

test("orders rows newest week first, then by case ID", () => {
  assert.deepEqual(
    table.orderRows(syntheticRows).map((row) => row.caseId),
    ["B", "A", "C"],
  );
});

test("the all filter keeps every demo case", () => {
  assert.equal(table.filterRows(demoRows, "all").length, 201);
});

test("the disagreements filter finds the demo disagreements", () => {
  assert.equal(table.filterRows(demoRows, "disagreements").length, 19);
});

test("the dangerous filter finds the demo dangerous false closes", () => {
  assert.equal(table.filterRows(demoRows, "dangerous").length, 4);
});

test("a class filter keeps the first demo class", () => {
  assert.equal(table.filterRows(demoRows, "class:0").length, 106);
});

test("a class filter keeps the second demo class", () => {
  assert.equal(table.filterRows(demoRows, "class:6").length, 95);
});

test("a case with an unknown agent verdict is not a disagreement", () => {
  assert.equal(table.isDisagreement(syntheticRows[2]), false);
});

test("the first page holds one page of rows", () => {
  assert.equal(table.nextPage(demoRows, 0).length, table.PAGE_SIZE);
});

test("the last page holds the remaining rows", () => {
  assert.equal(table.nextPage(demoRows, 200).length, 1);
});

test("the next page starts after the rows already shown", () => {
  assert.equal(table.nextPage(demoRows, 100)[0], demoRows[100]);
});

test("the count line uses thousands separators", () => {
  assert.equal(table.countLine(100, 4210), "Showing 100 of 4,210 matching cases.");
});

test("the count line is singular for one matching case", () => {
  assert.equal(table.countLine(1, 1), "Showing 1 of 1 matching case.");
});

test("the count line is plural for no matching cases", () => {
  assert.equal(table.countLine(0, 0), "Showing 0 of 0 matching cases.");
});

test("formats a count in the millions", () => {
  assert.equal(table.formatCount(1234567), "1,234,567");
});

test("a null version is labeled (no version)", () => {
  assert.equal(table.versionLabel(syntheticRows[2].version), "(no version)");
});

test("a version named like a label keeps its own text", () => {
  assert.equal(table.versionLabel("null"), "null");
});

test("an unknown verdict is labeled (no verdict)", () => {
  assert.equal(table.verdictLabel(null), "(no verdict)");
});

test("a case without a satisfied count shows a dash for its checklist", () => {
  assert.equal(table.checklistLabel(syntheticRows[2]), "—");
});

test("outcomes from detail carry the failing rule", () => {
  const row = findRow(demoRows, "DT-IT-0073");
  assert.deepEqual(table.toOutcomes(row, table.findDetail(demoDetails, "DT-IT-0073")), [
    { item: "signin_history", status: "missed", why: "wrong arguments (range: min_duration)" },
    { item: "mfa_check", status: "missed", why: "not called" },
  ]);
});

test("outcomes from the columns give the reason without the rule", () => {
  const row = findRow(demoRows, "DT-IT-0073");
  assert.deepEqual(table.toOutcomes(row, null), [
    { item: "signin_history", status: "missed", why: "wrong arguments" },
    { item: "mfa_check", status: "missed", why: "not called" },
  ]);
});

test("outcomes from the columns follow checklist order across reasons", () => {
  assert.deepEqual(table.toOutcomes(syntheticRows[0], null), [
    { item: "headers", status: "missed", why: "wrong arguments" },
    { item: "sandbox", status: "failed", why: "every call returned an error" },
    { item: "mailbox", status: "missed", why: "not called" },
  ]);
});

test("a detail lookup that finds nothing returns null", () => {
  assert.equal(table.findDetail(demoDetails, "DT-NOT-THERE"), null);
});

test("a __proto__ case ID without detail does not reach the prototype", () => {
  assert.equal(table.findDetail(table.indexDetails([]), "__proto__"), null);
});

test("a toString case ID without detail does not reach the prototype", () => {
  assert.equal(table.findDetail(table.indexDetails([]), "toString"), null);
});

test("a __proto__ case ID with detail finds its own detail", () => {
  const detail = { case_id: "__proto__", calls: [], outcomes: [] };
  assert.equal(table.findDetail(table.indexDetails([detail]), "__proto__"), detail);
});

test("visible text keeps plain text", () => {
  assert.equal(table.toVisibleText("Café 'x' {y}"), "Café 'x' {y}");
});

test("visible text escapes an escape character as \\xNN", () => {
  assert.equal(table.toVisibleText("a\x1b[2Kb"), "a\\x1b[2Kb");
});

test("visible text escapes line breaks", () => {
  assert.equal(table.toVisibleText("a\r\nb"), "a\\x0d\\x0ab");
});

test("visible text escapes a bidi override as \\uNNNN", () => {
  assert.equal(table.toVisibleText("a‮b"), "a\\u202eb");
});

test("visible text escapes the line and paragraph separators", () => {
  assert.equal(table.toVisibleText("  "), "\\u2028\\u2029");
});

test("visible text escapes an astral format character as \\UNNNNNNNN", () => {
  assert.equal(table.toVisibleText("\u{e0001}"), "\\U000e0001");
});

test("visible text keeps an emoji's surrogate pair whole", () => {
  assert.equal(table.toVisibleText("a\u{1f600}b"), "a\u{1f600}b");
});

test("visible text escapes a lone surrogate", () => {
  assert.equal(table.toVisibleText("\ud800"), "\\ud800");
});

test("a row shows its case ID through visible text", () => {
  const row = { ...syntheticRows[0], caseId: "A‮1-TSAC" };
  assert.equal(table.rowTexts(row)[0], "A\\u202e1-TSAC");
});

test("a row shows its alert class through visible text", () => {
  const row = { ...syntheticRows[0], alertClass: "phish\x1b[2K" };
  assert.equal(table.rowTexts(row)[1], "phish\\x1b[2K");
});

test("a version shows through visible text", () => {
  assert.equal(table.versionLabel("v1 v2"), "v1\\u2028v2");
});

test("an unrecognised verdict shows through visible text", () => {
  assert.equal(table.verdictLabel("tp​"), "tp\\u200b");
});

test("outcome items from the columns show through visible text", () => {
  const row = { ...syntheticRows[0], checklist: ["head‮ers", "sandbox", "url_rep", "mailbox"] };
  assert.equal(table.toOutcomes(row, null)[0].item, "head\\u202eers");
});

test("a failing rule from detail shows through visible text", () => {
  const detail = { outcomes: [{ item: "headers", status: "missed", reason: "wrong_arguments", failed_rule: "eq‮" }] };
  assert.equal(table.toOutcomes(syntheticRows[0], detail)[0].why, "wrong arguments (eq\\u202e)");
});

test("a page holds 100 rows", () => {
  assert.equal(table.PAGE_SIZE, 100);
});

test("a duration just under a second rounds up to seconds", () => {
  assert.equal(table.formatDuration(999.7), "1.0 s");
});

test("a duration that rounds below a second stays in milliseconds", () => {
  assert.equal(table.formatDuration(999.4), "999 ms");
});

// The detail model: what an opened row shows, before any DOM is built.

const failedCallDetail = {
  case_id: "A",
  calls: [
    { tool: "sandbox", arguments: null, status: "error", duration_ms: 5 },
    { tool: "headers", arguments: "{}", status: "success", duration_ms: 12 },
  ],
  outcomes: [],
};

test("a case without detail says how to include it", () => {
  assert.equal(
    table.toDetailModel(syntheticRows[2], null).callsNote,
    "Details not included. Raise dashboard.max_detail_cases.",
  );
});

test("a case without detail lists no calls", () => {
  assert.equal(table.toDetailModel(syntheticRows[2], null).calls, null);
});

test("a failed call is labeled as failed", () => {
  assert.equal(table.toDetailModel(syntheticRows[0], failedCallDetail).calls[0].statusLabel, "✕ Failed");
});

test("a successful call is labeled as a success", () => {
  assert.equal(table.toDetailModel(syntheticRows[0], failedCallDetail).calls[1].statusLabel, "✓ Success");
});

test("a call without arguments says so", () => {
  assert.equal(table.toDetailModel(syntheticRows[0], failedCallDetail).calls[0].args, "(no arguments)");
});

test("a detail case with no calls says so", () => {
  assert.equal(table.toDetailModel(syntheticRows[0], { ...failedCallDetail, calls: [] }).callsNote, "No tool calls.");
});

test("a failed step is labeled as failed", () => {
  assert.equal(table.toDetailModel(syntheticRows[0], null).steps[1].label, "Failed");
});

test("a missed step is labeled as missed", () => {
  assert.equal(table.toDetailModel(syntheticRows[0], null).steps[0].label, "Missed");
});

test("a case whose class has no checklist says so", () => {
  const row = { ...syntheticRows[0], checklist: null };
  assert.equal(table.toDetailModel(row, null).stepsNote, "This alert class has no checklist.");
});

test("a case with every step satisfied says so", () => {
  assert.equal(table.toDetailModel(syntheticRows[1], null).stepsNote, "None. Every checklist step was satisfied.");
});

// The status line shown when the table can't start.

test("the startup message gives the reason", () => {
  assert.equal(table.startupMessage(new Error("bad data")), "The case table could not be built: bad data.");
});

test("the startup message accepts a thrown non-error", () => {
  assert.equal(table.startupMessage("boom"), "The case table could not be built: boom.");
});

test("the startup message shows control characters as escapes", () => {
  assert.equal(table.startupMessage(new Error("a‮b")), "The case table could not be built: a\\u202eb.");
});

test("the startup message shortens a long reason", () => {
  assert.equal(
    table.startupMessage(new Error("x".repeat(500))),
    "The case table could not be built: " + "x".repeat(119) + "….",
  );
});

// Wiring: the whole script against a minimal fake DOM.

const scriptSource = readFileSync(new URL("../../src/detecttrace/templates/dashboard.js", import.meta.url), "utf8");
const demoText = JSON.stringify(demo);

class FakeElement {
  constructor(tag) {
    this.tagName = tag;
    this.children = [];
    this.parent = null;
    this.attributes = new Map();
    this.listeners = new Map();
    this.ownText = "";
    this.hidden = false;
  }
  setAttribute(name, value) { this.attributes.set(name, String(value)); }
  getAttribute(name) { return this.attributes.has(name) ? this.attributes.get(name) : null; }
  appendChild(child) { child.parent = this; this.children.push(child); return child; }
  replaceChildren() { this.children = []; }
  after(node) {
    node.parent = this.parent;
    this.parent.children.splice(this.parent.children.indexOf(this) + 1, 0, node);
  }
  addEventListener(type, listener) { this.listeners.set(type, listener); }
  click() { this.listeners.get("click")(); }
  focus() {}
  get textContent() { return this.ownText + this.children.map((child) => child.textContent).join(""); }
  set textContent(text) { this.children = []; this.ownText = String(text); }
  *walk() {
    yield this;
    for (const child of this.children) yield* child.walk();
  }
}

class FakeDocument {
  constructor() { this.root = new FakeElement("body"); }
  createElement(tag) { return new FakeElement(tag); }
  getElementById(id) { return this.root.walk().find((node) => node.getAttribute("id") === id) ?? null; }
  querySelectorAll() { return this.getElementById("case-filters").children; }
}

function createPage(resultsText) {
  const doc = new FakeDocument();
  const add = (parent, tag, id) => {
    const node = parent.appendChild(new FakeElement(tag));
    if (id) node.setAttribute("id", id);
    return node;
  };
  add(doc.root, "p", "case-status").textContent = "Loading the case table…";
  const ui = add(doc.root, "div", "case-ui");
  ui.hidden = true;
  const filters = add(ui, "div", "case-filters");
  add(filters, "button").setAttribute("data-filter", "all");
  add(filters, "button").setAttribute("data-filter", "dangerous");
  add(ui, "p", "case-count");
  add(ui, "tbody", "case-rows");
  add(ui, "button", "case-more");
  add(doc.root, "script", "dt-results").textContent = resultsText;
  return doc;
}

function runScript(doc) {
  new Function("document", scriptSource)(doc);
  return doc;
}

function firstToggle(doc) {
  return doc.getElementById("case-rows").children[0].children[0].children[0];
}

test("a table that can't start says why in the status line", () => {
  const doc = runScript(createPage("{not json"));
  assert.match(doc.getElementById("case-status").textContent, /^The case table could not be built: .+\.$/);
});

test("a table that can't start stays hidden", () => {
  const doc = runScript(createPage("{not json"));
  assert.equal(doc.getElementById("case-ui").hidden, true);
});

test("a table that starts clears the status line", () => {
  const doc = runScript(createPage(demoText));
  assert.equal(doc.getElementById("case-status").textContent, "");
});

test("a table that starts hides the status line", () => {
  const doc = runScript(createPage(demoText));
  assert.equal(doc.getElementById("case-status").hidden, true);
});

test("the count line is filled before the table is shown", () => {
  const doc = createPage(demoText);
  const ui = doc.getElementById("case-ui");
  let countWhenShown = null;
  Object.defineProperty(ui, "hidden", {
    set(value) { if (value === false) countWhenShown = doc.getElementById("case-count").textContent; },
  });
  runScript(doc);
  assert.equal(countWhenShown, "Showing 100 of 201 matching cases.");
});

test("a closed row controls nothing until its detail exists", () => {
  const doc = runScript(createPage(demoText));
  assert.equal(firstToggle(doc).getAttribute("aria-controls"), null);
});

test("an opened row controls its detail row", () => {
  const doc = runScript(createPage(demoText));
  firstToggle(doc).click();
  assert.equal(
    firstToggle(doc).getAttribute("aria-controls"),
    doc.getElementById("case-rows").children[1].getAttribute("id"),
  );
});

test("an opened row's headings sit one level under the section card", () => {
  const doc = runScript(createPage(demoText));
  firstToggle(doc).click();
  const detailRow = doc.getElementById("case-rows").children[1];
  assert.deepEqual(
    detailRow.walk().filter((node) => /^h\d$/.test(node.tagName)).map((node) => node.tagName).toArray(),
    ["h3", "h3"],
  );
});

test("an error while re-rendering says why in the status line", () => {
  const doc = runScript(createPage(demoText));
  doc.getElementById("case-rows").replaceChildren = () => { throw new Error("no room"); };
  doc.getElementById("case-filters").children[1].click();
  assert.equal(doc.getElementById("case-status").textContent, "The case table could not be built: no room.");
});
