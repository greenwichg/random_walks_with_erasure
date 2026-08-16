"use client";

import * as React from "react";
import { motion } from "framer-motion";
import type { Article } from "@/types/domain";
import { PublisherBadge, LeanBadge } from "@/components/shared/article-badges";
import { ArticleImage } from "@/components/shared/article-image";
import { ContinuationStrip } from "@/components/shared/continuation-strip";
import { useReadArticleAction } from "@/components/shared/read-article-button";
import { SaveButton } from "@/components/shared/save-button";
import { useTranslation } from "@/lib/i18n";
import { cn } from "@/lib/utils";

/**
 * Discover's front-page tier: one LEAD card and two SUPPORT cards (Direction 1 — "front page,
 * then river"). The card itself is the Read affordance: clicking anywhere records the read and
 * opens the canonical publisher URL (the same `useReadArticleAction` flow the shared button
 * runs), the headline is a real `<a>` so middle-click/cmd-click and keyboard behave natively,
 * and Save is a quiet icon — the labeled button pair on every card made UI furniture the
 * loudest thing on the page.
 *
 * Lean is said ONCE, as the pill in the metadata row — the house-lean dot is deliberately not
 * passed to PublisherBadge here, because on this pipeline the article's lean IS the outlet's
 * lean and the card was stating one fact twice. Images follow the article-surface rule: absent,
 * engine-flagged (`imageSuspect`), or failed-to-load all land in the same text-first layout.
 */
export function DiscoverLeadCard({
  article,
  size,
  priority = false,
  index = 0,
}: {
  article: Article;
  size: "lead" | "support";
  priority?: boolean;
  index?: number;
}) {
  const { t, timeAgo } = useTranslation();
  const { opened, href, open, record } = useReadArticleAction(article, "discover");
  const [imgFailed, setImgFailed] = React.useState(false);
  React.useEffect(() => setImgFailed(false), [article.image]);
  const showImage = Boolean(article.image) && !article.imageSuspect && !imgFailed;
  const lead = size === "lead";

  return (
    <motion.article
      initial={{ opacity: 0, y: 10 }}
      animate={{ opacity: 1, y: 0 }}
      transition={{ delay: Math.min(index * 0.06, 0.2), duration: 0.4, ease: [0.16, 1, 0.3, 1] }}
      onClick={href ? open : undefined}
      title={href ? t("read.openTitle") : undefined}
      className={cn(
        "group flex h-full flex-col rounded-lg border bg-card p-5 shadow-soft transition-all",
        href && "cursor-pointer hover:-translate-y-0.5 hover:shadow-card",
      )}
    >
      {showImage && (
        <ArticleImage
          src={article.image}
          alt={article.headline}
          priority={priority}
          aspect={lead ? "aspect-[16/9]" : "aspect-[16/8]"}
          className="mb-3"
          onHidden={() => setImgFailed(true)}
        />
      )}

      <div className="flex flex-wrap items-center gap-x-3 gap-y-1">
        {/* No house-lean dot: the pill below already says it (one fact, said once). */}
        <PublisherBadge
          name={article.publisher}
          logo={article.publisherLogo}
          logoFallbacks={article.publisherLogoFallbacks}
        />
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

      <h3
        className={cn(
          "mt-2 font-semibold leading-snug tracking-tight",
          lead ? (showImage ? "text-xl" : "text-2xl") : "text-base",
          opened && "text-muted-foreground",
        )}
      >
        {href ? (
          <a
            href={href}
            target="_blank"
            rel="noopener noreferrer"
            className="transition-colors group-hover:text-primary"
            // A real anchor: the browser owns navigation (middle-click, cmd-click, keyboard all
            // native); we only record, and stop the bubble so the card handler can't open twice.
            onClick={(e) => {
              e.stopPropagation();
              record();
            }}
          >
            {article.headline}
          </a>
        ) : (
          article.headline
        )}
      </h3>
      {article.description && (
        <p
          className={cn(
            "mt-1.5 text-sm text-muted-foreground",
            lead ? (showImage ? "line-clamp-3" : "line-clamp-5 leading-relaxed") : "line-clamp-2",
          )}
        >
          {article.description}
        </p>
      )}

      <div className="flex-1" />

      <div className="mt-3 flex items-center justify-between gap-2">
        <LeanBadge lean={article.lean} bucket={article.leanBucket} />
        <SaveButton article={article} compact />
      </div>

      {article.url ? <ContinuationStrip anchorUrl={article.url} /> : null}
    </motion.article>
  );
}
