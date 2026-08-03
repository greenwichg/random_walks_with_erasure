"use client";

import * as React from "react";

import { createDwellGate } from "@/lib/continuation";

/**
 * "The reader came back after actually being away" — the Story Continuation trigger
 * (docs/STORY_CONTINUATION_DESIGN.md §2.1).
 *
 * `ReadArticleButton` calls `window.open(href, "_blank")`, so the app is never navigated away from.
 * The return is therefore a `visibilitychange`, not a page load, and there is no `pageshow` or
 * router event to hang this on.
 *
 * **The dwell gate is the whole point.** A bare visibilitychange fires on every alt-tab, every
 * notification glance, and every password-manager popup, and a strip that appears after a four
 * second flick to another tab is noise attached to something the reader never did. `minHiddenMs`
 * (20 s by design) is the smallest interval that reliably separates "went and read something" from
 * "looked away", and the hidden duration is reported to the callback so
 * `continuation_shown.hiddenMs` can replace the constant with a measurement.
 *
 * Deliberately NOT debounced or throttled: each qualifying return is one event, and suppressing a
 * second one is the impression cap's job (`lib/continuation.mayShow`), not the trigger's.
 */
export function useVisibilityReturn(
  onReturn: (hiddenMs: number) => void,
  { minHiddenMs = 20_000, enabled = true }: { minHiddenMs?: number; enabled?: boolean } = {},
): void {
  // Held in a ref so changing the callback identity between renders never re-subscribes — a card
  // list re-rendering on every keystroke would otherwise tear down and rebuild this listener.
  const cb = React.useRef(onReturn);
  React.useEffect(() => {
    cb.current = onReturn;
  }, [onReturn]);

  React.useEffect(() => {
    if (!enabled || typeof document === "undefined") return;

    // The rule itself lives in lib/continuation.createDwellGate, tested at the millisecond. This
    // effect only wires the browser event to it — so the hook and its tests cannot hold two copies
    // of the same rule and drift apart.
    const gate = createDwellGate(minHiddenMs, (ms) => cb.current(ms));
    const onChange = () =>
      gate(document.visibilityState === "hidden" ? "hidden" : "visible", Date.now());

    document.addEventListener("visibilitychange", onChange);
    return () => document.removeEventListener("visibilitychange", onChange);
  }, [enabled, minHiddenMs]);
}
