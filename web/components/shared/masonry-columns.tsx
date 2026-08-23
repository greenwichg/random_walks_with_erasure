"use client";

import * as React from "react";
import {
  MASONRY_BREAKPOINTS,
  MASONRY_DEFAULT_COUNT,
  distributeIndexes,
} from "@/lib/masonry-order";

/**
 * Masonry for card streams — deterministic round-robin columns, not CSS multicol.
 *
 * Why not `columns-*`: multicol reads COLUMN-major (item 2 renders under item 1 — wrong for a
 * recency stream), balances globally (appending reshuffles cards the reader already saw, so
 * "Load More" needed per-batch blocks whose seams leaked whitespace at every boundary).
 *
 * Round-robin (`item i -> column i % count`) gives row-major reading order, and appending only
 * ever PUSHES onto column ends — existing cards can never move, so batches and their seams
 * disappear. The trade-off, accepted: distribution ignores heights, so column bottoms drift by
 * natural variance (typically under one card) instead of being height-balanced.
 *
 * The distribution law and the breakpoints live in lib/masonry-order.ts (pure, pinned by
 * lib/discover-layout.test.ts); this component adds only the matchMedia wiring and the DOM.
 * Column count mirrors the grid breakpoints (1 / md:2 / xl:3). These streams render
 * client-side after data arrives, so the count is settled before cards first paint.
 */
export function MasonryColumns<T>({
  items,
  itemKey,
  render,
}: {
  items: T[];
  itemKey: (item: T) => string;
  render: (item: T, index: number) => React.ReactNode;
}) {
  const count = useColumnCount();
  const columns = distributeIndexes(items.length, count);
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
