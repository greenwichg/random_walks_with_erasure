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

/** Distribute item indexes 0..n-1 round-robin into `count` columns. */
export function distributeIndexes(n: number, count: number): number[][] {
  const columns: number[][] = Array.from({ length: Math.max(1, count) }, () => []);
  for (let i = 0; i < n; i += 1) columns[i % columns.length]!.push(i);
  return columns;
}
