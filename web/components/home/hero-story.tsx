"use client";

import Link from "next/link";
import { ArrowRight, EyeOff, Newspaper } from "lucide-react";
import type { Story } from "@/types/domain";
import { ArticleImage } from "@/components/shared/article-image";
import { SpectrumBar } from "@/components/shared/spectrum-bar";
import { FreshnessBadge } from "@/components/stories/freshness-badge";
import { LEAN_META } from "@/lib/metrics";
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
        <ArticleImage
          src={story.image}
          alt={story.title}
          className="aspect-[16/9] w-full rounded-none border-0"
        />

        <div className="p-5 sm:p-6">
          <div className="mb-3 flex flex-wrap items-center gap-2">
            <span className="text-[0.7rem] font-semibold uppercase tracking-wider text-primary">
              {t("home.hero.eyebrow")}
            </span>
            <span className="inline-flex items-center rounded-full bg-accent px-2.5 py-0.5 text-xs font-medium text-accent-foreground">
              {story.topic}
            </span>
            {story.freshness && (
              <FreshnessBadge band={story.freshness.band} score={story.freshness.score} />
            )}
          </div>

          {/* The one <h2> of this module — the page's own <h1> stays the site heading. */}
          <h2 className="text-balance text-2xl font-semibold leading-tight tracking-tight transition-colors group-hover:text-primary sm:text-3xl">
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
