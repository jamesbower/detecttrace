import { act, cleanup, fireEvent, render, screen, within } from "@testing-library/react";
import { userEvent } from "@testing-library/user-event";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { TERMS } from "../help/terms";
import { startRouter } from "../router";
import { TermHint } from "./TermHint";

const LABEL = TERMS.completeness.label;

// Testing Library waits on a zero timeout after each user action, which fake timers never fire.
// It advances them itself when it finds Jest's, so it is shown Vitest's under Jest's name.
beforeEach(() => {
  vi.useFakeTimers();
  vi.stubGlobal("jest", { advanceTimersByTime: (ms: number) => vi.advanceTimersByTime(ms) });
});

// jsdom's own styles hide every popover until it is shown, but jsdom cannot show one. This lets
// the pop-up display as it does once a browser has shown it.
const showPopovers = document.createElement("style");
showPopovers.textContent = "[popover] { display: block !important; }";
beforeEach(() => {
  document.head.append(showPopovers);
});

afterEach(() => {
  cleanup();
  showPopovers.remove();
  vi.useRealTimers();
  vi.restoreAllMocks();
  vi.unstubAllGlobals();
  startRouter("offline");
  window.history.replaceState(null, "", "/");
});

function setUpUser() {
  return userEvent.setup({ advanceTimers: (ms) => vi.advanceTimersByTime(ms) });
}

function renderHint() {
  render(<TermHint term="completeness" />);
  return screen.getByRole("button", { name: LABEL });
}

function queryPopup() {
  return screen.queryByRole("group", { name: LABEL });
}

function advance(ms: number) {
  act(() => {
    vi.advanceTimersByTime(ms);
  });
}

describe("on hover", () => {
  it("opens after the delay", async () => {
    const user = setUpUser();
    await user.hover(renderHint());

    advance(300);

    expect(queryPopup()).not.toBeNull();
  });

  it("stays closed before the delay", async () => {
    const user = setUpUser();
    await user.hover(renderHint());

    advance(299);

    expect(queryPopup()).toBeNull();
  });

  it("stays open while the pointer moves into the pop-up", async () => {
    const user = setUpUser();
    const trigger = renderHint();
    await user.hover(trigger);
    advance(300);

    await user.unhover(trigger);
    await user.hover(screen.getByRole("group", { name: LABEL }));
    advance(1000);

    expect(queryPopup()).not.toBeNull();
  });

  it("ignores a touch, which has no hover", () => {
    fireEvent.pointerOver(renderHint(), { pointerType: "touch" });

    advance(300);

    expect(queryPopup()).toBeNull();
  });

  it("ignores a pen, which hovers only by accident", () => {
    fireEvent.pointerOver(renderHint(), { pointerType: "pen" });

    advance(300);

    expect(queryPopup()).toBeNull();
  });

  it("closes after the delay once the pointer leaves both", async () => {
    const user = setUpUser();
    const trigger = renderHint();
    await user.hover(trigger);
    advance(300);

    await user.unhover(trigger);
    advance(200);

    expect(queryPopup()).toBeNull();
  });
});

// A click on a pop-up that hover opened pins it open, rather than closing what the user meant
// to keep.
describe("hover, then a click", () => {
  async function hoverThenClick(user: ReturnType<typeof setUpUser>) {
    const trigger = renderHint();
    await user.hover(trigger);
    advance(300);
    await user.click(trigger);
    return trigger;
  }

  it("keeps the pop-up open", async () => {
    const user = setUpUser();

    await hoverThenClick(user);

    expect(queryPopup()).not.toBeNull();
  });

  it("keeps it open after the pointer leaves", async () => {
    const user = setUpUser();
    const trigger = await hoverThenClick(user);

    await user.unhover(trigger);
    advance(200);

    expect(queryPopup()).not.toBeNull();
  });

  it("closes it on the next click", async () => {
    const user = setUpUser();
    const trigger = await hoverThenClick(user);

    await user.click(trigger);

    expect(queryPopup()).toBeNull();
  });
});

it("stays open on a click inside the pop-up", async () => {
  const user = setUpUser();
  renderHint();
  await user.tab();

  await user.click(screen.getByText(TERMS.completeness.short));

  expect(queryPopup()).not.toBeNull();
});

// Safari moves focus to no element when a button is pressed, so the pop-up's blur names nothing.
it("closes on a click of the trigger that leaves focus nowhere", async () => {
  const user = setUpUser();
  const trigger = renderHint();
  await user.tab();
  await user.click(screen.getByText(TERMS.completeness.short));

  fireEvent.pointerDown(trigger);
  act(() => screen.getByRole("group", { name: LABEL }).blur());
  fireEvent.click(trigger);

  expect(queryPopup()).toBeNull();
});

it("opens on focus", async () => {
  const user = setUpUser();
  renderHint();

  await user.tab();

  expect(queryPopup()).not.toBeNull();
});

