import type { OwnershipCategory, StoryCoverage } from "../domain/types.ts";
import type { OutletMark } from "./bias-distribution.ts";

/**
 * Ownership distribution — who CONTROLS the outlets on a story, aggregated like the bias
 * panel: one mark per outlet however many articles it filed, categories in one fixed order so
 * a color always means the same owner type, and outlets the registry doesn't classify in an
 * explicit `unknown` slice — counted in every percentage (a story that is 80% unclassified
 * must say so), never folded into `other` (L2.2: unknown is not a category of owner).
 *
 * Callers pass MEMBER rows only (`splitCoverage(...).panel`) — attached Tier B coverage never
 * voted and must not appear to stand anywhere (M4 containment, client half).
 */

/** Fixed render + color order. `unknown` is not an owner type — it renders last, always muted. */
export const OWNERSHIP_ORDER: readonly OwnershipCategory[] = [
  "independent", "individual", "telecom", "government",
  "private_equity", "conglomerate", "corporation", "other",
];

export type OwnershipSlice = {
  category: OwnershipCategory | "unknown";
  outlets: OutletMark[];
};

export interface OwnershipGroups {
  /** Non-empty slices in OWNERSHIP_ORDER, then `unknown` last when non-empty. */
  slices: OwnershipSlice[];
  totalOutlets: number;
  knownCount: number;
}

const VALID = new Set<string>(OWNERSHIP_ORDER);

export function groupOutletsByOwnership(coverage: StoryCoverage[]): OwnershipGroups {
  // Map insertion order = first-seen order (coverage arrives newest-first), same as the bias
  // grouping — the marks kept when a list is capped are the outlets most recently on the story.
  const byPublisher = new Map<string, { category: OwnershipCategory | null; url?: string }>();
  for (const row of coverage) {
    if (!row.publisher) continue;
    let entry = byPublisher.get(row.publisher);
    if (!entry) {
      entry = { category: null };
      byPublisher.set(row.publisher, entry);
    }
    // First non-null wins; a token outside the vocabulary (a future engine ahead of this client)
    // stays null — rendering it as `unknown` is honest, inventing a slice for it is not.
    if (entry.category === null && row.ownership && VALID.has(row.ownership)) {
      entry.category = row.ownership;
    }
    if (!entry.url && row.url) entry.url = row.url;
  }

  const of = new Map<OwnershipCategory | "unknown", OutletMark[]>();
  for (const [publisher, { category, url }] of byPublisher) {
    const key = category ?? "unknown";
    const list = of.get(key) ?? [];
    list.push({ publisher, url });
    of.set(key, list);
  }

  const slices: OwnershipSlice[] = [];
  let known = 0;
  for (const category of OWNERSHIP_ORDER) {
    const outlets = of.get(category);
    if (outlets && outlets.length > 0) {
      slices.push({ category, outlets });
      known += outlets.length;
    }
  }
  const unknown = of.get("unknown");
  if (unknown && unknown.length > 0) slices.push({ category: "unknown", outlets: unknown });

  return { slices, totalOutlets: known + (unknown?.length ?? 0), knownCount: known };
}

/** The headline fact — "24% of the sources are Independent news". The share is over ALL
 *  outlets (unknown included), so it always agrees with the bar and the ring; the winner is
 *  the largest KNOWN category (ties -> earliest in OWNERSHIP_ORDER). Null when nothing is
 *  classified — no headline is better than "100% unknown" dressed as a finding. */
export function dominantOwnership(
  groups: OwnershipGroups,
): { category: OwnershipCategory; pct: number } | null {
  let best: OwnershipSlice | null = null;
  for (const s of groups.slices) {
    if (s.category === "unknown") continue;
    if (!best || s.outlets.length > best.outlets.length) best = s;
  }
  if (!best || groups.totalOutlets === 0) return null;
  return {
    category: best.category as OwnershipCategory,
    pct: Math.round((best.outlets.length / groups.totalOutlets) * 100),
  };
}
