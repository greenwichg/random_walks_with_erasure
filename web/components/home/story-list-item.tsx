"use client";

import Link from "next/link";
import { EyeOff } from "lucide-react";
import type { LeanBucket, Story } from "@ih/core/domain/types";
import { SpectrumBar } from "@/components/shared/spectrum-bar";
import { ArticleImage } from "@/components/shared/article-image";
import { FreshnessBadge } from "@/components/stories/freshness-badge";
import { LEAN_META } from "@ih/core/logic/metrics";
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
  variant = "summary",
  showTopic = true,
  showSplit,
  className,
}: {
  story: Story;
  /** 1-based position, rendered as a quiet ordinal when provided. */
  rank?: number;
  showImage?: boolean;
  /**
   * Density variant (the component hierarchy's two lowest weights):
   *  - "summary" — the full editorial summary: synopsis + the labelled L/C/R split. For a
   *    section's primary list (Top Stories).
   *  - "compact" — dateline, headline, bar and counts; no synopsis, and by default no labelled
   *    split. For supporting lists (category modules, related stories).
   */
  variant?: "summary" | "compact";
  /** Hide the topic label — set false inside a single-topic section, where the section header
   *  already names it and repeating it on every row is pure noise. */
  showTopic?: boolean;
  /** Labelled L/C/R split + publisher count. Defaults by variant (summary yes, compact no); the
   *  home Latest run opts IN on compact rows so it matches Top Stories' information density —
   *  same story payload, same numbers, just rendered. */
  showSplit?: boolean;
  className?: string;
}) {
  const { t, formatCompact, timeAgo } = useTranslation();
  const compact = variant === "compact";
  const split = showSplit ?? !compact;

  const d = story.distribution;
  const total = (d?.left ?? 0) + (d?.center ?? 0) + (d?.right ?? 0);
  const publisherCount = story.publisherCount ?? story.publishers?.length ?? null;
  const urgent = story.freshness && URGENT_BANDS.has(story.freshness.band);
  // `showTopic` is a LAYOUT preference; `story.topic` is whether there is a topic to show. Both
  // have to hold, and folding them here keeps the dateline row from rendering empty.
  const topic = showTopic ? story.topic : "";

  return (
    <li className={cn("group", className)}>
      <Link
        href={`/stories/${story.id}`}
        // Negative inline margin lets the hover tint bleed past the text column without shifting
        // the list's optical alignment.
        className={cn(
          "-mx-2 flex gap-4 rounded-md px-2 transition-colors hover:bg-accent/40 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring focus-visible:ring-offset-2",
          compact ? "py-3" : "py-4",
        )}
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
          {/* 1 — Dateline (hidden entirely when it has nothing to say) */}
          {(topic || urgent || story.updatedAt) && (
            <div className="mb-1 flex flex-wrap items-center gap-2">
              {topic && (
                <span className="text-[0.7rem] font-semibold uppercase tracking-wide text-foreground/70">
                  {topic}
                </span>
              )}
              {urgent ? (
                <FreshnessBadge band={story.freshness!.band} score={story.freshness!.score} />
              ) : (
                story.updatedAt && (
                  <span className="text-[0.7rem] text-muted-foreground">{timeAgo(story.updatedAt)}</span>
                )
              )}
            </div>
          )}

          {/* 2 — Headline */}
          <h3
            className={cn(
              "line-clamp-2 font-semibold leading-snug tracking-tight transition-colors group-hover:text-primary",
              compact ? "text-sm" : "text-[0.9375rem] sm:text-base",
            )}
          >
            {story.title}
          </h3>

          {/* 3 — Synopsis (summary variant only) */}
          {!compact && story.summary && (
            <p className="mt-1 line-clamp-2 text-[0.8125rem] leading-relaxed text-muted-foreground">
              {story.summary}
            </p>
          )}

          {/* 4 — Coverage, then provenance. The bar renders in both variants (it is the product's
              signature); the LABELLED split + publisher count render when `split` is on — always
              in the summary variant, and on compact rows that opt in (the Latest run). */}
          {total > 0 && (
            <div className={compact ? "mt-2" : "mt-2.5"} aria-hidden>
              <SpectrumBar distribution={story.distribution} height={compact ? 4 : 5} showLegend={false} />
            </div>
          )}

          <div className="mt-2 flex flex-wrap items-center gap-x-3 gap-y-1.5 text-xs text-muted-foreground">
            {split &&
              total > 0 &&
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

            {split && total > 0 && <span aria-hidden className="h-3 w-px bg-border" />}

            <span>{t("storyCard.sources", { n: formatCompact(story.totalCoverage) })}</span>
            {split && publisherCount != null && (
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
