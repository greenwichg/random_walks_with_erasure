"use client";

import * as React from "react";

/**
 * Measures an element's width via ResizeObserver, with a sensible initial guess
 * so charts render on the very first paint (fixes Recharts' ResponsiveContainer
 * collapsing to 0 inside flex/grid parents and in headless screenshots).
 *
 * MB1: the seed is mobile-first (320px, the narrowest supported phone) so the pre-measure
 * frame — SSR HTML and the first client paint before the layout effect — never overflows a
 * small screen. The observer still corrects up to the real container width on the next frame.
 * Chart wrappers additionally set `min-w-0 overflow-hidden` so the seeded SVG can never inflate
 * a grid/flex ancestor past the viewport (the CSS `min-width:auto` trap).
 */
export function useMeasure<T extends HTMLElement>(initialWidth = 320) {
  const ref = React.useRef<T>(null);
  const [width, setWidth] = React.useState(initialWidth);

  React.useLayoutEffect(() => {
    const el = ref.current;
    if (!el) return;
    const measure = () => {
      const w = el.clientWidth;
      if (w > 0) setWidth(w);
    };
    measure();
    const ro = new ResizeObserver(measure);
    ro.observe(el);
    return () => ro.disconnect();
  }, []);

  return { ref, width };
}
