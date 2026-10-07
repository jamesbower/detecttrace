// A toggletip for a measure's name: the name is a button, and its pop-up says in a sentence
// what the measure means, with a link to the Help page's section on it. Hover opens it after a
// short delay, focus opens it at once, and a click or tap toggles it. Escape closes it.
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

  const close = useCallback(() => {
    clearTimer();
    setIsOpen(false);
  }, [clearTimer]);

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
    return () => {
      document.removeEventListener("pointerdown", handlePointerDown);
      if (closeOpenHint === close) {
        closeOpenHint = null;
      }
    };
  }, [isOpen, close]);

  useLayoutEffect(() => {
    const wrapper = wrapperRef.current;
    const trigger = triggerRef.current;
    const popup = popupRef.current;
    if (!isOpen || wrapper === null || trigger === null || popup === null) {
      return;
    }
    setPosition(
      placePopup(wrapper.getBoundingClientRect(), trigger.getBoundingClientRect(), popup.getBoundingClientRect()),
    );
  }, [isOpen]);

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
// within the screen's gutters. The offsets are from the wrapper, which the pop-up is placed in.
function placePopup(wrapper: DOMRect, trigger: DOMRect, popup: DOMRect): React.CSSProperties {
  const isAbove = trigger.bottom + popup.height > window.innerHeight;
  const top = isAbove ? trigger.top - popup.height : trigger.bottom;
  const maxLeft = window.innerWidth - popup.width - VIEWPORT_GUTTER_PX;
  const left = Math.max(VIEWPORT_GUTTER_PX, Math.min(trigger.left, maxLeft));
  return { top: top - wrapper.top, left: left - wrapper.left };
}

function helpHref(anchor: string): string {
  return `${toHref("/help", new URLSearchParams())}#${anchor}`;
}
