"use client";

import * as React from "react";
import type { Article } from "@/types/domain";
import { PublisherBadge, LeanBadge } from "@/components/shared/article-badges";
import { ArticleImage } from "@/components/shared/article-image";
import { ContinuationStrip } from "@/components/shared/continuation-strip";
import { useReadArticleAction } from "@/components/shared/read-article-button";
import { SaveButton } from "@/components/shared/save-button";
import { useTranslation } from "@/lib/i18n";
import { cn } from "@/lib/utils";

/**
 * Discover's RIVER row — the dense scan unit below the front-page tier (Direction 1). One
 * article per row at 3–4x the old card density: metadata line, headline, lean pill, right-hand
 * thumbnail (the home-list pattern), quiet Save. The row is the Read affordance — same
 * `useReadArticleAction` flow as everywhere else; the headline stays a real `<a>` for native
 * navigation semantics.
 *
 * Deliberately static: no framer wrapper (R3 — entrance animation below the fold is main-thread
 * cost with no visible effect; the lead tier above carries the motion). Images follow the
 * article-surface rule: absent, engine-flagged, or failed all simply drop the thumbnail and the
 * text takes the full width.
 */
export function DiscoverRow({ article }: { article: Article }) {
  const { t, timeAgo } = useTranslation();
  const { opened, href, open, record } = useReadArticleAction(article, "discover");
  const [imgFailed, setImgFailed] = React.useState(false);
  React.useEffect(() => setImgFailed(false), [article.image]);
  const showImage = Boolean(article.image) && !article.imageSuspect && !imgFailed;

  return (
    <article
      onClick={href ? open : undefined}
      title={href ? t("read.openTitle") : undefined}
      className={cn(
        "group flex items-start gap-3 rounded-lg border bg-card p-3 transition-colors",
        href && "cursor-pointer hover:bg-accent/30",
      )}
    >
      <div className="min-w-0 flex-1">
        <div className="flex flex-wrap items-center gap-x-2 gap-y-1 text-xs text-muted-foreground">
          <PublisherBadge
            name={article.publisher}
            logo={article.publisherLogo}
            logoFallbacks={article.publisherLogoFallbacks}
          />
          {article.topic && (
            <>
              <span>·</span>
              <span>{article.topic}</span>
            </>
          )}
          {timeAgo(article.publishedAt) && (
            <>
              <span>·</span>
              <span>{timeAgo(article.publishedAt)}</span>
            </>
          )}
        </div>
        <h4
          className={cn(
            "mt-1 line-clamp-2 font-medium leading-snug",
            opened && "text-muted-foreground",
          )}
        >
          {href ? (
            <a
              href={href}
              target="_blank"
              rel="noopener noreferrer"
              className="transition-colors group-hover:text-primary"
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
        </h4>
        <div className="mt-1.5 flex items-center gap-1.5">
          <LeanBadge lean={article.lean} bucket={article.leanBucket} />
        </div>
        {article.url ? <ContinuationStrip anchorUrl={article.url} /> : null}
      </div>

      <div className="flex shrink-0 flex-col items-end gap-1.5">
        <SaveButton article={article} compact />
        {showImage && (
          <ArticleImage
            src={article.image}
            alt=""
            aspect="aspect-[16/10]"
            className="w-24 rounded-md sm:w-28"
            onHidden={() => setImgFailed(true)}
          />
        )}
      </div>
    </article>
  );
}
