// A toggletip for a measure's name: the name is a button, and its pop-up says in a sentence
// what the measure means, with a link to the Help page's section on it. Hover opens it after a
// short delay, focus opens it at once, and a click or tap toggles it. Escape closes it.
// The pop-up stays beside its trigger in the document, for the tab order and the focus checks,
// but shows in the top layer: a panel's clip-path or a table's scroll area would cut it off.
import { useCallback, useEffect, useId, useLayoutEffect, useRef, useState } from "react";

import { TERMS } from "../help/terms";
import { toHref } from "../router";
import "./TermHint.css";

import type * as React from "react";
import type { TermKey } from "../help/terms";

const OPEN_DELAY_MS = 300;
const CLOSE_DELAY_MS = 200;
// The pop-up keeps this far from the screen's sides: --sp-4.
const VIEWPORT_GUTTER_PX = 16;

// Only one pop-up is open at a time: opening one closes the last.
let closeOpenHint: (() => void) | null = null;

type Props = { term: TermKey; children?: React.ReactNode };

export function TermHint({ term, children }: Props) {
  const [isOpen, setIsOpen] = useState(false);
  const [position, setPosition] = useState<React.CSSProperties | undefined>(undefined);
  const popupId = useId();
  const wrapperRef = useRef<HTMLSpanElement>(null);
  const triggerRef = useRef<HTMLButtonElement>(null);
  const popupRef = useRef<HTMLSpanElement>(null);
  const timerRef = useRef<number | null>(null);
  // A press focuses the button before its click; that focus must not open the pop-up, or the
  // click would close it again at once.
  const isPressingRef = useRef(false);

  const clearTimer = useCallback(() => {
    if (timerRef.current !== null) {
      window.clearTimeout(timerRef.current);
      timerRef.current = null;
    }
  }, []);

  const open = useCallback(() => {
    clearTimer();
    setIsOpen(true);
  }, [clearTimer]);

  // Hidden here, while the pop-up is still in the document; React removes it after.
  const close = useCallback(() => {
    clearTimer();
    hidePopup(popupRef.current);
    setIsOpen(false);
  }, [clearTimer]);

  const place = useCallback(() => {
    const trigger = triggerRef.current;
    const popup = popupRef.current;
    if (trigger !== null && popup !== null) {
      setPosition(placePopup(trigger.getBoundingClientRect(), popup.getBoundingClientRect()));
    }
  }, []);

  useEffect(() => clearTimer, [clearTimer]);

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
    document.addEventListener("pointerdown", handlePointerDown);
    // The pop-up is fixed to the screen, so it moves with its trigger. It does not close: focus
    // scrolls an off-screen trigger into view, after the focus has opened the pop-up. Captured,
    // so a scroll inside any container counts too.
    window.addEventListener("scroll", place, { capture: true });
    window.addEventListener("resize", place);
    return () => {
      document.removeEventListener("pointerdown", handlePointerDown);
      window.removeEventListener("scroll", place, { capture: true });
      window.removeEventListener("resize", place);
      if (closeOpenHint === close) {
        closeOpenHint = null;
      }
    };
  }, [isOpen, close, place]);

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
    return () => hidePopup(popup);
  }, [isOpen, place]);

  function scheduleOpen(event: React.PointerEvent) {
    // Touch has no hover: a tap's click toggles, and its pointer leaving must not close.
    if (event.pointerType === "mouse") {
      clearTimer();
      timerRef.current = window.setTimeout(open, OPEN_DELAY_MS);
    }
  }

  function scheduleClose(event: React.PointerEvent) {
    if (event.pointerType === "mouse") {
      clearTimer();
      timerRef.current = window.setTimeout(close, CLOSE_DELAY_MS);
    }
  }

  function handleTriggerPointerDown() {
    isPressingRef.current = true;
  }

  function handleTriggerClick() {
    isPressingRef.current = false;
    if (isOpen) {
      close();
    } else {
      open();
    }
  }

  function handleTriggerFocus() {
    if (!isPressingRef.current) {
      open();
    }
  }

  function handleBlur(event: React.FocusEvent) {
    isPressingRef.current = false;
    const next = event.relatedTarget;
    if (!(next instanceof Node) || !wrapperRef.current?.contains(next)) {
      close();
    }
  }

  function handleKeyDown(event: React.KeyboardEvent) {
    if (event.key === "Escape" && isOpen) {
      event.preventDefault();
      // Focus first: the trigger's focus would open the pop-up, and the close comes after it.
      triggerRef.current?.focus();
      close();
    }
  }

  const { label, short, anchor } = TERMS[term];
  return (
    <span className="term-hint" ref={wrapperRef}>
      <button
        type="button"
        className="term-hint-trigger"
        ref={triggerRef}
        aria-expanded={isOpen}
        aria-controls={popupId}
        onClick={handleTriggerClick}
        onFocus={handleTriggerFocus}
        onBlur={handleBlur}
        onKeyDown={handleKeyDown}
        onPointerDown={handleTriggerPointerDown}
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
          onBlur={handleBlur}
          onKeyDown={handleKeyDown}
          onPointerEnter={clearTimer}
          onPointerLeave={scheduleClose}
        >
          <span className="term-hint-text">{short}</span>
          <a href={helpHref(anchor)}>More in Help</a>
        </span>
      )}
    </span>
  );
}

// Below the trigger, or above it when the screen has no room below; moved sideways to stay
// within the screen's gutters. In the screen's coordinates, as the pop-up is fixed.
function placePopup(trigger: DOMRect, popup: DOMRect): React.CSSProperties {
  const isAbove = trigger.bottom + popup.height > window.innerHeight;
  const maxLeft = window.innerWidth - popup.width - VIEWPORT_GUTTER_PX;
  return {
    top: isAbove ? trigger.top - popup.height : trigger.bottom,
    left: Math.max(VIEWPORT_GUTTER_PX, Math.min(trigger.left, maxLeft)),
  };
}

// A pop-up already taken out of the document was hidden when it left.
function hidePopup(popup: HTMLElement | null): void {
  // Older browsers and jsdom have no Popover API.
  if (popup !== null && popup.isConnected && typeof popup.hidePopover === "function") {
    popup.hidePopover();
  }
}

function helpHref(anchor: string): string {
  return `${toHref("/help", new URLSearchParams())}#${anchor}`;
}
