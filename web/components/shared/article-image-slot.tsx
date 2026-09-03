"use client";

import type { Article } from "@ih/core/domain/types";
import { CardImage } from "@/components/shared/card-image";

/**
 * The article card's image slot — a thin adapter over {@link CardImage}, so an article's three
 * "no usable art" states (absent, engine-flagged branding via `imageSuspect`, failed to load in
 * THIS browser) all land on the one shared newspaper fallback that story cards use.
 *
 * It used to render a PUBLISHER PLATE here instead: a duotone wash in the outlet's hashed hue with
 * its mark on a chip. That was a per-publisher answer to a per-card question, and it made an
 * article card and a story card with the same problem look like two different products. The plate
 * is retired; the fallback is now one image for every card in the app, which is what was asked
 * for. The outlet is still named — the metadata row beside every card carries it, which is why
 * the plate's identity work is not missed here.
 *
 * Kept as its own component (rather than calling CardImage directly from the two cards) because it
 * owns the mapping from an ARTICLE to the slot's inputs — notably `imageSuspect`, which stories do
 * not have. Shared by DiscoverCard (Discover / Search / Saved) and RecommendationCard, pinned as a
 * single implementation by lib/discover-layout.test.ts.
 */
export function ArticleImageSlot({
  article,
  priority = false,
  className = "mb-3",
}: {
  article: Pick<Article, "image" | "imageSuspect" | "headline">;
  priority?: boolean;
  className?: string;
}) {
  return (
    <CardImage
      src={article.image}
      alt={article.headline}
      suspect={article.imageSuspect}
      priority={priority}
      className={className}
    />
  );
}
