"use client";

import * as React from "react";

/** Tailwind's `lg` breakpoint — the line between the phone/tablet chrome and the desktop chrome. */
const DESKTOP_QUERY = "(min-width: 1024px)";

/**
 * `true` on a desktop viewport, `false` below it, `null` until the component has mounted.
 *
 * The home page renders two DIFFERENT compositions — the reference-matched desktop front page
 * and the untouched mobile page — and must render only one of them: hiding the other with CSS
 * would still mount every card twice (two entrance animations, two sets of hooks, twice the
 * DOM behind `content-visibility`). A media query in JS mounts exactly one tree.
 *
 * `null` first, because the server has no viewport: an initial `false` would hydrate the mobile
 * page on a desktop and then swap it — a flash the reader would see on every hard load. The
 * page shows its skeleton for that one frame instead, which it already shows while data loads.
 */
export function useIsDesktop(): boolean | null {
  const [desktop, setDesktop] = React.useState<boolean | null>(null);
  React.useEffect(() => {
    const mql = window.matchMedia(DESKTOP_QUERY);
    const sync = () => setDesktop(mql.matches);
    sync();
    mql.addEventListener("change", sync);
    return () => mql.removeEventListener("change", sync);
  }, []);
  return desktop;
}
