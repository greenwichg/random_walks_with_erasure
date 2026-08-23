"use client";

import * as React from "react";
import {
  MASONRY_BREAKPOINTS,
  MASONRY_DEFAULT_COUNT,
  distributeByHeight,
  distributeIndexes,
} from "@/lib/masonry-order";

/**
 * Masonry for card streams — deterministic columns, not CSS multicol.
 *
 * Why not `columns-*`: multicol reads COLUMN-major (item 2 renders under item 1 — wrong for a
 * recency stream), balances globally (appending reshuffles cards the reader already saw, so
 * "Load More" needed per-batch blocks whose seams leaked whitespace at every boundary).
 *
 * Both placements share the append law — appending only ever PUSHES onto column ends, so cards
 * the reader has seen never move. Without `estimateHeight`, round-robin (`item i -> column
 * i % count`): row-major reading order, even item counts, but column HEIGHTS drift without
 * bound when heights cluster (Discover measured a text-heavy column ending ~4 cards early).
 * With `estimateHeight`, shortest-column placement: bottoms level within one card, columns stay
 * chronological, reading order is approximately row-major instead of exactly.
 *
 * The distribution laws and the breakpoints live in lib/masonry-order.ts (pure, pinned by
 * lib/discover-layout.test.ts); this component adds only the matchMedia wiring and the DOM.
 * Column count mirrors the grid breakpoints (1 / md:2 / xl:3). These streams render
 * client-side after data arrives, so the count is settled before cards first paint.
 */
export function MasonryColumns<T>({
  items,
  itemKey,
  render,
  estimateHeight,
}: {
  items: T[];
  itemKey: (item: T) => string;
  render: (item: T, index: number) => React.ReactNode;
  /** Estimated card height (px, fair ratios — not pixel truth). Provide it when card heights
   *  cluster (image vs text cards) so columns balance; omit for round-robin. */
  estimateHeight?: (item: T) => number;
}) {
  const count = useColumnCount();
  const columns = estimateHeight
    ? distributeByHeight(items.length, count, (i) => estimateHeight(items[i]!))
    : distributeIndexes(items.length, count);
  return (
    <div className="flex items-start gap-5">
      {columns.map((column, c) => (
        <div key={c} className="flex min-w-0 flex-1 flex-col gap-5">
          {column.map((i) => (
            <div key={itemKey(items[i]!)}>{render(items[i]!, i)}</div>
          ))}
        </div>
      ))}
    </div>
  );
}

function currentCount(): number {
  if (typeof window === "undefined") return 3;
  for (const { query, count } of MASONRY_BREAKPOINTS) {
    if (window.matchMedia(query).matches) return count;
  }
  return MASONRY_DEFAULT_COUNT;
}

function useColumnCount(): number {
  const [count, setCount] = React.useState(currentCount);
  React.useEffect(() => {
    const update = () => setCount(currentCount());
    const lists = MASONRY_BREAKPOINTS.map(({ query }) => window.matchMedia(query));
    lists.forEach((l) => l.addEventListener("change", update));
    update();
    return () => lists.forEach((l) => l.removeEventListener("change", update));
  }, []);
  return count;
}
