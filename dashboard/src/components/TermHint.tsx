// A toggletip for a measure's name: the name is a button, and its pop-up says in a sentence
// what the measure means, with a link to the Help page's section on it. Hover opens it after a
// short delay, focus opens it at once, and a click or tap toggles it. A click, Enter or Space on
// a pop-up that hover or focus opened pins it open instead, until the next one; so does a press
// inside the pop-up. Escape closes it.
// The pop-up stays beside its trigger in the document, for the tab order and the focus checks,
// but shows in the top layer: a panel's clip-path or a table's scroll area would cut it off.
import { useCallback, useEffect, useId, useLayoutEffect, useRef, useState } from "react";

import { TERMS } from "../help/terms";
import { HELP_PATH } from "../router";
import { PageLink } from "./PageLink";
import "./TermHint.css";

import type * as React from "react";
import type { TermKey } from "../help/terms";

const OPEN_DELAY_MS = 300;
const CLOSE_DELAY_MS = 200;
// The pop-up keeps this far from the screen's sides: --sp-4.
const VIEWPORT_GUTTER_PX = 16;
// The pop-up keeps this far from its trigger, so it never covers the trigger's focus ring:
// --focus-width plus --focus-offset.
const FOCUS_RING_EXTENT_PX = 5;

// Only one pop-up is open at a time: opening one closes the last.
let closeOpenHint: (() => void) | null = null;

type Props = { term: TermKey; children?: React.ReactNode };
type OpenedBy = "hover" | "focus" | "click";

