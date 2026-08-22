/**
 * Where the selected country's coverage ends in a recommendation feed.
 *
 * The engine partitions country-matched items PER STRATEGY: the blend allocates slots to each
 * strategy (Bridging, then Discovery, then For You) and orders that strategy's own budget
 * country-first. The served list is therefore matched-first WITHIN each group and never globally,
 * so a boundary drawn over the raw order lands inside the FIRST group and strands every later
 * group's country cards below it — a reader who picked China saw three China cards, a "China
 * coverage ends here" divider, and then eight more China cards.
 *
 * Pure and DOM-free so the ordering is testable under `node --test` rather than by eye.
 */

/** The shape this needs from a recommendation: absent `countryMatch` means no country was
 *  selected, and nothing is claimed either way. */
export interface CountryMatchable {
  countryMatch?: boolean;
}

export interface CountryPartition<T> {
  /** Country-matched items first, then backfill; each part keeps its original blend order. */
  ordered: T[];
  /** Index in `ordered` of the first backfill item, or -1 when there is no boundary to draw —
   *  either no country is selected, or the country filled every slot. */
  firstBackfill: number;
}

export function partitionByCountryMatch<T extends CountryMatchable>(items: T[]): CountryPartition<T> {
  const hasCountry = items.some((r) => r.countryMatch !== undefined);
  if (!hasCountry) return { ordered: items, firstBackfill: -1 };
  const ordered = [
    ...items.filter((r) => r.countryMatch === true),
    ...items.filter((r) => r.countryMatch !== true),
  ];
  return { ordered, firstBackfill: ordered.findIndex((r) => r.countryMatch === false) };
}
