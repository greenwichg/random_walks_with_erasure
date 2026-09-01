import type { LeanBucket, StoryCoverage } from "../domain/types.ts";

/**
 * Bias distribution — the outlet-level half of a story's political shape.
 *
 * The spectrum bar answers "how much of the COVERAGE leans each way"; this module answers
 * "WHICH outlets stand where", which is a different aggregation: an outlet that filed nine
 * articles is one mark, not nine. Rows come in grouped by publisher, each outlet resolved to
 * the house lean its rated rows carry, and outlets the registry doesn't rate land in
 * `untracked` — never silently in center, the same L2.2 rule the rows themselves follow
 * (an absent lean is unknown, not neutral).
 *
 * Callers pass MEMBER rows only (`splitCoverage(...).panel`) — attached Tier B coverage never
 * voted and must not appear to stand anywhere (M4 containment, client half).
 */

export interface OutletMark {
  publisher: string;
  /** Any one of the outlet's article URLs — enough to derive its site icons (hostIconCandidates). */
  url?: string;
  /** The server-resolved mark when the row carries one (publisherLogo on story coverage rows):
   *  a URL known to exist and to be large enough, tried before any host-derived guess. */
  logo?: string;
  logoFallbacks?: string[];
}

/** What a mark carries besides identity. First non-null wins per outlet, like the lean. */
export interface MarkFields {
  url?: string;
  logo?: string;
  logoFallbacks?: string[];
}

export function takeMarkFields(
  entry: MarkFields,
  row: { url?: string; publisherLogo?: string; publisherLogoFallbacks?: string[] },
): void {
  if (!entry.url && row.url) entry.url = row.url;
  if (!entry.logo && row.publisherLogo) {
    entry.logo = row.publisherLogo;
    if (row.publisherLogoFallbacks) entry.logoFallbacks = row.publisherLogoFallbacks;
  }
}

/** The mark itself. Logo keys appear only when known — a mark is `{publisher, url}` otherwise,
 *  exactly as before the logo tier existed. */
export function toMark(publisher: string, f: MarkFields): OutletMark {
  const mark: OutletMark = { publisher, url: f.url };
  if (f.logo) {
    mark.logo = f.logo;
    if (f.logoFallbacks) mark.logoFallbacks = f.logoFallbacks;
  }
  return mark;
}

export interface BiasGroups {
  buckets: Record<LeanBucket, OutletMark[]>;
  /** Outlets with no rated row — the registry doesn't rate them. */
  untracked: OutletMark[];
  /** Distinct rated outlets — the denominator for every percentage here. */
  ratedCount: number;
}

export const BIAS_BUCKETS: readonly LeanBucket[] = ["left", "center", "right"];

/** Neutral-first tie order — an even split never headlines a partisan side (the coverage
 *  plate's WASH_ORDER rule, applied to the summary sentence instead of a background tint). */
const DOMINANT_ORDER: readonly LeanBucket[] = ["center", "left", "right"];

export function groupOutletsByLean(coverage: StoryCoverage[]): BiasGroups {
  // Map insertion order = first-seen order, and coverage arrives newest-first — so the marks
  // rendered when a list is capped are the outlets most recently on the story.
  const byPublisher = new Map<string, { bucket: LeanBucket | null } & MarkFields>();
  for (const row of coverage) {
    if (!row.publisher) continue;
    let entry = byPublisher.get(row.publisher);
    if (!entry) {
      entry = { bucket: null };
      byPublisher.set(row.publisher, entry);
    }
    // First non-null wins on every field: the outlet's house lean doesn't change row to row,
    // but individual rows can omit it — a null row must never unrate an already-rated outlet.
    if (entry.bucket === null && row.leanBucket) entry.bucket = row.leanBucket;
    takeMarkFields(entry, row);
  }

  const buckets: Record<LeanBucket, OutletMark[]> = { left: [], center: [], right: [] };
  const untracked: OutletMark[] = [];
  for (const [publisher, entry] of byPublisher) {
    (entry.bucket ? buckets[entry.bucket] : untracked).push(toMark(publisher, entry));
  }
  return {
    buckets,
    untracked,
    ratedCount: buckets.left.length + buckets.center.length + buckets.right.length,
  };
}

/**
 * Split a group's marks at the display cap: what the capsule draws, and what its `+N` chip
 * stands for. One function so the NUMBER on the chip and the LIST behind it can never disagree
 * — that off-by-one is the whole bug class here (a chip promising 11 and opening 10).
 */
export function splitAtCap(
  outlets: OutletMark[],
  cap: number,
): { shown: OutletMark[]; hidden: OutletMark[] } {
  return { shown: outlets.slice(0, cap), hidden: outlets.slice(cap) };
}

/** The headline fact — "45% of the sources are Center" — or null when nothing is rated. */
export function dominantBucket(groups: BiasGroups): { bucket: LeanBucket; pct: number } | null {
  if (groups.ratedCount <= 0) return null;
  const bucket = DOMINANT_ORDER.reduce((a, b) =>
    groups.buckets[b].length > groups.buckets[a].length ? b : a,
  );
  return { bucket, pct: Math.round((groups.buckets[bucket].length / groups.ratedCount) * 100) };
}
