import assert from "node:assert/strict";
import { createRequire } from "node:module";
import { test } from "node:test";

const require = createRequire(import.meta.url);
const serve = require("../../src/detecttrace/templates/dashboard-serve.js");

const page = { generation: 7, updatedAt: "2026-10-05T12:00:00.000000Z" };

function status(changes) {
  return {
    generation: 7,
    updated_at: "2026-10-05T12:00:00.000000Z",
    recompute_running: false,
    last_error: null,
    last_error_at: null,
    ...changes,
  };
}

test("shows nothing when the server has the page's results", () => {
  assert.deepEqual(serve.decideBar(status({}), page), { isNewData: false, errorText: null });
});

test("offers a reload when the server has a newer generation", () => {
  assert.equal(serve.decideBar(status({ generation: 8 }), page).isNewData, true);
});

test("offers no reload for an older generation", () => {
  assert.equal(serve.decideBar(status({ generation: 6 }), page).isNewData, false);
});

test("offers a reload for the same generation computed later", () => {
  assert.equal(
    serve.decideBar(status({ updated_at: "2026-10-05T12:05:00.000000Z" }), page).isNewData,
    true,
  );
});

test("offers no reload for the same generation computed earlier", () => {
  assert.equal(
    serve.decideBar(status({ updated_at: "2026-10-05T11:55:00.000000Z" }), page).isNewData,
    false,
  );
});

test("offers no reload when the status has no generation", () => {
  assert.equal(serve.decideBar(status({ generation: "8" }), page).isNewData, false);
});

test("says the last update failed and when the page's own data is from", () => {
  // The server's newer snapshot is not what the reader sees until they reload.
  const newer = status({ last_error: "RuntimeError: boom", updated_at: "2026-10-05T13:30:00Z" });
  assert.equal(
    serve.decideBar(newer, page).errorText,
    "Showing data from 2026-10-05 12:00 UTC; the last update failed.",
  );
});

test("says the last update failed without a time when the page has none", () => {
  assert.equal(
    serve.decideBar(status({ last_error: "RuntimeError: boom" }), { generation: 7, updatedAt: null })
      .errorText,
    "Showing data from an earlier update; the last update failed.",
  );
});

test("treats an empty error as no error", () => {
  assert.equal(serve.decideBar(status({ last_error: "" }), page).errorText, null);
});

test("gives bars that differ only by the error different keys", () => {
  assert.notEqual(
    serve.barKey(serve.decideBar(status({ last_error: "RuntimeError: boom" }), page)),
    serve.barKey(serve.decideBar(status({}), page)),
  );
});

test("never shows the error text itself", () => {
  assert.equal(
    serve.decideBar(status({ last_error: "<b>secret path</b>" }), page).errorText.includes("secret"),
    false,
  );
});

test("gives two bars with the same content the same key", () => {
  assert.equal(
    serve.barKey(serve.decideBar(status({ generation: 8 }), page)),
    serve.barKey(serve.decideBar(status({ generation: 9 }), page)),
  );
});

test("gives bars with different content different keys", () => {
  assert.notEqual(
    serve.barKey(serve.decideBar(status({ generation: 8 }), page)),
    serve.barKey(serve.decideBar(status({}), page)),
  );
});

test("formats a time in UTC to the minute", () => {
  assert.equal(serve.formatTime("2026-10-05T09:07:59.999999+02:00"), "2026-10-05 07:07 UTC");
});

test("polls every 30 seconds", () => {
  assert.equal(serve.POLL_MS, 30000);
});

test("reads a microsecond time where Date.parse takes at most milliseconds", (t) => {
  // Safari's Date.parse may refuse more than three fraction digits; the server sends six.
  const parse = Date.parse;
  t.mock.method(Date, "parse", (text) => (/\.\d{4,}/.test(text) ? NaN : parse(text)));
  assert.equal(serve.formatTime("2026-10-05T12:34:56.123456Z"), "2026-10-05 12:34 UTC");
});

// A region that counts how often the bar is drawn, and a document that can build one.
function createRegion() {
  return {
    firstChild: null,
    drawCount: 0,
    removeChild() { this.firstChild = null; },
    appendChild(child) { this.firstChild = child; this.drawCount += 1; },
  };
}

function createElement() {
  return { appendChild() {}, addEventListener() {} };
}

function respondWith(body) {
  return () => Promise.resolve({ ok: true, json: () => Promise.resolve(body) });
}

function failToConnect() {
  return Promise.reject(new TypeError("Failed to fetch"));
}

test("logs one console line for an outage however many checks fail", async () => {
  const warnings = [];
  const poll = serve.createPoll(createRegion(), page, failToConnect, (text) => warnings.push(text));
  await poll();
  await poll();
  assert.deepEqual(warnings, [
    "detecttrace: could not check for newer results: TypeError: Failed to fetch",
  ]);
});

test("logs a new outage again after a check succeeds", async () => {
  const warnings = [];
  const responses = [failToConnect, respondWith(status({})), failToConnect];
  const poll = serve.createPoll(
    createRegion(),
    page,
    () => responses.shift()(),
    (text) => warnings.push(text),
  );
  await poll();
  await poll();
  await poll();
  assert.equal(warnings.length, 2);
});

test("draws the bar once while the news stays the same", async (t) => {
  globalThis.document = { createElement };
  t.after(() => { delete globalThis.document; });
  const region = createRegion();
  const poll = serve.createPoll(region, page, respondWith(status({ generation: 8 })), () => {});
  await poll();
  await poll();
  assert.equal(region.drawCount, 1);
});
