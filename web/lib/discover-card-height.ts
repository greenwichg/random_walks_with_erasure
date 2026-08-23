/**
 * Estimated DiscoverCard height, in px at a typical desktop column (~350px wide) — the input
 * height-aware masonry placement needs (lib/masonry-order.ts, distributeByHeight).
 *
 * The estimate mirrors the card's own rendering rules (components/discover/discover-card.tsx):
 * every card leads with an OCCUPIED image slot — article art, or the publisher placeholder when
 * the art is absent, engine-flagged branding, or broken — one type scale, and a 3-line summary
 * clamp. Height therefore varies only by text lines, so image fields are deliberately not
 * consulted: art and placeholder fill the same slot.
 *
 * Constants are deliberately coarse: placement needs the ORDER of heights and fair ratios, not
 * pixel truth. The residual error is exactly what the placement's one-item skew bound absorbs.
 */
export function estimateDiscoverCardHeight(article: {
  headline: string;
  description?: string | null;
}): number {
  const chrome = 150; // card padding, metadata row, lean badge, actions row
  const slot = 209; // the always-occupied 16:9 slot at ~350px column + its bottom margin
  const headlineLines = Math.min(4, Math.max(1, Math.ceil(article.headline.length / 34)));
  const descLen = (article.description ?? "").trim().length;
  const descLines = descLen ? Math.min(3, Math.ceil(descLen / 48)) : 0;
  return chrome + slot + headlineLines * 24 + descLines * 20 + (descLines ? 8 : 0);
}