it("opens on a click", async () => {
  const user = setUpUser();

  await user.click(renderHint());

  expect(queryPopup()).not.toBeNull();
});

it("closes on a second click", async () => {
  const user = setUpUser();
  const trigger = renderHint();
  await user.click(trigger);

  await user.click(trigger);

  expect(queryPopup()).toBeNull();
});

describe("aria-expanded", () => {
  it("is false while closed", () => {
    expect(renderHint().getAttribute("aria-expanded")).toBe("false");
  });

  it("is true while open", async () => {
    const user = setUpUser();
    const trigger = renderHint();

    await user.click(trigger);

    expect(trigger.getAttribute("aria-expanded")).toBe("true");
  });
});

describe("aria-controls", () => {
  it("is absent while closed", () => {
    expect(renderHint().hasAttribute("aria-controls")).toBe(false);
  });

  it("names the pop-up while open", () => {
    const trigger = renderHint();

    fireEvent.click(trigger);

    expect(trigger.getAttribute("aria-controls")).toBe(screen.getByRole("group", { name: LABEL }).id);
  });
});

describe("Escape", () => {
  it("closes a pop-up that hover opened, with focus elsewhere", async () => {
    const user = setUpUser();
    await user.hover(renderHint());
    advance(300);

    await user.keyboard("{Escape}");

    expect(queryPopup()).toBeNull();
  });

  it("leaves focus alone when it was elsewhere", async () => {
    const user = setUpUser();
    await user.hover(renderHint());
    advance(300);

    await user.keyboard("{Escape}");

    expect(document.activeElement).toBe(document.body);
  });

  it("closes the pop-up from inside it", async () => {
    const user = setUpUser();
    renderHint();
    await user.tab();
    await user.tab();

    await user.keyboard("{Escape}");

    expect(queryPopup()).toBeNull();
  });

  it("returns focus to the trigger", async () => {
    const user = setUpUser();
    const trigger = renderHint();
    await user.tab();
    await user.tab();

    await user.keyboard("{Escape}");

    expect(document.activeElement).toBe(trigger);
  });

  it("closes the pop-up from the trigger", async () => {
    const user = setUpUser();
    renderHint();
    await user.tab();

    await user.keyboard("{Escape}");

    expect(queryPopup()).toBeNull();
  });
});

it("closes on a press outside", async () => {
  render(
    <>
      <TermHint term="completeness" />
      <p>Elsewhere</p>
    </>,
  );
  fireEvent.click(screen.getByRole("button", { name: LABEL }));

  fireEvent.pointerDown(screen.getByText("Elsewhere"));

  expect(queryPopup()).toBeNull();
});

it("closes when focus leaves both the trigger and the pop-up", async () => {
  const user = setUpUser();
  render(
    <>
      <TermHint term="completeness" />
      <button type="button">Next</button>
    </>,
  );
  await user.tab();
  await user.tab();

  await user.tab();

  expect(queryPopup()).toBeNull();
});

// Clicks without a press or a focus change, so only the one-open rule can close the first.
it("closes the open hint when another opens", () => {
  render(
    <>
      <TermHint term="completeness" />
      <TermHint term="kappa" />
    </>,
  );
  fireEvent.click(screen.getByRole("button", { name: LABEL }));

  fireEvent.click(screen.getByRole("button", { name: TERMS.kappa.label }));

  expect(queryPopup()).toBeNull();
});

it("says the term's short explanation", () => {
  fireEvent.click(renderHint());

  expect(within(screen.getByRole("group", { name: LABEL })).getByText(TERMS.completeness.short)).not.toBeNull();
});

it("shows its children in place of the term's name", () => {
  render(<TermHint term="completeness">Completeness</TermHint>);

  expect(screen.getByRole("button", { name: "Completeness" })).not.toBeNull();
});

describe("the Help link", () => {
  it("names the section by path on a served page", () => {
    startRouter("ui", "http:");
    fireEvent.click(renderHint());

    expect(screen.getByRole("link", { name: "More in Help" }).getAttribute("href")).toBe(
      "/help#evidence-completeness",
    );
  });

  it("names the section by hash on a page opened from disk", () => {
    startRouter("offline");
    fireEvent.click(renderHint());

    expect(screen.getByRole("link", { name: "More in Help" }).getAttribute("href")).toBe(
      "#/help#evidence-completeness",
    );
  });
});

