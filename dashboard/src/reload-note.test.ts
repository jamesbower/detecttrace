import { afterEach, describe, expect, it, vi } from "vitest";

import { reloadPage, reloadWithNote, takeReloadNote } from "./reload-note";

afterEach(() => {
  sessionStorage.clear();
  vi.unstubAllGlobals();
  vi.restoreAllMocks();
});

it("reloads the page", () => {
  const reload = vi.fn();
  vi.stubGlobal("location", { reload });

  reloadWithNote({ focusId: "data-step-config", shouldAnnounce: true });

  expect(reload).toHaveBeenCalledOnce();
});

it("hands the note to the reloaded page", () => {
  vi.stubGlobal("location", { reload: vi.fn() });
  reloadWithNote({ focusId: "data-step-config", shouldAnnounce: true });

  expect(takeReloadNote()).toEqual({ focusId: "data-step-config", shouldAnnounce: true });
});

it("hands the note over only once", () => {
  vi.stubGlobal("location", { reload: vi.fn() });
  reloadWithNote({ focusId: null, shouldAnnounce: true });
  takeReloadNote();

  expect(takeReloadNote()).toBeNull();
});

it("has no note after a reload the page did not make", () => {
  expect(takeReloadNote()).toBeNull();
});

it("reloads even when storage refuses the note", () => {
  vi.spyOn(Storage.prototype, "setItem").mockImplementation(() => {
    throw new Error("SecurityError");
  });
  const reload = vi.fn();
  vi.stubGlobal("location", { reload });

  reloadWithNote({ focusId: null, shouldAnnounce: true });

  expect(reload).toHaveBeenCalledOnce();
});

it("has no note when storage cannot be read", () => {
  vi.spyOn(Storage.prototype, "getItem").mockImplementation(() => {
    throw new Error("SecurityError");
  });

  expect(takeReloadNote()).toBeNull();
});

it("ignores a note it cannot read", () => {
  sessionStorage.setItem("detecttrace:reload-note", "{not json");

  expect(takeReloadNote()).toBeNull();
});

describe("a reload the page makes by itself", () => {
  afterEach(() => {
    document.body.replaceChildren();
  });

  function showDataSteps() {
    document.body.innerHTML = `
      <section data-reload-focus="data-step-upload"><input id="file"></section>
      <section data-reload-focus="data-step-config"><button id="change">Change configuration</button></section>`;
  }

  it("notes the step the focus was in", () => {
    vi.stubGlobal("location", { reload: vi.fn() });
    showDataSteps();
    document.getElementById("change")?.focus();

    reloadPage();

    expect(takeReloadNote()).toEqual({ focusId: "data-step-config", shouldAnnounce: true });
  });

  it("notes no step when the focus was outside the steps", () => {
    vi.stubGlobal("location", { reload: vi.fn() });
    showDataSteps();

    reloadPage();

    expect(takeReloadNote()).toEqual({ focusId: null, shouldAnnounce: true });
  });

  it("leaves no note away from the Data page's steps", () => {
    vi.stubGlobal("location", { reload: vi.fn() });

    reloadPage();

    expect(takeReloadNote()).toBeNull();
  });

  it("reloads away from the Data page's steps too", () => {
    const reload = vi.fn();
    vi.stubGlobal("location", { reload });

    reloadPage();

    expect(reload).toHaveBeenCalledOnce();
  });
});
