"use client";

import * as React from "react";

/**
 * Measures an element's width via ResizeObserver, with a sensible initial guess
 * so charts render on the very first paint (fixes Recharts' ResponsiveContainer
 * collapsing to 0 inside flex/grid parents and in headless screenshots).
 */
export function useMeasure<T extends HTMLElement>(initialWidth = 600) {
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
