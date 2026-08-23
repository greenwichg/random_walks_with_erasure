/**
 * Estimated DiscoverCard height, in px at a typical desktop column (~350px wide) — the input
 * height-aware masonry placement needs (lib/masonry-order.ts, distributeByHeight).
 *
 * The estimate mirrors the card's own rendering rules (components/discover/discover-card.tsx):
 * an image card leads with an aspect-video image and clamps the summary at 3 lines; an imageless
 * card gets senior type — larger headline, summary clamped at 6 lines — and an engine-flagged
 * branding image (imageSuspect) renders text-first, so it estimates as imageless too.
 *
 * Constants are deliberately coarse: placement needs the ORDER of heights and a fair ratio
 * between an image card and a text card, not pixel truth. The residual error is exactly what the
 * placement's one-item skew bound absorbs. Client-only image load failures cannot be estimated
 * (they happen after placement) and are rare enough to live inside the same bound.
 */
export function estimateDiscoverCardHeight(article: {
  image?: string | null;
  imageSuspect?: boolean;
  headline: string;
  description?: string | null;
}): number {
  const hasImage = Boolean(article.image) && !article.imageSuspect;
  const chrome = 150; // card padding, metadata row, lean badge, actions row
  const image = hasImage ? 209 : 0; // aspect-video at ~350px column + its bottom margin
  const headlineLines = Math.min(4, Math.max(1, Math.ceil(article.headline.length / (hasImage ? 34 : 30))));
  const headline = headlineLines * (hasImage ? 24 : 27);
  const descLen = (article.description ?? "").trim().length;
  const descLines = descLen ? Math.min(hasImage ? 3 : 6, Math.ceil(descLen / 48)) : 0;
  const description = descLines * 20 + (descLines ? 8 : 0);
  return chrome + image + headline + description;
}
