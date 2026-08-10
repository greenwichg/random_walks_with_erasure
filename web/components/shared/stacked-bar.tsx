"use client";

import dynamic from "next/dynamic";
import * as React from "react";
import { ChartEmpty } from "./states";
import type { StackedBar as Impl } from "./stacked-bar-impl";

// The chart module also owned this shared type. Re-exported from the same path it always lived at,
// so the split stays invisible to importers (type-only: still no runtime cost).
export type { BarSeries } from "./stacked-bar-impl";

/**
 * Code-splitting boundary for StackedBar.
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
const Lazy = dynamic(() => import("./stacked-bar-impl").then((m) => m.StackedBar), {
  ssr: false,
  loading: () => <div aria-hidden className="w-full animate-pulse rounded-lg bg-muted/40" />,
});

export function StackedBar(props: React.ComponentProps<typeof Impl>) {
  // Nothing to plot is its own answer, and a different one from "still loading". Recharts draws an
  // empty grid for an empty series, which reads as a broken card rather than a quiet one — visible
  // on the period report pages, where a window can legitimately contain no reads. Returning before
  // <Lazy> also means a card with no data never fetches the ~100 kB Recharts chunk at all.
  if (!props.data?.length) return <ChartEmpty height={props.height ?? 220} />;
  return (
    <div style={{ minHeight: props.height ?? 220 }}>
      <Lazy {...props} />
    </div>
  );
}