describe("placement", () => {
  // The pop-up is 200 by 100 and sits in the top layer, so it is placed in the screen's
  // coordinates. The wrapper is elsewhere on purpose: nothing may be measured from it.
  function stubLayout(trigger: Partial<DOMRect>, viewport: { width: number; height: number }) {
    const triggerRect = { top: 0, bottom: 0, left: 0, right: 0, width: 0, height: 0, ...trigger };
    const rects: Record<string, Partial<DOMRect>> = {
      "term-hint": { top: 50, bottom: 70, left: 30, right: 130, width: 100, height: 20 },
      "term-hint-trigger": triggerRect,
      "term-hint-popup": { top: 0, bottom: 100, left: 0, right: 200, width: 200, height: 100 },
    };
    vi.spyOn(HTMLElement.prototype, "getBoundingClientRect").mockImplementation(function (this: HTMLElement) {
      return { ...rects[this.className] } as DOMRect;
    });
    vi.stubGlobal("innerWidth", viewport.width);
    vi.stubGlobal("innerHeight", viewport.height);
  }

  it("opens below the trigger when there is room", () => {
    stubLayout({ top: 100, bottom: 120, left: 100 }, { width: 800, height: 600 });

    fireEvent.click(renderHint());

    expect(screen.getByRole("group", { name: LABEL }).style.top).toBe("120px");
  });

  it("flips above the trigger when there is no room below", () => {
    stubLayout({ top: 560, bottom: 580, left: 100 }, { width: 800, height: 600 });

    fireEvent.click(renderHint());

    expect(screen.getByRole("group", { name: LABEL }).style.top).toBe("460px");
  });

  it("opens below on a short screen when below has more room", () => {
    stubLayout({ top: 20, bottom: 40, left: 100 }, { width: 800, height: 120 });

    fireEvent.click(renderHint());

    expect(screen.getByRole("group", { name: LABEL }).style.top).toBe("40px");
  });

  it("keeps clear of the top gutter on a short screen", () => {
    stubLayout({ top: 100, bottom: 120, left: 100 }, { width: 800, height: 140 });

    fireEvent.click(renderHint());

    expect(screen.getByRole("group", { name: LABEL }).style.top).toBe("16px");
  });

  it("lines up with the trigger's left edge when there is room", () => {
    stubLayout({ top: 100, bottom: 120, left: 100 }, { width: 800, height: 600 });

    fireEvent.click(renderHint());

    expect(screen.getByRole("group", { name: LABEL }).style.left).toBe("100px");
  });

  it("keeps clear of the left gutter", () => {
    stubLayout({ top: 100, bottom: 120, left: 4 }, { width: 800, height: 600 });

    fireEvent.click(renderHint());

    expect(screen.getByRole("group", { name: LABEL }).style.left).toBe("16px");
  });

  it("keeps clear of the right gutter", () => {
    stubLayout({ top: 100, bottom: 120, left: 700 }, { width: 800, height: 600 });

    fireEvent.click(renderHint());

    expect(screen.getByRole("group", { name: LABEL }).style.left).toBe("584px");
  });
});

// jsdom has no Popover API, so it is stubbed here and removed again after each test.
describe("the top layer", () => {
  let showPopover: ReturnType<typeof vi.fn>;

  beforeEach(() => {
    showPopover = vi.fn();
    Object.assign(HTMLElement.prototype, { showPopover });
  });

  afterEach(() => {
    Reflect.deleteProperty(HTMLElement.prototype, "showPopover");
  });

  it("is a manual popover", () => {
    fireEvent.click(renderHint());

    expect(screen.getByRole("group", { name: LABEL }).getAttribute("popover")).toBe("manual");
  });

  it("shows the pop-up there on open", () => {
    fireEvent.click(renderHint());

    expect(showPopover.mock.contexts).toEqual([screen.getByRole("group", { name: LABEL })]);
  });
});

// A fixed pop-up would stay put while its trigger moves, so it follows the trigger. It does not
// close: focus scrolls an off-screen trigger into view, and the pop-up focus opened must stay.
describe("follows its trigger", () => {
  function stubTriggerAt(top: number) {
    const rects: Record<string, Partial<DOMRect>> = {
      "term-hint-trigger": { top, bottom: top + 20, left: 100, right: 200, width: 100, height: 20 },
      "term-hint-popup": { top: 0, bottom: 100, left: 0, right: 200, width: 200, height: 100 },
    };
    vi.spyOn(HTMLElement.prototype, "getBoundingClientRect").mockImplementation(function (this: HTMLElement) {
      return { ...rects[this.className] } as DOMRect;
    });
  }

  it("on a scroll in any container", () => {
    vi.stubGlobal("innerHeight", 600);
    render(
      <div data-testid="scroller">
        <TermHint term="completeness" />
      </div>,
    );
    stubTriggerAt(100);
    fireEvent.click(screen.getByRole("button", { name: LABEL }));

    stubTriggerAt(40);
    fireEvent.scroll(screen.getByTestId("scroller"));

    expect(screen.getByRole("group", { name: LABEL }).style.top).toBe("60px");
  });

  it("on a resize", () => {
    vi.stubGlobal("innerHeight", 600);
    stubTriggerAt(100);
    fireEvent.click(renderHint());

    stubTriggerAt(40);
    fireEvent(window, new Event("resize"));

    expect(screen.getByRole("group", { name: LABEL }).style.top).toBe("60px");
  });
});
