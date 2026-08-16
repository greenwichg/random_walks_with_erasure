"use client";

import * as React from "react";
import type { Article } from "@/types/domain";
import { PublisherBadge, LeanBadge } from "@/components/shared/article-badges";
import { ArticleImage } from "@/components/shared/article-image";
import { ContinuationStrip } from "@/components/shared/continuation-strip";
import { useReadArticleAction } from "@/components/shared/read-article-button";
import { SaveButton } from "@/components/shared/save-button";
import { leanBucket } from "@/lib/political";
import { useTranslation } from "@/lib/i18n";
import { cn } from "@/lib/utils";

/**
 * Discover's RIVER units — the quiet row, its text-only variant, and the featured BEAT — built to
 * the measured mock spec (docs: the River Rhythm mock, 2026-08-16; quiet row ~106px vs the old
 * 144px, density 6.4 → 8.5 rows per 1000px between beats).
 *
 * One lean statement per row: the 11px mini-pill at the end of the metadata line. Text-only rows
 * additionally carry the SAME fact structurally as a 3px tinted left edge — known lean only; an
 * unknown lean gets no edge and keeps its neutral pill, absence never wears a color. Publisher is
 * the anchored token (foreground weight); Save is a quiet 28px corner icon (hover-revealed on
 * `sm+`, always visible on touch); the thumb top-aligns at 128px (200px on beats, left side).
 *
 * The row is the Read affordance — the same `useReadArticleAction` flow as the shared button; the
 * headline stays a real `<a>` so middle-click/cmd-click/keyboard stay native and record-only.
 * Deliberately static below the fold (R3): no framer wrapper; the front-page tier owns motion.
 */
export function DiscoverRow({ article, beat = false }: { article: Article; beat?: boolean }) {
  const { t, timeAgo } = useTranslation();
  const { opened, href, open, record } = useReadArticleAction(article, "discover");
  const [imgFailed, setImgFailed] = React.useState(false);
  React.useEffect(() => setImgFailed(false), [article.image]);
  const showImage = Boolean(article.image) && !article.imageSuspect && !imgFailed;
  const textOnly = !beat && !showImage;
  const bucket =
    article.leanBucket ?? (article.lean != null ? leanBucket(article.lean) : null);
  // The lean edge: known lean only, text rows only — a counted fact rendered structurally. The
  // 75%-opacity tint over the themed hue keeps it an accent, not a flag.
  const edge =
    textOnly && bucket
      ? { boxShadow: `inset 3px 0 0 hsl(var(--${bucket}) / 0.75)` }
      : undefined;

  const headline = href ? (
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
  );

  return (
    <article
      onClick={href ? open : undefined}
      title={href ? t("read.openTitle") : undefined}
      style={edge}
      data-testid={beat ? "river-beat" : "river-row"}
      data-lean-edge={edge ? bucket : undefined}
      className={cn(
        "group relative flex items-start rounded-lg border bg-card transition-colors",
        beat ? "gap-4 p-4" : "gap-3 p-3",
        textOnly && "pl-3.5",
        href && "cursor-pointer hover:bg-accent/30",
      )}
    >
      {beat && showImage && (
        <ArticleImage
          src={article.image}
          alt=""
          aspect="aspect-[16/10]"
          className="w-28 rounded-md sm:w-[200px]"
          onHidden={() => setImgFailed(true)}
        />
      )}

      <div className={cn("min-w-0 flex-1", (textOnly || beat) && "pr-8")}>
        <div className="flex flex-wrap items-center gap-x-2 gap-y-1 text-xs text-muted-foreground">
          <PublisherBadge
            name={article.publisher}
            logo={article.publisherLogo}
            logoFallbacks={article.publisherLogoFallbacks}
            emphasis
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
          {/* Lean, said once — the mini-pill closing the metadata line (11px per the mock). */}
          <LeanBadge
            lean={article.lean}
            bucket={article.leanBucket}
            className="px-2 py-px text-[11px]"
          />
        </div>

        <h4
          className={cn(
            "mt-1 line-clamp-2 font-medium leading-snug",
            beat && "text-xl font-semibold leading-tight tracking-tight",
            textOnly && "text-[17px] leading-[1.4]",
            opened && "text-muted-foreground",
          )}
        >
          {headline}
        </h4>
        {beat && article.description && (
          <p className="mt-1.5 line-clamp-2 text-sm text-muted-foreground">
            {article.description}
          </p>
        )}
        {article.url ? <ContinuationStrip anchorUrl={article.url} /> : null}
      </div>

      {!beat && showImage && (
        <ArticleImage
          src={article.image}
          alt=""
          aspect="aspect-[16/10]"
          className="w-32 rounded-md"
          onHidden={() => setImgFailed(true)}
        />
      )}

      <SaveButton
        article={article}
        compact
        className="absolute right-2 top-2 h-7 w-7 sm:opacity-0 sm:focus-visible:opacity-100 sm:group-hover:opacity-100"
      />
    </article>
  );
}
