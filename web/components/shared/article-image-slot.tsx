"use client";

import * as React from "react";
import type { Article } from "@ih/core/domain/types";
import { ArticleImage } from "@/components/shared/article-image";
import { PublisherLogo } from "@/components/shared/publisher-logo";
import { cn } from "@/lib/utils";

/**
 * The always-occupied article image slot — THE one implementation of "what fronts a card when
 * the art can't". Three ways an image doesn't lead a card, one outcome: absent, engine-flagged
 * branding (`imageSuspect` — furniture never masquerades as article art, the story-hero rule
 * applied to article surfaces), or failed to load in THIS browser (only observable here; the
 * engine never downloads images). All three render the publisher placeholder: the same slot
 * geometry ArticleImage draws — aspect, rounding, muted ground — holding the outlet's dimmed
 * mark via the PublisherLogo fallback chain, glyph when it runs out. Decorative (aria-hidden):
 * the metadata row beside every card already NAMES the publisher. Keeping the slot occupied is
 * what holds one rhythm across card streams, and the anti-deception rule holds: a suspect image
 * is still never shown AS art.
 *
 * Shared by DiscoverCard (Discover / Search / Saved) and RecommendationCard — pinned as a single
 * implementation by lib/discover-layout.test.ts.
 */
export function ArticleImageSlot({
  article,
  priority = false,
  className = "mb-3",
}: {
  article: Pick<
    Article,
    "image" | "imageSuspect" | "headline" | "publisherLogo" | "publisherLogoFallbacks"
  >;
  priority?: boolean;
  className?: string;
}) {
  const [imgFailed, setImgFailed] = React.useState(false);
  React.useEffect(() => setImgFailed(false), [article.image]);
  const hasImage = Boolean(article.image) && !article.imageSuspect && !imgFailed;
  if (hasImage) {
    return (
      <ArticleImage
        src={article.image}
        alt={article.headline}
        priority={priority}
        className={className}
        onHidden={() => setImgFailed(true)}
      />
    );
  }
  return (
    <div
      className={cn("flex aspect-[16/9] items-center justify-center rounded-lg bg-muted", className)}
      aria-hidden="true"
    >
      <div className="opacity-35 grayscale">
        <PublisherLogo
          logo={article.publisherLogo}
          fallbacks={article.publisherLogoFallbacks}
          sizePx={40}
          className="h-10 w-10 object-contain"
          glyphClassName="h-9 w-9 text-muted-foreground"
        />
      </div>
    </div>
  );
}