export function TermHint({ term, children }: Props) {
  const [openedBy, setOpenedBy] = useState<OpenedBy | null>(null);
  const isOpen = openedBy !== null;
  const [position, setPosition] = useState<React.CSSProperties | undefined>(undefined);
  const popupId = useId();
  const wrapperRef = useRef<HTMLSpanElement>(null);
  const triggerRef = useRef<HTMLButtonElement>(null);
  const popupRef = useRef<HTMLSpanElement>(null);
  const timerRef = useRef<number | null>(null);
  // A press focuses the button before its click; that focus must not open the pop-up, or the
  // click would close it again at once. Safari focuses nothing on the press instead, and the
  // pop-up's blur then must not close it either: the click decides.
  const isPressingRef = useRef(false);

  const clearTimer = useCallback(() => {
    if (timerRef.current !== null) {
      window.clearTimeout(timerRef.current);
      timerRef.current = null;
    }
  }, []);

  // An open pop-up keeps the way it was first opened, except that focus takes over from hover:
  // a focused trigger's pop-up must not close because the pointer left.
  const open = useCallback(
    (by: OpenedBy) => {
      clearTimer();
      setOpenedBy((current) => (current === null || (current === "hover" && by === "focus") ? by : current));
    },
    [clearTimer],
  );

  // Taken out of the document, the pop-up leaves the top layer by itself.
  const close = useCallback(() => {
    clearTimer();
    setOpenedBy(null);
  }, [clearTimer]);

  const place = useCallback(() => {
    const trigger = triggerRef.current;
    const popup = popupRef.current;
    if (trigger !== null && popup !== null) {
      setPosition(placePopup(trigger.getBoundingClientRect(), popup.getBoundingClientRect()));
    }
  }, []);

  useEffect(() => clearTimer, [clearTimer]);

  // Safari fires neither a click nor a pointercancel for a mouse press dragged off the button,
  // and never focuses it, so the release ends the press. Only a mouse's: a tap focuses the
  // button after its pointerup.
  useEffect(() => {
    function handlePointerUp(event: PointerEvent) {
      if (event.pointerType === "mouse") {
        isPressingRef.current = false;
      }
    }
    document.addEventListener("pointerup", handlePointerUp);
    return () => document.removeEventListener("pointerup", handlePointerUp);
  }, []);

  useEffect(() => {
    if (!isOpen) {
      return;
    }
    closeOpenHint?.();
    closeOpenHint = close;
    function handlePointerDown(event: PointerEvent) {
      if (!(event.target instanceof Node) || !wrapperRef.current?.contains(event.target)) {
        close();
      }
    }
    // On the document, so Escape also closes a pop-up that hover opened, wherever focus is.
    // Not an Escape another handler took, such as a dialog's or a search box's.
    function handleKeyDown(event: KeyboardEvent) {
      if (event.key !== "Escape" || event.defaultPrevented) {
        return;
      }
      // Focus first: the trigger's focus would open the pop-up, and the close comes after it.
      if (document.activeElement !== null && wrapperRef.current?.contains(document.activeElement)) {
        triggerRef.current?.focus();
      }
      close();
    }
    document.addEventListener("pointerdown", handlePointerDown);
    document.addEventListener("keydown", handleKeyDown);
    return () => {
      document.removeEventListener("pointerdown", handlePointerDown);
      document.removeEventListener("keydown", handleKeyDown);
      if (closeOpenHint === close) {
        closeOpenHint = null;
      }
    };
  }, [isOpen, close]);

  // Shown before it is measured: a hidden popover has no size.
  useLayoutEffect(() => {
    const popup = popupRef.current;
    if (!isOpen || popup === null) {
      return;
    }
    // Older browsers and jsdom have no Popover API; there the pop-up stays in the page's layers.
    if (typeof popup.showPopover === "function") {
      popup.showPopover();
    }
    place();
    // The pop-up is fixed to the screen, so it moves with its trigger. It does not close: focus
    // scrolls an off-screen trigger into view, after the focus has opened the pop-up. Captured,
    // so a scroll inside any container counts too.
    window.addEventListener("scroll", place, { capture: true });
    window.addEventListener("resize", place);
    return () => {
      window.removeEventListener("scroll", place, { capture: true });
      window.removeEventListener("resize", place);
    };
  }, [isOpen, place]);

  function scheduleOpen(event: React.PointerEvent) {
    // Only a mouse hovers on purpose. A tap's click toggles, and its pointer leaving must not
    // close; a pen hovers only on its way to a tap.
    if (event.pointerType === "mouse") {
      clearTimer();
      timerRef.current = window.setTimeout(() => open("hover"), OPEN_DELAY_MS);
    }
  }

  // Leaving cancels a hover that has not opened yet, and closes only what hover opened.
  function scheduleClose(event: React.PointerEvent) {
    if (event.pointerType === "mouse") {
      clearTimer();
      if (openedBy === "hover") {
        timerRef.current = window.setTimeout(close, CLOSE_DELAY_MS);
      }
    }
  }

  function handleTriggerPointerDown() {
    isPressingRef.current = true;
  }

  // A press the browser takes over, such as a touch that turns into a scroll, never clicks.
  function handleTriggerPointerCancel() {
    isPressingRef.current = false;
  }

  function handleTriggerClick() {
    isPressingRef.current = false;
    if (openedBy === "hover" || openedBy === "focus") {
      clearTimer();
      setOpenedBy("click");
    } else if (isOpen) {
      close();
    } else {
      open("click");
    }
  }

  // A press inside the pop-up means the reader is keeping it, so the pointer leaving must not
  // close it.
  function handlePopupPointerDown() {
    clearTimer();
    setOpenedBy("click");
  }

  function handleTriggerFocus() {
    if (!isPressingRef.current) {
      open("focus");
    }
  }

  // Clears a press dragged off the button, which never clicked.
  function handleTriggerBlur(event: React.FocusEvent) {
    isPressingRef.current = false;
    closeUnlessFocusInside(event);
  }

  function handlePopupBlur(event: React.FocusEvent) {
    if (!isPressingRef.current) {
      closeUnlessFocusInside(event);
    }
  }

  function closeUnlessFocusInside(event: React.FocusEvent) {
    const next = event.relatedTarget;
    if (!(next instanceof Node) || !wrapperRef.current?.contains(next)) {
      close();
    }
  }

  const { label, short, anchor } = TERMS[term];
  // A toggletip without a live region: a screen reader reads the pop-up next after the button.
  return (
    <span className="term-hint" ref={wrapperRef}>
      <button
        type="button"
        className="term-hint-trigger"
        ref={triggerRef}
        aria-expanded={isOpen}
        aria-controls={isOpen ? popupId : undefined}
        onClick={handleTriggerClick}
        onFocus={handleTriggerFocus}
        onBlur={handleTriggerBlur}
        onPointerDown={handleTriggerPointerDown}
        onPointerCancel={handleTriggerPointerCancel}
        onPointerEnter={scheduleOpen}
        onPointerLeave={scheduleClose}
      >
        {children ?? label}
      </button>
      {isOpen && (
        <span
          id={popupId}
          role="group"
          popover="manual"
          aria-label={label}
          className="term-hint-popup"
          ref={popupRef}
          style={position}
          // Focusable by a press, so a press on its text keeps focus inside and it stays open.
          tabIndex={-1}
          onBlur={handlePopupBlur}
          onPointerDown={handlePopupPointerDown}
          onPointerEnter={clearTimer}
          onPointerLeave={scheduleClose}
        >
          <span className="term-hint-text">{short}</span>
          <PageLink path={HELP_PATH} section={anchor}>
            More in Help
          </PageLink>
        </span>
      )}
    </span>
  );
}

// Below the trigger's focus ring when it fits; otherwise on whichever side has more room, and
// above it never past the top gutter. Moved sideways to stay within the screen's gutters. In the
// screen's coordinates, as the pop-up is fixed.
function placePopup(trigger: DOMRect, popup: DOMRect): React.CSSProperties {
  const below = trigger.bottom + FOCUS_RING_EXTENT_PX;
  const above = trigger.top - FOCUS_RING_EXTENT_PX;
  const roomBelow = window.innerHeight - below;
  const isAbove = popup.height > roomBelow && above > roomBelow;
  const maxLeft = window.innerWidth - popup.width - VIEWPORT_GUTTER_PX;
  return {
    top: isAbove ? Math.max(VIEWPORT_GUTTER_PX, above - popup.height) : below,
    left: Math.max(VIEWPORT_GUTTER_PX, Math.min(trigger.left, maxLeft)),
  };
}
