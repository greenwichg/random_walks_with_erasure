/**
 * The one country-search matcher, shared by every country picker: by ISO code or localised name,
 * diacritics folded so "tur" finds Türkiye. Verbatim from the web's `lib/country-search.ts`.
 */
export function fold(s: string): string {
  return s.trim().toLowerCase().normalize("NFD").replace(/\p{Diacritic}/gu, "");
}

export function matchesCountry(code: string, name: string, query: string): boolean {
  const q = fold(query);
  if (!q) return true;
  return fold(code).includes(q) || fold(name).includes(q);
}

/** Generic matcher for searchable filter lists (publishers, topics) — the same fold. */
export function matchesOption(label: string, query: string): boolean {
  const q = fold(query);
  return !q || fold(label).includes(q);
}
