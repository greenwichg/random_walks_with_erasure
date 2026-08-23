/**
 * The masonry layout law, extracted pure so it is testable: deterministic round-robin
 * distribution (item i → column i % count) and the column-count breakpoints.
 *
 * Two properties the card streams (Discover, Search, Saved) rely on, pinned in
 * lib/discover-layout.test.ts:
 *
 *   1. ROW-MAJOR READING ORDER — reading across the column tops reconstructs the input
 *      order, so a recency stream still reads newest-first (CSS multicol reads
 *      column-major, which is why MasonryColumns never uses it).
 *   2. APPEND STABILITY — distributing a longer list keeps every earlier item in the same
 *      column at the same position: "Load More" only pushes onto column ends, so cards the
 *      reader has already seen never move.
 */

/** Column-count breakpoints — mirrors the grid scale used across the app (1 / md:2 / xl:3). */
export const MASONRY_BREAKPOINTS = [
  { query: "(min-width: 1280px)", count: 3 }, // xl
  { query: "(min-width: 768px)", count: 2 }, // md
] as const;

/** Below the smallest breakpoint: a single column (phones). */
export const MASONRY_DEFAULT_COUNT = 1;

/** Distribute item indexes 0..n-1 round-robin into `count` columns. Count-based: even ITEM
 *  counts per column, but column HEIGHTS drift without bound when heights cluster (measured on
 *  Discover, 2026-08-23: image cards ~2.3× text cards, and a text-heavy column ended ~4 cards
 *  early). Streams with height-clustered cards should use {@link distributeByHeight}. */
export function distributeIndexes(n: number, count: number): number[][] {
  const columns: number[][] = Array.from({ length: Math.max(1, count) }, () => []);
  for (let i = 0; i < n; i += 1) columns[i % columns.length]!.push(i);
  return columns;
}

/** Distribute item indexes 0..n-1 into `count` columns by ESTIMATED height: each item joins the
 *  currently-shortest column (ties → leftmost). Deterministic, and placement of item i depends
 *  only on items 0..i-1, so it keeps the append-stability law: a longer list never moves an
 *  earlier item. Each column stays chronological top-to-bottom, and the bottom skew is bounded
 *  by ONE item's height — the guarantee count-based round-robin cannot give. The estimate only
 *  needs fair RATIOS between cards, not pixel truth; its error is absorbed by that same bound. */
export function distributeByHeight(
  n: number,
  count: number,
  heightOf: (i: number) => number,
): number[][] {
  const cols = Math.max(1, count);
  const columns: number[][] = Array.from({ length: cols }, () => []);
  const heights: number[] = new Array(cols).fill(0);
  for (let i = 0; i < n; i += 1) {
    let c = 0;
    for (let k = 1; k < cols; k += 1) if (heights[k]! < heights[c]!) c = k;
    columns[c]!.push(i);
    heights[c] = heights[c]! + Math.max(1, heightOf(i));
  }
  return columns;
}
