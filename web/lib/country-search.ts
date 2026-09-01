/**
 * The one country-search matcher, shared by every country picker on the settings page.
 *
 * Extracted verbatim from the For You picker's inline search so the three pickers (For You
 * country, Preferred edition, Followed places) cannot drift into three different ideas of what
 * "matches" means. Matching is by ISO code or localized display name.
 */

/** Lower-case and strip diacritics for search matching. Without the fold, typing "tur" misses
 *  Türkiye — ICU's current name — and the same applies to Côte d'Ivoire, São Tomé and Åland.
 *  A reader searching a country list types the letters on their keyboard, not the accents. */
export function fold(s: string): string {
  return s.trim().toLowerCase().normalize("NFD").replace(/\p{Diacritic}/gu, "");
}

/** Whether `query` matches a country given its ISO `code` and localized display `name`.
 *  An empty query matches everything — the unfiltered list is the browse state, not a no-match. */
export function matchesCountry(code: string, name: string, query: string): boolean {
  const q = fold(query);
  if (!q) return true;
  return fold(code).includes(q) || fold(name).includes(q);
}

/** Generic matcher for searchable filter lists (publishers, topics) — the same fold, so every
 *  searchable dropdown agrees with the country pickers about what "matches" means. */
export function matchesOption(label: string, query: string): boolean {
  const q = fold(query);
  return !q || fold(label).includes(q);
}
