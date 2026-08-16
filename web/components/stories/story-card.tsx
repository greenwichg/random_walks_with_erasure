"use client";

import * as React from "react";
import Link from "next/link";
import { motion } from "framer-motion";
import { Newspaper, ArrowRight, EyeOff } from "lucide-react";
import type { Story } from "@/types/domain";
import { track, urlHost } from "@/lib/analytics";
import { useTranslation } from "@/lib/i18n";
import { SpectrumBar } from "@/components/shared/spectrum-bar";
import { ArticleImage } from "@/components/shared/article-image";
import { FreshnessBadge } from "@/components/stories/freshness-badge";
import { CoveragePlate } from "@/components/stories/coverage-plate";
import { LEAN_META } from "@/lib/metrics";
import { cn } from "@/lib/utils";

/** A clustered-story preview card — one event, coverage across the spectrum.
 *
 * Imageless stories render the COVERAGE PLATE in the image slot (coverage-plate.tsx): kicker,
 * publisher chips, the publisher-count credential and the labeled distribution band, composed as
 * a designed object. Same slot, same aspect, so imaged and imageless cards share one skeleton
 * and grid rows never leave a void — and every mark is a counted fact unique to each story,
 * never a stock illustration or category artwork. (The plate replaces the small spectrum strip
 * AND, on gap stories, the thin-side chip; each fact is said once.) */
export function StoryCard({ story, index = 0, priority = false }: { story: Story; index?: number; priority?: boolean }) {
  const { t, formatCompact, timeAgo } = useTranslation();
  // A hero URL that fails to LOAD is a third state the engine cannot see (it never downloads
  // images, so a dead or hotlink-protected URL is only observable here): the reserved slot used
  // to become a silent void with the spectrum strip still attached beneath it. Failure hands the
  // slot to the plate and the strip/chip logic follows, so a dead hero and an absent hero end in
  // the same designed card. Reset when a refetch changes the URL.
  const [heroFailed, setHeroFailed] = React.useState(false);
  React.useEffect(() => setHeroFailed(false), [story.image]);
  const showImage = Boolean(story.image) && !heroFailed;
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
        {showImage ? (
          <ArticleImage
            src={story.image}
            alt={story.title}
            priority={priority}
            className="mb-3"
            onHidden={() => {
              setHeroFailed(true);
              track("story_hero_error", { host: urlHost(story.image), surface: "card" });
            }}
          />
        ) : (
          <CoveragePlate story={story} />
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

        {showImage && (
          <div className="mt-4">
            <SpectrumBar distribution={story.distribution} height={8} showLegend={false} />
          </div>
        )}

        <div className="mt-3 flex items-center justify-between text-xs text-muted-foreground">
          {/* On imageless gap stories the plate already STATES the thin side (with its rated
              share) — repeating it here as a chip would say the same thing twice, so those
              cards show the update time like any other. */}
          {story.blindspotSide && showImage ? (
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

