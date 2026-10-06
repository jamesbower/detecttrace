import { afterEach, expect, it, vi } from "vitest";

import { holdReload, isReloadHeld, subscribeToReloadHold } from "./reload-hold";

const releases: (() => void)[] = [];

function hold(): () => void {
  const release = holdReload();
  releases.push(release);
  return release;
}

afterEach(() => {
  for (const release of releases.splice(0)) {
    release();
  }
});

it("is free with no holder", () => {
  expect(isReloadHeld()).toBe(false);
});

it("is held by one holder", () => {
  hold();

  expect(isReloadHeld()).toBe(true);
});

it("stays held while a second holder still holds it", () => {
  const releaseFirst = hold();
  hold();

  releaseFirst();

  expect(isReloadHeld()).toBe(true);
});

it("is free once every holder lets go", () => {
  const releaseFirst = hold();
  const releaseSecond = hold();

  releaseFirst();
  releaseSecond();

  expect(isReloadHeld()).toBe(false);
});

it("ignores a holder letting go twice", () => {
  const releaseFirst = hold();
  hold();

  releaseFirst();
  releaseFirst();

  expect(isReloadHeld()).toBe(true);
});

it("tells listeners only when the hold starts and ends", () => {
  const listener = vi.fn();
  const stop = subscribeToReloadHold(listener);
  const releaseFirst = hold();
  const releaseSecond = hold();
  releaseFirst();
  releaseSecond();
  stop();

  expect(listener).toHaveBeenCalledTimes(2);
});
