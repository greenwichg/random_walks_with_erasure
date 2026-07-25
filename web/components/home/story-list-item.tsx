"use client";

import Link from "next/link";
import { EyeOff } from "lucide-react";
import type { LeanBucket, Story } from "@/types/domain";
import { SpectrumBar } from "@/components/shared/spectrum-bar";
import { ArticleImage } from "@/components/shared/article-image";
import { FreshnessBadge } from "@/components/stories/freshness-badge";
import { LEAN_META } from "@/lib/metrics";
import { useTranslation } from "@/lib/i18n";
import { cn } from "@/lib/utils";

/** Bands worth interrupting the dateline for. Anything calmer shows a relative time instead, so the
 *  badge keeps its meaning — a page where every row is flagged flags nothing. */
const URGENT_BANDS = new Set(["Breaking", "Developing"]);

const BUCKETS: LeanBucket[] = ["left", "center", "right"];

/**
 * One story as a compact editorial summary — the workhorse of the dense sections (Top Stories,
 * category modules, the closing Latest run).
 *
 * It is built as four deliberate tiers rather than a flat run of fields, because a list where the
 * topic, the source count and a missing-viewpoint warning all render at the same size is a table,
 * not a page:
 *
 *   1. DATELINE — topic, plus urgency (a freshness badge only when the story is genuinely moving,
 *      otherwise a relative time). Answers "what desk, how fresh".
 *   2. HEADLINE — the largest type in the row and the only element that changes colour on hover,
 *      so the click target is unambiguous.
 *   3. SUMMARY — the story's own one-line synopsis. This is what turns a row into a summary; the
 *      field was already on the contract and simply wasn't being shown.
 *   4. COVERAGE + PROVENANCE — the spectrum bar spanning the full text column (so it reads as
 *      coverage, not as a progress bar) with a LABELLED, localized split beneath it, then who
 *      covered it, then the blind-spot flag as a bordered pill — separated from the counts because
 *      it is an editorial warning, not another statistic.
 *
 * Accessibility: the split is real text ("Left 43%"), never colour alone; the thumbnail is
 * decorative (the headline names the story) and the bar is marked aria-hidden since the same
 * numbers sit beside it.
 */
export function StoryListItem({
  story,
  rank,
  showImage = false,
  showSummary = true,
  className,
}: {
  story: Story;
  /** 1-based position, rendered as a quiet ordinal when provided. */
  rank?: number;
  showImage?: boolean;
  /** Opt out of the synopsis for a deliberately terse list. */
  showSummary?: boolean;
  className?: string;
}) {
  const { t, formatCompact, timeAgo } = useTranslation();

  const d = story.distribution;
  const total = (d?.left ?? 0) + (d?.center ?? 0) + (d?.right ?? 0);
  const publisherCount = story.publisherCount ?? story.publishers?.length ?? null;
  const urgent = story.freshness && URGENT_BANDS.has(story.freshness.band);

  return (
    <li className={cn("group", className)}>
      <Link
        href={`/stories/${story.id}`}
        // Negative inline margin lets the hover tint bleed past the text column without shifting
        // the list's optical alignment.
        className="-mx-2 flex gap-4 rounded-md px-2 py-4 transition-colors hover:bg-accent/40 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring focus-visible:ring-offset-2"
      >
        {rank != null && (
          <span
            aria-hidden
            className="w-4 shrink-0 pt-px text-xs font-semibold tabular-nums text-muted-foreground/70"
          >
            {rank}
          </span>
        )}

        <div className="min-w-0 flex-1">
          {/* 1 — Dateline */}
          <div className="mb-1 flex flex-wrap items-center gap-2">
            <span className="text-[0.7rem] font-semibold uppercase tracking-wide text-foreground/70">
              {story.topic}
            </span>
            {urgent ? (
              <FreshnessBadge band={story.freshness!.band} score={story.freshness!.score} />
            ) : (
              story.updatedAt && (
                <span className="text-[0.7rem] text-muted-foreground">{timeAgo(story.updatedAt)}</span>
              )
            )}
          </div>

          {/* 2 — Headline */}
          <h3 className="line-clamp-2 text-[0.9375rem] font-semibold leading-snug tracking-tight transition-colors group-hover:text-primary sm:text-base">
            {story.title}
          </h3>

          {/* 3 — Synopsis */}
          {showSummary && story.summary && (
            <p className="mt-1 line-clamp-2 text-[0.8125rem] leading-relaxed text-muted-foreground">
              {story.summary}
            </p>
          )}

          {/* 4 — Coverage, then provenance */}
          {total > 0 && (
            <div className="mt-2.5" aria-hidden>
              <SpectrumBar distribution={story.distribution} height={5} showLegend={false} />
            </div>
          )}

          <div className="mt-2 flex flex-wrap items-center gap-x-3 gap-y-1.5 text-xs text-muted-foreground">
            {total > 0 &&
              BUCKETS.map((bucket) => (
                <span key={bucket} className="inline-flex items-center gap-1">
                  <span
                    aria-hidden
                    className="h-1.5 w-1.5 rounded-full"
                    style={{ background: LEAN_META[bucket].color }}
                  />
                  {t(`filter.${bucket}`)}
                  <span className="font-medium tabular-nums text-foreground/80">
                    {Math.round(((d?.[bucket] ?? 0) / total) * 100)}%
                  </span>
                </span>
              ))}

            {total > 0 && <span aria-hidden className="h-3 w-px bg-border" />}

            <span>{t("storyCard.sources", { n: formatCompact(story.totalCoverage) })}</span>
            {publisherCount != null && (
              <span>{t("stories.publishers", { n: formatCompact(publisherCount) })}</span>
            )}

            {story.blindspotSide && (
              <span
                className="inline-flex items-center gap-1 rounded-full border px-2 py-0.5 text-[0.68rem] font-medium"
                style={{
                  color: LEAN_META[story.blindspotSide].color,
                  borderColor: LEAN_META[story.blindspotSide].color,
                }}
              >
                <EyeOff className="h-3 w-3" aria-hidden />
                {t("storyCard.thinOn", { side: t(`filter.${story.blindspotSide}`).toLowerCase() })}
              </span>
            )}
          </div>
        </div>

        {showImage && story.image && (
          // Fixed width + ratio so thumbnails form a clean right-hand column across rows.
          <ArticleImage
            src={story.image}
            alt=""
            aspect="aspect-[16/10]"
            className="w-24 shrink-0 rounded-md sm:w-32"
          />
        )}
      </Link>
    </li>
  );
}
