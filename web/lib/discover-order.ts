/**
 * River ordering — publisher interleave for Discover's scan rows.
 *
 * One outlet's feed poll lands as a burst (measured on the live river: Sportskeeda filed 6 of the
 * 12 visible rows), and recency-only order renders the burst verbatim. The front-page tier is
 * already publisher-diverse; this extends the idea to the river with the weakest rule that fixes
 * the symptom: no two ADJACENT rows from the same publisher while any other publisher's row is
 * still pending. Greedy and deterministic — walk the list, and when the next item repeats the
 * previous publisher, pull forward the nearest item from a different one.
 *
 * Nothing is hidden and nothing is demoted out of the visible set: the output is a permutation,
 * every deferred item lands at the next legal slot, and when only the bursting publisher remains
 * (or a publisher filter makes everything one outlet) the input order passes through unchanged.
 * Pure function of the full fetched list, computed once per fetch — Load More reveals more of a
 * FIXED order, so rows the reader has seen never move.
 */
export function interleavePublishers<T extends { publisher?: string }>(items: T[]): T[] {
  const out: T[] = [];
  const rest = [...items];
  while (rest.length) {
    const prev = out.length ? out[out.length - 1]?.publisher : undefined;
    let i = rest.findIndex((it) => it.publisher !== prev);
    if (i < 0) i = 0; // only the bursting publisher left: emit in original order, hide nothing
    out.push(rest.splice(i, 1)[0] as T);
  }
  return out;
}
