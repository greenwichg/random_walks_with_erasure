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
  // `void | Promise<void>`: the strip's handler revalidates the offer against the engine before
  // showing it, so it is async. The gate never awaits — a return is a fire-and-forget signal, and
  // nothing here has a result to wait for.
  onReturn: (hiddenMs: number) => void | Promise<void>,
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

    // The hide has usually ALREADY happened by the time this listener attaches, and missing it
    // meant the strip could never appear at all. The Read click fires the prefetch and then
    // `window.open` immediately; the tab goes hidden on that same tick, while the prefetch is still
    // in flight. Only when it resolves does the candidate arm, the card re-render, and this effect
    // run — strictly after the `hidden` event it needed to see. The gate then treats the return as
    // a visible-without-a-preceding-hide and correctly, uselessly, ignores it.
    //
    // Seeding from the CURRENT visibility state closes that race. The dwell is measured from attach
    // rather than from the true hide, so it under-reports by the prefetch's own latency — tens of
    // milliseconds against a 20 s threshold, and erring short is the safe direction.
    if (document.visibilityState === "hidden") gate("hidden", Date.now());

    const onChange = () =>
      gate(document.visibilityState === "hidden" ? "hidden" : "visible", Date.now());

    document.addEventListener("visibilitychange", onChange);
    return () => document.removeEventListener("visibilitychange", onChange);
  }, [enabled, minHiddenMs]);
}
