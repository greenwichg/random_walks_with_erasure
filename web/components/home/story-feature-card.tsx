"use client";

import Link from "next/link";
import { EyeOff } from "lucide-react";
import type { Story } from "@/types/domain";
import { ArticleImage } from "@/components/shared/article-image";
import { SpectrumBar } from "@/components/shared/spectrum-bar";
import { FreshnessBadge } from "@/components/stories/freshness-badge";
import { LEAN_META } from "@/lib/metrics";
import { useTranslation } from "@/lib/i18n";

/**
 * An image-forward story card — the middle weight between the full-bleed `HeroStory` and the
 * compact `StoryListItem`.
 *
 * It exists for RHYTHM. A front page built from one repeated row shape reads as a table; the three
 * weights together (hero → feature pair → rows) give a section a lead, a second tier, and a tail,
 * which is how a print front page actually organises attention.
 *
 * Same `Story` contract and same signals as the other two, so a reader learns one visual language:
 * topic, freshness, coverage spectrum, and the blind-spot flag when the Story Service set one.
 */
export function StoryFeatureCard({ story }: { story: Story }) {
  const { t, formatCompact, timeAgo } = useTranslation();

  return (
    <article className="group h-full">
      <Link
        href={`/stories/${story.id}`}
        className="flex h-full flex-col overflow-hidden rounded-lg border bg-card transition-colors hover:bg-accent/40 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring focus-visible:ring-offset-2"
      >
        <ArticleImage src={story.image} alt={story.title} className="rounded-none border-0" />

        <div className="flex flex-1 flex-col p-4">
          <div className="mb-2 flex flex-wrap items-center gap-2 text-xs">
            <span className="font-medium text-foreground/70">{story.topic}</span>
            {story.freshness && (
              <FreshnessBadge band={story.freshness.band} score={story.freshness.score} />
            )}
          </div>

          <h3 className="line-clamp-3 text-base font-semibold leading-snug tracking-tight transition-colors group-hover:text-primary">
            {story.title}
          </h3>

          <div className="mt-3 flex-1" />

          <SpectrumBar distribution={story.distribution} height={4} showLegend={false} />

          <div className="mt-2.5 flex flex-wrap items-center gap-x-3 gap-y-1 text-xs text-muted-foreground">
            <span>{t("storyCard.sources", { n: formatCompact(story.totalCoverage) })}</span>
            {story.updatedAt && <span>{timeAgo(story.updatedAt)}</span>}
            {story.blindspotSide && (
              <span
                className="inline-flex items-center gap-1 font-medium"
                style={{ color: LEAN_META[story.blindspotSide].color }}
              >
                <EyeOff className="h-3 w-3" aria-hidden />
                {t("storyCard.thinOn", { side: t(`filter.${story.blindspotSide}`).toLowerCase() })}
              </span>
            )}
          </div>
        </div>
      </Link>
    </article>
  );
}
