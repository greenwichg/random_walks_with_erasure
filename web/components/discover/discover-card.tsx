"use client";

import * as React from "react";
import { motion } from "framer-motion";
import type { Article } from "@ih/core/domain/types";
import { PublisherBadge, LeanBadge } from "@/components/shared/article-badges";
import { ArticleImage } from "@/components/shared/article-image";
import { ContinuationStrip } from "@/components/shared/continuation-strip";
import { ReadArticleButton } from "@/components/shared/read-article-button";
import { SaveButton } from "@/components/shared/save-button";
import { useTranslation } from "@/lib/i18n";
import { cn } from "@/lib/utils";

/**
 * A Discover article card — one live FeedArticle: publisher, title, description, real publication
 * time, outlet lean, category, and the shared Read control (opens the canonical publisher URL).
 * Reuses the article badges and the same Read button as Recommendations; adds no new article store.
 */
export function DiscoverCard({
  article,
  index = 0,
  openedFrom = "discover",
  priority = false,
  leanDot = true,
}: {
  article: Article;
  index?: number;
  openedFrom?: string;
  priority?: boolean;
  /** Whether PublisherBadge shows the house-lean dot. Discover passes false — on this pipeline
   *  the article's lean IS the outlet's lean, and dot + pill stated one fact twice (the
   *  lean-said-once fix, kept through the 2026-08-16 layout revert). Search keeps the default. */
  leanDot?: boolean;
}) {
  const { timeAgo } = useTranslation();
  // Three ways an image doesn't lead this card, one text-first outcome: absent, engine-flagged
  // branding (`imageSuspect` — furniture never fronts a card, the story-hero rule applied to
  // article surfaces), or failed to load in THIS browser (only observable here; the engine never
  // downloads images). The senior-type treatment below keys on the same state, so a demoted
  // image also hands its space to content.
  const [imgFailed, setImgFailed] = React.useState(false);
  React.useEffect(() => setImgFailed(false), [article.image]);
  const hasImage = Boolean(article.image) && !article.imageSuspect && !imgFailed;
  return (
    <motion.article
      layout
      /* R3: `layout` stays on every card — the FLIP on filter changes is visible behaviour — but the
         ENTRANCE below the first rows plays offscreen and is skipped (`initial={false}` mounts the
         card at its final state with no animation work). Deliberately NO `cv-card` here, measured:
         with it, this page's 4x-CPU long tasks went UP (~840 -> ~1000 ms) — framer's layout
         measurements keep forcing the browser to size the very boxes content-visibility is trying
         to skip, the one combination where the two features fight. */
      initial={index < 8 ? { opacity: 0, y: 10 } : false}
      animate={{ opacity: 1, y: 0 }}
      transition={{ delay: Math.min(index * 0.04, 0.4), duration: 0.4, ease: [0.16, 1, 0.3, 1] }}
      className="flex flex-col rounded-lg border bg-card p-5 shadow-soft transition-shadow hover:shadow-card"
    >
      {hasImage && (
        <ArticleImage
          src={article.image}
          alt={article.headline}
          priority={priority}
          className="mb-3"
          onHidden={() => setImgFailed(true)}
        />
      )}

      {/* Content flow (2026-08-23): image → headline → metadata → summary → actions. The headline
          leads the text block so the card reads like a front page, and there is deliberately no
          internal stretcher — inside natural-height masonry columns the card ends where its
          content ends, so a short card is a short card instead of a void. Imageless: the type
          gets senior (newspaper rule) — a larger headline and a deeper description clamp give
          the text-first card the presence the image would have carried. */}
      <h3
        className={cn(
          "font-semibold leading-snug tracking-tight",
          hasImage ? "text-[1.05rem]" : "text-lg",
        )}
      >
        {article.headline}
      </h3>

      <div className="mt-2 flex flex-wrap items-center gap-x-3 gap-y-1">
        <PublisherBadge name={article.publisher} lean={leanDot ? article.publisherLean : null} logo={article.publisherLogo} logoFallbacks={article.publisherLogoFallbacks} />
        {article.topic && (
          <>
            <span className="text-xs text-muted-foreground">·</span>
            <span className="text-xs font-medium text-muted-foreground">{article.topic}</span>
          </>
        )}
        {timeAgo(article.publishedAt) && (
          <>
            <span className="text-xs text-muted-foreground">·</span>
            <span className="text-xs text-muted-foreground">{timeAgo(article.publishedAt)}</span>
          </>
        )}
      </div>

      {article.description && (
        <p
          className={cn(
            "mt-2 text-sm text-muted-foreground",
            hasImage ? "line-clamp-3" : "line-clamp-6 leading-relaxed",
          )}
        >
          {article.description}
        </p>
      )}

      <div className="mt-3 flex flex-wrap items-center gap-1.5">
        <LeanBadge lean={article.lean} bucket={article.leanBucket} />
      </div>

      <div className="mt-4 flex items-center gap-2">
        <ReadArticleButton article={article} openedFrom={openedFrom} />
        <SaveButton article={article} />
      </div>

      {/* Story Continuation — renders nothing unless the reader opened THIS article, came back
          after a real absence, and the engine found an opposing account (§1.4). */}
      {article.url ? <ContinuationStrip anchorUrl={article.url} /> : null}
    </motion.article>
  );
}
