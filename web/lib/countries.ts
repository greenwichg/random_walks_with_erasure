/**
 * Country / language display (Location Intelligence UX) — turn the canonical ISO codes the
 * platform stores into what a reader recognises: a localized display name and a flag.
 *
 * The API contract stays ISO (codes filter, route, and store); DISPLAY resolves at render time
 * via `Intl.DisplayNames` in the active interface language — so future localization needs no API
 * change, no shipped name tables, and no new keys per country. Fail-honest: when the runtime
 * can't name a code, the code itself renders (never a guessed name).
 *
 * Pure and DOM-free (runs under `node --test`); the active language is passed in by callers
 * (components read it from `activeLang()`).
 */

/** A conservative short-name overlay for genuinely long names on narrow screens. Only entries
 *  whose short form is universally recognisable belong here — everything else keeps its full
 *  name rather than degrading to an ISO code. */
const SHORT_NAMES: Record<string, string> = {
  US: "USA",
  GB: "UK",
  AE: "UAE",
};

const displayNamesCache = new Map<string, Intl.DisplayNames | null>();

function displayNames(lang: string, type: "region" | "language"): Intl.DisplayNames | null {
  const key = `${lang}:${type}`;
  if (!displayNamesCache.has(key)) {
    try {
      displayNamesCache.set(key, new Intl.DisplayNames([lang], { type }));
    } catch {
      displayNamesCache.set(key, null); // unsupported runtime/locale → callers fall back to the code
    }
  }
  return displayNamesCache.get(key) ?? null;
}

/** Whether `code` is a plausible ISO 3166-1 alpha-2 region code. */
function isRegionCode(code: string): boolean {
  return /^[A-Za-z]{2}$/.test(code);
}

/** The flag emoji for an ISO 3166-1 alpha-2 code ("US" → 🇺🇸), or "" when the code isn't one.
 *  Pure computation (regional-indicator symbols) — no lookup table to drift.
 *
 *  NOT for UI: Windows draws no flag glyphs, so this renders as the bare letters "US" there.
 *  Use {@link countryFlagSrc} with an `<img>` (or `CountryBadge`, which does). This remains for
 *  text-only contexts — a plain-text export, a string label — where an image is impossible. */
export function countryFlag(code: string): string {
  if (!isRegionCode(code)) return "";
  const upper = code.toUpperCase();
  return String.fromCodePoint(
    0x1f1e6 + (upper.charCodeAt(0) - 65),
    0x1f1e6 + (upper.charCodeAt(1) - 65),
  );
}

/**
 * The URL of a country's flag artwork, or "" when the code isn't a region.
 *
 * These are FILES, not emoji, and the difference is the whole point: a flag emoji is a pair of
 * Unicode regional-indicator letters that the platform is expected to draw a flag for, and
 * **Windows ships no flag glyphs**, so every Windows browser renders the two letters instead —
 * "US United States" where macOS and Android show "🇺🇸 United States". `countryFlag` below is kept
 * for text-only contexts (a chart label is a string and cannot hold an image), where the letters
 * are a graceful degradation rather than a broken image.
 *
 * Copied into `public/flags/` at build time by scripts/build-flags.mjs.
 */
export function countryFlagSrc(code: string): string {
  return isRegionCode(code) ? `/flags/${code.toLowerCase()}.svg` : "";
}


/** The localized country name for an ISO code ("US", "en" → "United States"); the code itself
 *  when the runtime can't resolve one. */
export function countryName(code: string, lang = "en"): string {
  if (!isRegionCode(code)) return code;
  const names = displayNames(lang, "region");
  const resolved = names?.of(code.toUpperCase());
  return resolved && resolved !== code.toUpperCase() ? resolved : code.toUpperCase();
}

/** The narrow-screen name: the recognisable short form where one exists, else the full name —
 *  never the bare ISO code while a name is resolvable. */
export function countryShortName(code: string, lang = "en"): string {
  return SHORT_NAMES[code.toUpperCase()] ?? countryName(code, lang);
}

/**
 * ISO codes ordered by the DISPLAY NAME a reader is actually reading.
 *
 * A list you scan for a known country has to be alphabetical; ordered by story count, finding
 * "Japan" means reading the whole thing. `localeCompare` with the active language so accented
 * names sort where that language expects them (Å after A in English, its own letter in Swedish)
 * rather than by code point, and the code breaks ties so the order is total and stable.
 *
 * "All" / "Global" is never in this list — it is a reset row the picker renders above the options,
 * so it cannot be sorted out of first place.
 */
export function sortByCountryName(codes: readonly string[], lang = "en"): string[] {
  return [...codes]
    .map((code) => ({ code, name: countryName(code, lang) }))
    .sort((a, b) => a.name.localeCompare(b.name, lang) || a.code.localeCompare(b.code))
    .map(({ code }) => code);
}


/** The localized language name for an ISO 639-1 code ("en" → "English"); the code as fallback. */
export function languageName(code: string, lang = "en"): string {
  if (!/^[A-Za-z]{2,3}$/.test(code)) return code;
  const names = displayNames(lang, "language");
  const resolved = names?.of(code.toLowerCase());
  return resolved && resolved !== code.toLowerCase() ? resolved : code;
}
