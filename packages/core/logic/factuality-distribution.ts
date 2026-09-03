import type { FactualityLevel, FactualityRating, StoryCoverage } from "../domain/types.ts";
import { FACTUALITY_LEVELS } from "../domain/types.ts";
import type { MarkFields, OutletMark } from "./bias-distribution.ts";
import { takeMarkFields, toMark } from "./bias-distribution.ts";

/**
 * Factuality distribution — how the outlets on a story are rated for factual reporting,
 * aggregated exactly like bias and ownership: one mark per outlet however many articles it
 * filed, levels in the rater's own fixed order, and outlets nobody has rated in an explicit
 * `unrated` slice — counted in every percentage (a story that is 70% unrated must say so),
 * never folded into a middle level.
 *
 * Three things this module refuses to do, all of them for the same reason — the verdict is a
 * THIRD PARTY's claim about a named news organisation, not ours:
 *
 *   * It never derives a level. An outlet with no verdict is `unrated`, full stop; there is no
 *     inference from lean, from credibility, or from the rest of the story.
 *   * It never collapses the six levels into three. "Mostly Factual" is a mild reservation and
 *     "Mixed" a serious one (registry FACTUALITY), and one word for both is a false statement.
 *   * It never drops the attribution. Every mark keeps the rating that produced it, and
 *     {@link factualityAttribution} rolls the raters and the OLDEST read-date up for the panel's
 *     one-line credit — oldest because understating freshness is the only one of the two errors
 *     that cannot mislead.
 *
 * Callers pass MEMBER rows only (`splitCoverage(...).panel`) — attached Tier B coverage never
 * voted and must not appear to stand anywhere (M4 containment, client half).
 */

/** Fixed render + color order: the rater's own scale, best to worst. `unrated` is not a level —
 *  it renders last, always muted. */
export const FACTUALITY_ORDER: readonly FactualityLevel[] = FACTUALITY_LEVELS;

export type FactualityKey = FactualityLevel | "unrated";

/** A mark that also carries the verdict behind it, so the full breakdown can attribute each row
 *  without a second lookup. `rating` is absent on the `unrated` slice's marks, by construction. */
export type FactualityMark = OutletMark & { rating?: FactualityRating };

export type FactualitySlice = {
  level: FactualityKey;
  outlets: FactualityMark[];
};

export interface FactualityGroups {
  /** Non-empty slices in FACTUALITY_ORDER, then `unrated` last when non-empty. */
  slices: FactualitySlice[];
  totalOutlets: number;
  /** Distinct outlets carrying a verdict — zero means there is nothing to draw. */
  ratedCount: number;
}

const VALID = new Set<string>(FACTUALITY_LEVELS);

export function groupOutletsByFactuality(coverage: StoryCoverage[]): FactualityGroups {
  // Map insertion order = first-seen order (coverage arrives newest-first), same as the bias and
  // ownership groupings — the marks kept when a list is capped are the most recent outlets.
  const byPublisher = new Map<string, { rating: FactualityRating | null } & MarkFields>();
  for (const row of coverage) {
    if (!row.publisher) continue;
    let entry = byPublisher.get(row.publisher);
    if (!entry) {
      entry = { rating: null };
      byPublisher.set(row.publisher, entry);
    }
    // First non-null wins, like the lean: an outlet's verdict doesn't change row to row, but a
    // row can omit it, and a null row must never unrate an already-rated outlet. A level outside
    // the rater's vocabulary (a future engine ahead of this client) stays unrated — rendering it
    // as unrated is honest; inventing a slice for a word we cannot label is not.
    if (entry.rating === null && row.factuality && VALID.has(row.factuality.value)) {
      entry.rating = row.factuality;
    }
    takeMarkFields(entry, row);
  }

  const of = new Map<FactualityKey, FactualityMark[]>();
  for (const [publisher, entry] of byPublisher) {
    const key: FactualityKey = entry.rating ? entry.rating.value : "unrated";
    const list = of.get(key) ?? [];
    const mark: FactualityMark = toMark(publisher, entry);
    if (entry.rating) mark.rating = entry.rating;
    list.push(mark);
    of.set(key, list);
  }

  const slices: FactualitySlice[] = [];
  let rated = 0;
  for (const level of FACTUALITY_ORDER) {
    const outlets = of.get(level);
    if (outlets && outlets.length > 0) {
      slices.push({ level, outlets });
      rated += outlets.length;
    }
  }
  const unrated = of.get("unrated");
  if (unrated && unrated.length > 0) slices.push({ level: "unrated", outlets: unrated });

  return { slices, totalOutlets: rated + (unrated?.length ?? 0), ratedCount: rated };
}

/**
 * The headline fact — "38% of the sources are rated High". The share is over ALL outlets
 * (unrated included), so it always agrees with the bar and the ring; the winner is the largest
 * RATED level (ties -> earliest in FACTUALITY_ORDER, i.e. the rater's better level, which is the
 * conservative direction for a tie between a good and a bad verdict). Null when nothing is
 * rated — no headline beats "100% unrated" dressed as a finding.
 */
export function dominantFactuality(
  groups: FactualityGroups,
): { level: FactualityLevel; pct: number } | null {
  let best: FactualitySlice | null = null;
  for (const s of groups.slices) {
    if (s.level === "unrated") continue;
    if (!best || s.outlets.length > best.outlets.length) best = s;
  }
  if (!best || groups.totalOutlets === 0) return null;
  return {
    level: best.level as FactualityLevel,
    pct: Math.round((best.outlets.length / groups.totalOutlets) * 100),
  };
}

/**
 * Who rated these outlets, and how stale the oldest of those verdicts is — the panel's one-line
 * credit, so a page never shows a rater's levels without naming the rater and a date.
 *
 * The date is the OLDEST `asOf` across the shown verdicts, not the newest: the line stands for
 * every rating on the panel, and a newest-date credit would imply all of them were read then.
 * Understating freshness can only make a reader check the source; overstating it cannot.
 */
export function factualityAttribution(
  groups: FactualityGroups,
): { sources: string[]; asOf: string } | null {
  const sources = new Set<string>();
  let oldest = "";
  for (const slice of groups.slices) {
    for (const outlet of slice.outlets) {
      if (!outlet.rating) continue;
      sources.add(outlet.rating.source);
      if (!oldest || outlet.rating.asOf < oldest) oldest = outlet.rating.asOf;
    }
  }
  if (sources.size === 0 || !oldest) return null;
  return { sources: [...sources].sort(), asOf: oldest };
}
