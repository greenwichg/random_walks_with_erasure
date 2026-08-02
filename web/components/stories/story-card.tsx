"use client";

import Link from "next/link";
import { motion } from "framer-motion";
import { Newspaper, ArrowRight, EyeOff } from "lucide-react";
import type { Story } from "@/types/domain";
import { useTranslation } from "@/lib/i18n";
import { SpectrumBar } from "@/components/shared/spectrum-bar";
import { ArticleImage } from "@/components/shared/article-image";
import { FreshnessBadge } from "@/components/stories/freshness-badge";
import { LEAN_META } from "@/lib/metrics";
import { cn } from "@/lib/utils";

/** A clustered-story preview card — one event, coverage across the spectrum.
 *
 * Imageless stories render the COVERAGE FIGURE in the image slot: the story's own left/center/
 * right distribution drawn large. Same slot, same aspect, so imaged and imageless cards share
 * one skeleton and grid rows never leave a void — and the visual is a counted fact unique to
 * each story, never a stock illustration or category artwork. (The figure replaces the small
 * spectrum strip; showing both would say the same thing twice.) */
export function StoryCard({ story, index = 0, priority = false }: { story: Story; index?: number; priority?: boolean }) {
  const { t, formatCompact, timeAgo } = useTranslation();
  const hasImage = Boolean(story.image);
  // The entrance animation exists for the cards a reader can SEE arrive. Below the first grid rows
  // it plays offscreen — pure main-thread cost with no visible effect (R3: a framer wrapper per
  // card was a measurable share of the 24-card grid's 1.4 s of 4x-CPU long tasks). Static cards
  // keep the identical DOM inside a plain div; `cv-card` lets the browser skip offscreen paint.
  const Wrapper = index < 8 ? motion.div : "div";
  const entrance =
    index < 8
      ? {
          initial: { opacity: 0, y: 10 },
          animate: { opacity: 1, y: 0 },
          transition: { delay: Math.min(index * 0.05, 0.35), ease: [0.16, 1, 0.3, 1] as const },
        }
      : {};
  return (
    <Wrapper className="cv-card" {...entrance}>
      <Link
        href={`/stories/${story.id}`}
        className="group flex h-full flex-col rounded-lg border bg-card p-5 shadow-soft transition-all hover:-translate-y-0.5 hover:shadow-card"
      >
        {hasImage ? (
          <ArticleImage src={story.image} alt={story.title} priority={priority} className="mb-3" />
        ) : (
          <CoverageFigure story={story} />
        )}

        <div className="mb-2 flex items-center justify-between gap-2">
          <div className="flex min-w-0 items-center gap-1.5">
            {story.topic && (
              <span className="inline-flex items-center gap-1.5 rounded-full bg-primary/10 px-2.5 py-0.5 text-xs font-medium text-primary">
                {story.topic}
              </span>
            )}
            {story.freshness && (
              <FreshnessBadge band={story.freshness.band} score={story.freshness.score} />
            )}
          </div>
          <span className="inline-flex shrink-0 items-center gap-1 text-xs text-muted-foreground">
            <Newspaper className="h-3.5 w-3.5" />
            {t("storyCard.sources", { n: formatCompact(story.totalCoverage) })}
          </span>
        </div>

        <h3 className="line-clamp-2 font-semibold leading-snug tracking-tight group-hover:text-primary">
          {story.title}
        </h3>
        <p className="mt-1.5 line-clamp-2 text-sm text-muted-foreground">{story.summary}</p>

        <div className="flex-1" />

        {hasImage && (
          <div className="mt-4">
            <SpectrumBar distribution={story.distribution} height={8} showLegend={false} />
          </div>
        )}

        <div className="mt-3 flex items-center justify-between text-xs text-muted-foreground">
          {story.blindspotSide ? (
            <span
              className="inline-flex items-center gap-1 font-medium"
              style={{ color: LEAN_META[story.blindspotSide].color }}
            >
              <EyeOff className="h-3.5 w-3.5" />
              {t("storyCard.thinOn", { side: t(`filter.${story.blindspotSide}`).toLowerCase() })}
            </span>
          ) : (
            <span>{t("storyCard.updated", { time: timeAgo(story.updatedAt) })}</span>
          )}
          <span className="inline-flex items-center gap-0.5 font-medium text-foreground/70 transition-colors group-hover:text-primary">
            {t("storyCard.compare")}
            <ArrowRight className={cn("h-3.5 w-3.5 transition-transform group-hover:translate-x-0.5")} />
          </span>
        </div>
      </Link>
    </Wrapper>
  );
}

/** The story's own coverage, drawn large in the hero slot: one column per side, height ∝ share,
 *  in the diverging lean palette — a per-story figure from counted facts (the same numbers the
 *  small spectrum strip shows on imaged cards). A 0% side keeps a stub column + label, so the
 *  triptych always reads left-to-right and a missing side is VISIBLY missing. */
function CoverageFigure({ story }: { story: Story }) {
  const { t } = useTranslation();
  const sides = ["left", "center", "right"] as const;
  // On imageless cards this figure IS the card's distribution display, so announce it: one
  // label carrying the three percentages (the visual columns are then decorative detail).
  const label = sides
    .map((s) => `${t(`filter.${s}`)} ${Math.round((story.distribution?.[s] ?? 0) * 100)}%`)
    .join(" · ");
  return (
    <div
      role="img"
      aria-label={label}
      className="mb-3 flex aspect-[16/9] items-end justify-center gap-8 overflow-hidden rounded-lg bg-muted/40 px-6 pb-4 pt-6"
    >
      {sides.map((side) => {
        const share = Math.max(0, Math.min(1, story.distribution?.[side] ?? 0));
        const pct = Math.round(share * 100);
        return (
          <div key={side} className="flex h-full w-14 min-w-0 flex-col items-center justify-end gap-1">
            <span className="text-xs font-medium tabular-nums text-muted-foreground">{pct}%</span>
            <div
              className="w-full rounded-t"
              style={{
                height: `${Math.max(share * 72, 2.5)}%`,
                backgroundColor: LEAN_META[side].color,
                opacity: share > 0 ? 0.8 : 0.25,
              }}
            />
            <span className="text-[0.7rem] text-muted-foreground">{t(`filter.${side}`)}</span>
          </div>
        );
      })}
    </div>
  );
}
