/**
 * Placeholder art identity — the deterministic colour and letterform behind every designed
 * no-image state that keys on a PUBLISHER (the article cards' publisher plate; anything after it
 * that wants the same identity).
 *
 * Pure functions, kept out of the components for the same reason as publisher-logo.ts: identity
 * must AGREE across surfaces. The same outlet gets the same hue on Recommendations, Discover,
 * Search and Saved, or the colour stops carrying identity and becomes noise.
 *
 * Two rules the palette encodes:
 *  - COLOUR NEVER FAKES A PHOTO AND NEVER FAKES A POLITICS. Every hue keeps a wide berth
 *    (≥ 20°) from the diverging lean axis — left blue (214) and right red (356) are semantic
 *    tokens on these exact cards, and a wash a reader could parse as "this outlet is blue/red"
 *    would be the colour equivalent of the branding-image deception the slot exists to prevent.
 *  - DERIVED, NEVER RANDOM. The hue is a hash of the publisher name, so it is stable across
 *    renders, sessions and SSR/client boundaries — no flicker, no hydration mismatch.
 */

/**
 * The wheel decorative placeholders draw from. Curated, not generated: eight tones spaced for
 * variety on a card grid, all clear of the lean axis (214 / 356, ±20°) and of nothing else —
 * positive/caution greens and ambers appear only as filled badges with icon + label, which a
 * huge soft wash cannot be mistaken for.
 */
export const PLACEHOLDER_HUES = [20, 45, 95, 165, 190, 250, 285, 320] as const;

/** How far two washes sit apart on one plate — a duotone, not a rainbow. */
const COMPANION_STEP = 40;

/** FNV-1a over UTF-16 code units — tiny, stable, and good enough spread for 8 buckets. */
function fnv1a(seed: string): number {
  let h = 0x811c9dc5;
  for (let i = 0; i < seed.length; i++) {
    h ^= seed.charCodeAt(i);
    h = Math.imul(h, 0x01000193);
  }
  return h >>> 0;
}

/**
 * The stable hue pair for one publisher: `base` tints the plate, `companion` (40° round the
 * wheel) deepens the opposite corner. An empty/whitespace seed still returns a pair — the
 * plate must never be the thing that crashes on a nameless outlet.
 */
export function placeholderHues(seed: string): { base: number; companion: number } {
  // The modulo keeps the index in range; the ?? is for noUncheckedIndexedAccess, not for reality.
  const base = PLACEHOLDER_HUES[fnv1a(seed.trim()) % PLACEHOLDER_HUES.length] ?? PLACEHOLDER_HUES[0];
  return { base, companion: (base + COMPANION_STEP) % 360 };
}

/**
 * Up-to-two-letter monogram for a publisher with no loadable icon ("The Hill" -> "TH").
 * Moved here from the coverage plate so the story chips and the article plate render the same
 * letters for the same outlet.
 */
export function monogram(name: string): string {
  const letters = name
    .split(/\s+/)
    .map((w) => w.charAt(0))
    .filter((ch) => /\p{L}|\p{N}/u.test(ch))
    .slice(0, 2)
    .join("")
    .toUpperCase();
  return letters || "?";
}
