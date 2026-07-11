"use client";

import { motion } from "framer-motion";
import type { Article } from "@/types/domain";
import { PublisherBadge, LeanBadge } from "@/components/shared/article-badges";
import { ArticleImage } from "@/components/shared/article-image";
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
}: {
  article: Article;
  index?: number;
  openedFrom?: string;
}) {
  const { timeAgo } = useTranslation();
  return (
    <motion.article
      layout
      initial={{ opacity: 0, y: 10 }}
      animate={{ opacity: 1, y: 0 }}
      transition={{ delay: Math.min(index * 0.04, 0.4), duration: 0.4, ease: [0.16, 1, 0.3, 1] }}
      className="flex flex-col rounded-lg border bg-card p-5 shadow-soft transition-shadow hover:shadow-card"
    >
      <ArticleImage src={article.image} alt={article.headline} className="mb-3" />

      <div className="flex flex-wrap items-center gap-x-3 gap-y-1">
        <PublisherBadge name={article.publisher} lean={article.publisherLean} logo={article.publisherLogo} />
        {article.topic && (
          <>
            <span className="text-xs text-muted-foreground">·</span>
            <span className="text-xs font-medium text-muted-foreground">{article.topic}</span>
          </>
        )}
        <span className="text-xs text-muted-foreground">·</span>
        <span className="text-xs text-muted-foreground">{timeAgo(article.publishedAt)}</span>
      </div>

      <h3 className="mt-2 text-[1.05rem] font-semibold leading-snug tracking-tight">
        {article.headline}
      </h3>
      {article.description && (
        <p className="mt-1.5 line-clamp-3 flex-1 text-sm text-muted-foreground">{article.description}</p>
      )}

      <div className="mt-3 flex flex-wrap items-center gap-1.5">
        <LeanBadge lean={article.lean} bucket={article.leanBucket} />
      </div>

      <div className="mt-4 flex items-center gap-2">
        <ReadArticleButton article={article} openedFrom={openedFrom} />
        <SaveButton article={article} />
      </div>
    </motion.article>
  );
}
