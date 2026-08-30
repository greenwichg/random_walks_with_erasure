/**
 * Attached-coverage separation — the client half of M4's containment rule.
 *
 * The engine guarantees a story's member-derived facts (distribution, blindspot, publisher count)
 * are computed BEFORE Tier B attachment, and appends attached rows after the members with
 * `tierB: true`. But the web page also re-derives facts from `story.coverage` locally — publisher
 * counts, the register split, framing comparison — and every one of those must see members only,
 * or the containment the engine proved is quietly undone one `.map()` at a time.
 *
 * One rule, one place: split first, then derive. A component that takes `coverage` decides which
 * half it is about; nothing downstream re-tests `tierB` ad hoc.
 */
import type { StoryCoverage } from "../domain/types";

export interface SplitCoverage {
  /** Member rows — the story's panel: rated-or-not, these VOTED, and every derived stat is theirs. */
  panel: StoryCoverage[];
  /** Attached Tier B rows — coverage that never voted. Render them labeled; count them separately. */
  attached: StoryCoverage[];
}

/** Order-preserving within each half; the engine's member-prefix ordering survives the split. */
export function splitCoverage(coverage: readonly StoryCoverage[] | undefined | null): SplitCoverage {
  const panel: StoryCoverage[] = [];
  const attached: StoryCoverage[] = [];
  for (const row of coverage ?? []) (row.tierB ? attached : panel).push(row);
  return { panel, attached };
}
