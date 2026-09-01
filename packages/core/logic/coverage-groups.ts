import type { StoryCoverage } from "../domain/types.ts";

/**
 * Consecutive-run grouping for the story coverage list — the house adaptation of Ground News's
 * "reposted by N other sources" row. Literal reposts are already one row here (ingest dedupes by
 * canonical URL), so the repetition that actually clutters this list is an outlet filing several
 * updates in a row — a liveblog cadence. Those CONSECUTIVE same-publisher runs collapse to their
 * lead row plus an expandable remainder.
 *
 * Deliberately consecutive-only: pulling an outlet's scattered rows together would break the
 * chronological order the sort promises. A publisher that reappears later in the timeline starts
 * a new group — that reappearance is information (they came back to the story), not repetition.
 */

export interface CoverageGroup {
  lead: StoryCoverage;
  /** The rest of the run, in the incoming order. Empty for a publisher that appears once. */
  rest: StoryCoverage[];
}

export function collapseConsecutive(rows: StoryCoverage[]): CoverageGroup[] {
  const groups: CoverageGroup[] = [];
  for (const row of rows) {
    const last = groups[groups.length - 1];
    if (last && last.lead.publisher === row.publisher) last.rest.push(row);
    else groups.push({ lead: row, rest: [] });
  }
  return groups;
}
