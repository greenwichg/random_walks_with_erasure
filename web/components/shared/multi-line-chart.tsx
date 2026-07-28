"use client";

import dynamic from "next/dynamic";
import * as React from "react";
import type { MultiLineChart as Impl } from "./multi-line-chart-impl";

/**
 * Code-splitting boundary for MultiLineChart.
 *
 * Recharts is ~100 kB of the First Load JS and every route that statically imported one of these
 * charts paid for the whole library — including the HOME page, whose only chart is a 104px
 * sparkline in the rail. Measured before this split: `/` 394 kB vs `/stories` 293 kB, the two
 * pages differing by little else.
 *
 * The wrapper keeps the original module path and export name, so no call site changes and the
 * props stay exactly the impl's (`import type` is erased, so it pulls in no runtime code).
 * `ssr: false` is safe here: every consumer is already a client component fetching its data
 * through React Query, so these charts never rendered in server HTML anyway.
 *
 * The placeholder reserves the chart's own height — a lazy chart that collapses to zero and then
 * pushes the page down on arrival trades a bundle win for a layout shift.
 */
const Lazy = dynamic(() => import("./multi-line-chart-impl").then((m) => m.MultiLineChart), {
  ssr: false,
  loading: () => <div aria-hidden className="w-full animate-pulse rounded-lg bg-muted/40" />,
});

export function MultiLineChart(props: React.ComponentProps<typeof Impl>) {
  return (
    <div style={{ minHeight: props.height ?? 220 }}>
      <Lazy {...props} />
    </div>
  );
}
