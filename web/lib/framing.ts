import type { LeanBucket, StoryCoverage } from "@/types/domain";

/**
 * "How each side frames it" — the derivation behind the story page's framing juxtaposition.
 *
 * The CoverageList already lets a reader look at left OR right coverage one filter at a time; what
 * it never does is put the two headlines NEXT TO each other, and the juxtaposition is the product
 * thesis in its most concrete form: same event, different words. This module derives that view
 * from the coverage rows the story already carries — no new data, no fabricated facets.
 *
 * Honesty rules, in order:
 *  - Unknown-lean rows are EXCLUDED, never bucketed (L2.2). A side exists only if a rated outlet
 *    actually wrote on that side.
 *  - At least two sides must be present, else `null` — one voice is not a comparison, and the
 *    one-sided case is already the blindspot banner's job.
 *  - Each side's representative is its most recent REPORTING row when one exists, else its most
 *    recent row of any register — comparing news headlines to news headlines where the data
 *    allows, and saying so via `register` on the returned row rather than hiding the fallback.
 *  - Deterministic: ties on `publishedAt` break on publisher, then headline, so the same story
 *    renders the same comparison on every visit.
 */
export interface FramingSide {
  side: LeanBucket;
  /** The representative row (carries headline, publisher, url, publishedAt, register). */
  row: StoryCoverage;
  /** How many rated rows sit on this side — the "N sources" chip. */
  count: number;
}

const SIDE_ORDER: LeanBucket[] = ["left", "center", "right"];

function newerFirst(a: StoryCoverage, b: StoryCoverage): number {
  const t = (b.publishedAt || "").localeCompare(a.publishedAt || "");
  if (t !== 0) return t;
  const p = a.publisher.localeCompare(b.publisher);
  if (p !== 0) return p;
  return a.headline.localeCompare(b.headline);
}

function representative(rows: StoryCoverage[]): StoryCoverage {
  const reporting = rows.filter((r) => r.register === "reporting");
  const pool = reporting.length > 0 ? reporting : rows;
  // Non-null: callers only pass non-empty buckets, and the fallback keeps the pool non-empty.
  return [...pool].sort(newerFirst)[0]!;
}

export function framingComparison(coverage: StoryCoverage[]): FramingSide[] | null {
  const buckets = new Map<LeanBucket, StoryCoverage[]>();
  for (const row of coverage) {
    if (!row.leanBucket) continue; // unrated outlet: no side, no guess
    const list = buckets.get(row.leanBucket);
    if (list) list.push(row);
    else buckets.set(row.leanBucket, [row]);
  }
  const sides = SIDE_ORDER.filter((s) => (buckets.get(s)?.length ?? 0) > 0);
  if (sides.length < 2) return null;
  return sides.map((side) => {
    const rows = buckets.get(side)!;
    return { side, row: representative(rows), count: rows.length };
  });
}
