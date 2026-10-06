import { act, cleanup, fireEvent, render, screen } from "@testing-library/react";
import { afterEach, beforeEach, expect, it, vi } from "vitest";

import { POLL_MS } from "../poller";
import { StatusBar } from "./StatusBar";

const SERVED = { generation: 7, updated_at: "2026-10-05T12:00:00.000000Z", held_back_text: null };

function status(changes: Record<string, unknown>) {
  return { generation: 7, updated_at: "2026-10-05T12:00:00.000000Z", last_error: null, ...changes };
}

function stubFetch(respond: () => Promise<Response>) {
  const fetchMock = vi.fn(respond);
  vi.stubGlobal("fetch", fetchMock);
  return fetchMock;
}

function respondWith(body: unknown): () => Promise<Response> {
  return () => Promise.resolve(new Response(JSON.stringify(body)));
}

async function waitIntervals(count: number) {
  await act(async () => {
    await vi.advanceTimersByTimeAsync(POLL_MS * count);
  });
}

beforeEach(() => {
  vi.useFakeTimers();
});

afterEach(() => {
  cleanup();
  vi.useRealTimers();
  vi.unstubAllGlobals();
  vi.restoreAllMocks();
});

it("is a polite live region", () => {
  render(<StatusBar />);

  expect(screen.getByRole("status").getAttribute("aria-live")).toBe("polite");
});

it("starts empty", () => {
  render(<StatusBar served={SERVED} />);

  expect(screen.getByRole("status").textContent).toBe("");
});

it("never asks the network on an offline page", async () => {
  const fetchMock = stubFetch(respondWith(status({})));
  render(<StatusBar />);

  await waitIntervals(3);

  expect(fetchMock).not.toHaveBeenCalled();
});

it("asks the same server for its status", async () => {
  const fetchMock = stubFetch(respondWith(status({})));
  render(<StatusBar served={SERVED} />);

  await waitIntervals(1);

  expect(fetchMock).toHaveBeenCalledWith("/api/status", { credentials: "same-origin", cache: "no-store" });
});

it("says when newer results exist", async () => {
  stubFetch(respondWith(status({ generation: 8 })));
  render(<StatusBar served={SERVED} />);

  await waitIntervals(1);

  expect(screen.getByRole("status").querySelector("p")?.textContent).toBe("New data is available.");
});

it("reloads the page from the Reload button", async () => {
  stubFetch(respondWith(status({ generation: 8 })));
  const reload = vi.fn();
  vi.stubGlobal("location", { reload });
  render(<StatusBar served={SERVED} />);
  await waitIntervals(1);

  fireEvent.click(screen.getByRole("button", { name: "Reload" }));

  expect(reload).toHaveBeenCalledOnce();
});

it("offers no reload while the server has the page's results", async () => {
  stubFetch(respondWith(status({})));
  render(<StatusBar served={SERVED} />);

  await waitIntervals(1);

  expect(screen.queryByRole("button", { name: "Reload" })).toBeNull();
});

it("says the last update failed, with the time of the page's own data", async () => {
  stubFetch(respondWith(status({ last_error: "RuntimeError: boom" })));
  render(<StatusBar served={SERVED} />);

  await waitIntervals(1);

  expect(screen.getByRole("status").textContent).toBe(
    "Showing data from 2026-10-05 12:00 UTC; the last update failed.",
  );
});

it("warns once in the console for an outage however many checks fail", async () => {
  stubFetch(() => Promise.reject(new TypeError("Failed to fetch")));
  const warn = vi.spyOn(console, "warn").mockImplementation(() => {});
  render(<StatusBar served={SERVED} />);

  await waitIntervals(3);

  expect(warn.mock.calls).toEqual([["detecttrace: could not check for newer results: TypeError: Failed to fetch"]]);
});

it("shows nothing while the server cannot be reached", async () => {
  stubFetch(() => Promise.reject(new TypeError("Failed to fetch")));
  vi.spyOn(console, "warn").mockImplementation(() => {});
  render(<StatusBar served={SERVED} />);

  await waitIntervals(2);

  expect(screen.getByRole("status").textContent).toBe("");
});

it("stops asking once it is gone", async () => {
  const fetchMock = stubFetch(respondWith(status({})));
  const { unmount } = render(<StatusBar served={SERVED} />);
  await waitIntervals(1);

  unmount();
  await waitIntervals(3);

  expect(fetchMock).toHaveBeenCalledOnce();
});

it("says the last update failed and that newer data exists, in that order", async () => {
  stubFetch(respondWith(status({ generation: 8, last_error: "RuntimeError: boom" })));
  render(<StatusBar served={SERVED} />);

  await waitIntervals(1);

  expect(screen.getByRole("status").querySelector("p")?.textContent).toBe(
    "Showing data from 2026-10-05 12:00 UTC; the last update failed. New data is available.",
  );
});

it("reloads by itself on a ui page when newer results exist", async () => {
  stubFetch(respondWith(status({ generation: 8 })));
  const reload = vi.fn();
  vi.stubGlobal("location", { reload });
  render(<StatusBar served={SERVED} shouldReloadOnNewData />);

  await waitIntervals(1);

  expect(reload).toHaveBeenCalledOnce();
});

it("shows no new-data bar on a ui page", async () => {
  stubFetch(respondWith(status({ generation: 8 })));
  vi.stubGlobal("location", { reload: vi.fn() });
  render(<StatusBar served={SERVED} shouldReloadOnNewData />);

  await waitIntervals(1);

  expect(screen.getByRole("status").textContent).toBe("");
});

it("never reloads a served page by itself", async () => {
  stubFetch(respondWith(status({ generation: 8 })));
  const reload = vi.fn();
  vi.stubGlobal("location", { reload });
  render(<StatusBar served={SERVED} />);

  await waitIntervals(1);

  expect(reload).not.toHaveBeenCalled();
});
