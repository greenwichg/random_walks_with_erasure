"use client";

import Link from "next/link";
import { ArrowRight, EyeOff, Newspaper } from "lucide-react";
import type { Story } from "@ih/core/domain/types";
import { ArticleImage } from "@/components/shared/article-image";
import { SpectrumBar } from "@/components/shared/spectrum-bar";
import { FreshnessBadge } from "@/components/stories/freshness-badge";
import { LEAN_META } from "@ih/core/logic/metrics";
import { useTranslation } from "@/lib/i18n";

/**
 * The lead story — the page's single largest editorial moment. Same data contract as `StoryCard`
 * (it is the same `Story`), rendered at hero scale: full-bleed image, generous headline, the
 * cross-publisher spectrum bar, and the blind-spot signal when the Story Service flagged one.
 *
 * The spectrum bar is the product's signature: even at a glance the reader sees *how* an event is
 * being covered, not just that it happened.
 */
export function HeroStory({ story }: { story: Story }) {
  const { t, formatCompact, timeAgo } = useTranslation();
  const publisherCount = story.publisherCount ?? story.publishers?.length ?? null;

  return (
    <article className="group relative overflow-hidden rounded-lg border bg-card shadow-soft transition-shadow hover:shadow-card">
      <Link href={`/stories/${story.id}`} className="block focus-visible:outline-none">
        {/* Slow, motion-safe zoom on hover — the card's overflow-hidden clips it. The one image
            micro-interaction on the page; rows and modules stay still. */}
        <ArticleImage
          src={story.image}
          alt={story.title}
          priority
          className="aspect-[16/9] w-full rounded-none border-0 transition-transform duration-700 ease-out motion-safe:group-hover:scale-[1.02]"
        />

        <div className="p-5">
          <div className="mb-3 flex flex-wrap items-center gap-2">
            <span className="text-[0.7rem] font-semibold uppercase tracking-wider text-muted-foreground">
              {t("home.hero.eyebrow")}
            </span>
            {/* An uncategorized story has no topic, and the pill is a filled chip: without this
                guard a blank topic renders as an empty coloured lozenge. */}
            {story.topic && (
              <span className="inline-flex items-center rounded-full bg-accent px-2.5 py-0.5 text-xs font-medium text-accent-foreground">
                {story.topic}
              </span>
            )}
            {story.freshness && (
              <FreshnessBadge band={story.freshness.band} score={story.freshness.score} />
            )}
          </div>

          {/* The one <h2> of this module — the page's own <h1> stays the site heading. Set at
              true headline scale (34px bold, tight leading) in the display face: the lead is the
              page's thesis, and it has to be unmistakably larger than every card title below it.
              Ink, not accent — the colour arrives only on hover, so the headline reads as content
              first and a link second. */}
          <h2 className="text-balance text-[1.75rem] font-bold leading-[1.12] tracking-tight transition-colors group-hover:text-primary sm:text-[2.125rem]">
            {story.title}
          </h2>

          {story.summary && (
            <p className="mt-2.5 line-clamp-3 max-w-2xl text-sm leading-relaxed text-muted-foreground sm:text-base">
              {story.summary}
            </p>
          )}

          <div className="mt-5">
            <SpectrumBar distribution={story.distribution} height={10} />
          </div>

          <div className="mt-4 flex flex-wrap items-center gap-x-4 gap-y-2 text-xs text-muted-foreground">
            <span className="inline-flex items-center gap-1.5">
              <Newspaper className="h-3.5 w-3.5" aria-hidden />
              {t("storyCard.sources", { n: formatCompact(story.totalCoverage) })}
            </span>
            {publisherCount != null && (
              <span className="tabular-nums">{t("stories.publishers", { n: formatCompact(publisherCount) })}</span>
            )}
            {story.blindspotSide && (
              <span
                className="inline-flex items-center gap-1 font-medium"
                style={{ color: LEAN_META[story.blindspotSide].color }}
              >
                <EyeOff className="h-3.5 w-3.5" aria-hidden />
                {t("storyCard.thinOn", { side: t(`filter.${story.blindspotSide}`).toLowerCase() })}
              </span>
            )}
            <span className="ml-auto inline-flex items-center gap-1 font-medium text-foreground/70 transition-colors group-hover:text-primary">
              {t("storyCard.compare")}
              <ArrowRight className="h-3.5 w-3.5 transition-transform group-hover:translate-x-0.5" />
            </span>
          </div>

          {story.updatedAt && (
            <p className="mt-2 text-xs text-muted-foreground">
              {t("storyCard.updated", { time: timeAgo(story.updatedAt) })}
            </p>
          )}
        </div>
      </Link>
    </article>
  );
}
