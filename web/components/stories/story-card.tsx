"use client";

import * as React from "react";
import Link from "next/link";
import { motion } from "framer-motion";
import { Newspaper, ArrowRight, EyeOff } from "lucide-react";
import type { Story } from "@ih/core/domain/types";
import { track, urlHost } from "@/lib/analytics";
import { useTranslation } from "@/lib/i18n";
import { SpectrumBar } from "@/components/shared/spectrum-bar";
import { CardImage } from "@/components/shared/card-image";
import { FreshnessBadge } from "@/components/stories/freshness-badge";
import { LEAN_META } from "@ih/core/logic/metrics";
import { cn } from "@/lib/utils";

/** A clustered-story preview card — one event, coverage across the spectrum.
 *
 * Imageless stories get the shared newspaper fallback in the image slot (card-image.tsx), the same
 * one every article and story card in the app falls back to. It replaced the COVERAGE PLATE, which
 * put this card's facts INSIDE the image slot and therefore had to suppress the spectrum strip and
 * the thin-side chip below to avoid saying each thing twice. With one fallback image for every
 * card, that conditional goes away: an imageless card now renders exactly like an imaged one —
 * same strip, same chip, same rhythm — and the grid has one skeleton instead of two. */
export function StoryCard({ story, index = 0, priority = false }: { story: Story; index?: number; priority?: boolean }) {
  const { t, formatCompact, timeAgo } = useTranslation();
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
        // Hover is a tone change, not a lift: the grid stays a flat, aligned sheet of cards and
        // the headline's colour carries the affordance (desktop rework).
        className="group flex h-full flex-col rounded-lg border bg-card p-5 shadow-soft transition-shadow hover:shadow-card"
      >
        <CardImage
          src={story.image}
          alt={story.title}
          priority={priority}
          className="mb-3"
          onFallback={() => track("story_hero_error", { host: urlHost(story.image), surface: "card" })}
        />

        <div className="mb-2 flex items-center justify-between gap-2">
          <div className="flex min-w-0 items-center gap-1.5">
            {/* Neutral chip, same as the home lead's: the topic is a label, not a control, and
                accent colour is reserved for interactive state (globals.css). This was the one
                surface still painting it purple, so a grid of 24 cards read as 24 buttons. */}
            {story.topic && (
              <span className="inline-flex items-center gap-1.5 rounded-full bg-accent px-2.5 py-0.5 text-xs font-medium text-accent-foreground">
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

        <div className="mt-4">
          <SpectrumBar distribution={story.distribution} height={8} showLegend={false} />
        </div>

        <div className="mt-3 flex items-center justify-between text-xs text-muted-foreground">
          {/* Unconditional now: the fallback image carries no facts, so nothing below it can be a
              repeat. Every card states its distribution and its thin side the same way. */}
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

