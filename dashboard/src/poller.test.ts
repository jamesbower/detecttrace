import { afterEach, expect, it, vi } from "vitest";

import { POLL_MS, barKey, createPoll, decideBar, formatTime, startPolling } from "./poller";

import type { Bar } from "./poller";

const PAGE = { generation: 7, updatedAt: "2026-10-05T12:00:00.000000Z" };

function status(changes: Record<string, unknown>) {
  return {
    generation: 7,
    updated_at: "2026-10-05T12:00:00.000000Z",
    recompute_running: false,
    last_error: null,
    last_error_at: null,
    ...changes,
  };
}

function respondWith(body: unknown): () => Promise<Response> {
  return () => Promise.resolve(new Response(JSON.stringify(body)));
}

function failToConnect(): Promise<Response> {
  return Promise.reject(new TypeError("Failed to fetch"));
}

afterEach(() => {
  vi.restoreAllMocks();
  vi.useRealTimers();
});

it("shows nothing when the server has the page's results", () => {
  expect(decideBar(status({}), PAGE)).toEqual({ isNewData: false, errorText: null });
});

it("offers a reload when the server has a newer generation", () => {
  expect(decideBar(status({ generation: 8 }), PAGE).isNewData).toBe(true);
});

it("offers no reload for an older generation", () => {
  expect(decideBar(status({ generation: 6 }), PAGE).isNewData).toBe(false);
});

it("offers a reload for the same generation computed later", () => {
  expect(decideBar(status({ updated_at: "2026-10-05T12:05:00.000000Z" }), PAGE).isNewData).toBe(true);
});

it("offers no reload for the same generation computed earlier", () => {
  expect(decideBar(status({ updated_at: "2026-10-05T11:55:00.000000Z" }), PAGE).isNewData).toBe(false);
});

it("offers no reload when the status has no generation", () => {
  expect(decideBar(status({ generation: "8" }), PAGE).isNewData).toBe(false);
});

it("says the last update failed and when the page's own data is from", () => {
  // The server's newer snapshot is not what the reader sees until they reload.
  const newer = status({ last_error: "RuntimeError: boom", updated_at: "2026-10-05T13:30:00Z" });

  expect(decideBar(newer, PAGE).errorText).toBe("Showing data from 2026-10-05 12:00 UTC; the last update failed.");
});

it("says the last update failed without a time when the page has none", () => {
  const bar = decideBar(status({ last_error: "RuntimeError: boom" }), { generation: 7, updatedAt: null });

  expect(bar.errorText).toBe("Showing data from an earlier update; the last update failed.");
});

it("treats an empty error as no error", () => {
  expect(decideBar(status({ last_error: "" }), PAGE).errorText).toBeNull();
});

it("gives bars that differ only by the error different keys", () => {
  expect(barKey(decideBar(status({ last_error: "RuntimeError: boom" }), PAGE))).not.toBe(
    barKey(decideBar(status({}), PAGE)),
  );
});

it("never shows the error text itself", () => {
  expect(decideBar(status({ last_error: "<b>secret path</b>" }), PAGE).errorText).not.toContain("secret");
});

it("gives two bars with the same content the same key", () => {
  expect(barKey(decideBar(status({ generation: 8 }), PAGE))).toBe(barKey(decideBar(status({ generation: 9 }), PAGE)));
});

it("gives bars with different content different keys", () => {
  expect(barKey(decideBar(status({ generation: 8 }), PAGE))).not.toBe(barKey(decideBar(status({}), PAGE)));
});

it("formats a time in UTC to the minute", () => {
  expect(formatTime("2026-10-05T09:07:59.999999+02:00")).toBe("2026-10-05 07:07 UTC");
});

it("polls every 30 seconds", () => {
  expect(POLL_MS).toBe(30_000);
});

it("reads a microsecond time where Date.parse takes at most milliseconds", () => {
  // Safari's Date.parse may refuse more than three fraction digits; the server sends six.
  const parse = Date.parse;
  vi.spyOn(Date, "parse").mockImplementation((text) => (/\.\d{4,}/.test(text) ? NaN : parse(text)));

  expect(formatTime("2026-10-05T12:34:56.123456Z")).toBe("2026-10-05 12:34 UTC");
});

it("logs one console line for an outage however many checks fail", async () => {
  const warnings: string[] = [];
  const poll = createPoll(() => {}, PAGE, failToConnect, (text) => warnings.push(text));

  await poll();
  await poll();

  expect(warnings).toEqual(["detecttrace: could not check for newer results: TypeError: Failed to fetch"]);
});

it("logs a new outage again after a check succeeds", async () => {
  const warnings: string[] = [];
  const responses = [failToConnect, respondWith(status({})), failToConnect];
  const poll = createPoll(
    () => {},
    PAGE,
    () => responses.shift()!(),
    (text) => warnings.push(text),
  );

  await poll();
  await poll();
  await poll();

  expect(warnings).toHaveLength(2);
});

it("counts an error status from the server as a failed check", async () => {
  const warnings: string[] = [];
  const poll = createPoll(
    () => {},
    PAGE,
    () => Promise.resolve(new Response("", { status: 503 })),
    (text) => warnings.push(text),
  );

  await poll();

  expect(warnings).toEqual(["detecttrace: could not check for newer results: Error: HTTP 503"]);
});

it("draws the bar once while the news stays the same", async () => {
  const shown: Bar[] = [];
  const poll = createPoll((bar) => shown.push(bar), PAGE, respondWith(status({ generation: 8 })), () => {});

  await poll();
  await poll();

  expect(shown).toHaveLength(1);
});

it("draws nothing while the server has the page's results", async () => {
  const shown: Bar[] = [];
  const poll = createPoll((bar) => shown.push(bar), PAGE, respondWith(status({})), () => {});

  await poll();

  expect(shown).toEqual([]);
});

it("makes the first check one interval after the start", () => {
  vi.useFakeTimers();
  const poll = vi.fn(() => Promise.resolve());
  startPolling(poll);

  vi.advanceTimersByTime(POLL_MS - 1);

  expect(poll).not.toHaveBeenCalled();
});

it("waits for a check to settle before scheduling the next", async () => {
  vi.useFakeTimers();
  let calls = 0;
  // A check that never settles, so no later one may start.
  startPolling(() => {
    calls += 1;
    return new Promise<void>(() => {});
  });

  await vi.advanceTimersByTimeAsync(POLL_MS * 3);

  expect(calls).toBe(1);
});

it("checks again one interval after a check settles", async () => {
  vi.useFakeTimers();
  let calls = 0;
  startPolling(() => {
    calls += 1;
    return Promise.resolve();
  });

  await vi.advanceTimersByTimeAsync(POLL_MS * 3);

  expect(calls).toBe(3);
});

it("checks no more once stopped", async () => {
  vi.useFakeTimers();
  let calls = 0;
  const stop = startPolling(() => {
    calls += 1;
    return Promise.resolve();
  });
  await vi.advanceTimersByTimeAsync(POLL_MS);

  stop();
  await vi.advanceTimersByTimeAsync(POLL_MS * 3);

  expect(calls).toBe(1);
});

it("schedules no check after one that was running when polling stopped", async () => {
  vi.useFakeTimers();
  let calls = 0;
  let settle = () => {};
  const stop = startPolling(() => {
    calls += 1;
    return new Promise<void>((resolve) => {
      settle = resolve;
    });
  });
  await vi.advanceTimersByTimeAsync(POLL_MS);

  stop();
  settle();
  await vi.advanceTimersByTimeAsync(POLL_MS * 3);

  expect(calls).toBe(1);
});
