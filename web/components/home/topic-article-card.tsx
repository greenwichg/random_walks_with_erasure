"use client";

import type { Article } from "@ih/core/domain/types";
import { LeanBadge, PublisherBadge } from "@/components/shared/article-badges";
import { ArticleImageSlot } from "@/components/shared/article-image-slot";
import { ReadArticleButton } from "@/components/shared/read-article-button";
import { SaveButton } from "@/components/shared/save-button";
import { useTranslation } from "@/lib/i18n";

/** Where reads opened from this card are attributed — the home page's topic view. */
export const TOPIC_ARTICLE_OPENED_FROM = "home-topic";

/**
 * One single-outlet article under a thin topic — the home page's lightest card.
 *
 * It is the Discover card's information without its weight: the same image slot (art or the
 * publisher plate, never a blank), headline, outlet, publication time and lean, and the same
 * shared Read and Save controls — but no entrance animation, no continuation strip, and a
 * two-line summary, because eight of these sit in a grid beneath a hero and must read as a
 * supporting tier rather than compete with it. Same `Article` contract, so nothing is invented
 * for it: an unrated outlet renders "Unknown", an absent image renders the plate.
 */
export function TopicArticleCard({ article }: { article: Article }) {
  const { timeAgo } = useTranslation();
  const when = timeAgo(article.publishedAt);

  return (
    <article className="flex h-full flex-col rounded-lg border bg-card p-4 transition-shadow hover:shadow-card">
      <ArticleImageSlot article={article} className="mb-3" />

      <h3 className="line-clamp-2 text-[0.95rem] font-semibold leading-snug tracking-tight">
        {article.headline}
      </h3>

      <div className="mt-2 flex flex-wrap items-center gap-x-2 gap-y-1 text-xs text-muted-foreground">
        {/* The lean is stated once, on the badge below — the outlet dot would say it twice. */}
        <PublisherBadge
          name={article.publisher}
          lean={null}
          logo={article.publisherLogo}
          logoFallbacks={article.publisherLogoFallbacks}
        />
        {when && (
          <>
            <span aria-hidden>·</span>
            <span>{when}</span>
          </>
        )}
      </div>

      {article.description && (
        <p className="mt-2 line-clamp-2 text-[0.8125rem] leading-relaxed text-muted-foreground">
          {article.description}
        </p>
      )}

      <div className="flex-1" />

      <div className="mt-3 flex items-center justify-between gap-2">
        <LeanBadge lean={article.lean} bucket={article.leanBucket} />
        <div className="flex items-center gap-1.5">
          <ReadArticleButton article={article} openedFrom={TOPIC_ARTICLE_OPENED_FROM} variant="soft" />
          <SaveButton article={article} compact />
        </div>
      </div>
    </article>
  );
}
