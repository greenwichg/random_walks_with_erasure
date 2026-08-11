/**
 * Publisher logo selection — which URL to try, and when an image is too small to be worth showing.
 *
 * Pure functions, kept out of the components because two surfaces render publisher logos (the
 * profile header at 36px of content, the inline `PublisherBadge` at 14px) and they must agree.
 * A rule that lives in one component is a rule the other one quietly breaks.
 *
 * The problem this solves: a publisher with no Wikipedia enrichment fell back to `favicon.ico`,
 * a 16-32px browser-chrome icon, rendered into a 36px box that needs 72 real pixels on a 2x
 * display. That is a 4.5x upscale — the blurry mark on every unenriched outlet.
 */

/** Ordered URLs to try for one publisher: the chosen logo, then its alternates. */
export function logoCandidates(primary?: string | null, fallbacks?: string[] | null): string[] {
  const all = [primary, ...(fallbacks ?? [])];
  const seen = new Set<string>();
  return all.filter((u): u is string => {
    if (!u || seen.has(u)) return false;
    seen.add(u);
    return true;
  });
}

/**
 * How many real pixels a box needs, given the device's pixel ratio.
 *
 * Capped at 3: beyond that the extra pixels are invisible at normal viewing distance, and treating
 * a 4x phone as needing 4x assets would reject perfectly good icons on exactly the devices where
 * the difference cannot be seen.
 */
export function requiredPixels(cssPx: number, dpr = 1): number {
  return Math.round(cssPx * Math.min(Math.max(dpr, 1), 3));
}

/** Vector art has no resolution to be too low — `naturalWidth` reports its viewBox, not its quality. */
function isVector(url?: string | null): boolean {
  return /\.svgz?(\?|#|$)/i.test(url ?? "");
}

/**
 * Is this image too low-resolution to render in a box of `cssPx`?
 *
 * The tolerance is deliberate. Demanding an exact match would reject a 64px icon in a 72px box
 * over a difference nobody can see, and most publishers ship power-of-two icons that land just
 * under a box size. A quarter under is invisible; half under is the blur we are removing.
 *
 * `naturalWidth === 0` means the image has not loaded or failed to decode — not a size judgement,
 * so it is not treated as too small and the caller's error path handles it.
 *
 * An SVG is exempt: it scales to any box, but reports its viewBox as `naturalWidth`. A hand-picked
 * `logo.svg` authored at 24x24 is pin-sharp at 72px and would otherwise be demoted to the favicon
 * underneath it — the exact blur this module exists to remove, applied to the best asset we have.
 */
export function isTooLowRes(naturalWidth: number, cssPx: number, dpr = 1, url?: string | null): boolean {
  if (!naturalWidth || isVector(url)) return false;
  return naturalWidth < requiredPixels(cssPx, dpr) * 0.75;
}

/**
 * The next candidate after one fails or proves too small, or null when the list is exhausted.
 *
 * Exhaustion is a real outcome, not a failure to handle: a publisher that exposes no usable icon
 * should get the monogram/glyph rather than a stretched 16px favicon. Showing nothing beats
 * showing something misleadingly bad — the same instinct the null-lean rule follows.
 */
export function nextCandidate(candidates: string[], current: string | null): string | null {
  if (!candidates.length) return null;
  if (current === null) return candidates[0] ?? null;
  const i = candidates.indexOf(current);
  if (i < 0) return null;
  return candidates[i + 1] ?? null;
}
