"use client";

import * as React from "react";
import { motion } from "framer-motion";
import type { Article } from "@ih/core/domain/types";
import { PublisherBadge, LeanBadge } from "@/components/shared/article-badges";
import { ArticleImage } from "@/components/shared/article-image";
import { ContinuationStrip } from "@/components/shared/continuation-strip";
import { PublisherLogo } from "@/components/shared/publisher-logo";
import { ReadArticleButton } from "@/components/shared/read-article-button";
import { SaveButton } from "@/components/shared/save-button";
import { useTranslation } from "@/lib/i18n";

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
  // Three ways an image doesn't lead this card, one outcome: absent, engine-flagged branding
  // (`imageSuspect` — furniture never masquerades as article art, the story-hero rule applied to
  // article surfaces), or failed to load in THIS browser (only observable here; the engine never
  // downloads images). All three render the publisher PLACEHOLDER below — visibly structure on a
  // muted ground, so the slot stays occupied and every card shares one rhythm, while the
  // anti-deception rule holds: a suspect image is still never shown as art.
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
      {hasImage ? (
        <ArticleImage
          src={article.image}
          alt={article.headline}
          priority={priority}
          className="mb-3"
          onHidden={() => setImgFailed(true)}
        />
      ) : (
        /* The publisher placeholder (2026-08-23): the same slot ArticleImage draws — aspect,
           rounding, muted ground — holding the outlet's dimmed mark, glyph when the chain runs
           out. Decorative (aria-hidden): the metadata row below already NAMES the publisher.
           Replaces the retired senior-type compensation: with the slot always occupied, uniform
           rhythm comes from structure, not from inflating the text. */
        <div
          className="mb-3 flex aspect-[16/9] items-center justify-center rounded-lg bg-muted"
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
      )}

      {/* Content flow (2026-08-23): image slot → headline → metadata → summary → actions. The
          headline leads the text block so the card reads like a front page. One type scale for
          every card: the slot above is always occupied, so no card needs compensating
          typography. */}
      <h3 className="text-[1.05rem] font-semibold leading-snug tracking-tight">
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
        <p className="mt-2 line-clamp-3 text-sm text-muted-foreground">{article.description}</p>
      )}

      {/* The one structural slack point: grid rows stretch every card in a row to the tallest,
          and with the image slot always occupied that difference is a few text lines at most —
          it vanishes here, between the summary and the badges, so footers and action rows sit
          flush across the row instead of ragged. */}
      <div className="flex-1" />

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
