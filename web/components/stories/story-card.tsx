"use client";

import * as React from "react";
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
 * Imageless cards follow the newspaper rule — no photo means the TYPE gets senior: a larger,
 * deeper-clamped headline and summary, plus the coverage line (real publisher names the story
 * already carries). Grid rows stretch cards to equal height, so without this the flex spacer
 * renders the difference as dead space; with it, the space holds the product's own facts —
 * never a stock illustration or a placeholder box. */
export function StoryCard({ story, index = 0 }: { story: Story; index?: number }) {
  const { t, formatCompact, timeAgo } = useTranslation();
  const hasImage = Boolean(story.image);
  // Coverage line candidates: real outlet names only. GDELT's long-tail publishers are
  // prettified domains ("1003Thepeak.Iheart.Com") — dots/digits give them away — and a line of
  // those reads worse than whitespace. All sources stay counted in the header either way.
  const cleanPublishers = React.useMemo(
    () => (story.publishers ?? []).filter((p) => !/[.\d]/.test(p)),
    [story.publishers],
  );
  return (
    <motion.div
      initial={{ opacity: 0, y: 10 }}
      animate={{ opacity: 1, y: 0 }}
      transition={{ delay: Math.min(index * 0.05, 0.35), ease: [0.16, 1, 0.3, 1] }}
    >
      <Link
        href={`/stories/${story.id}`}
        className="group flex h-full flex-col rounded-lg border bg-card p-5 shadow-soft transition-all hover:-translate-y-0.5 hover:shadow-card"
      >
        <ArticleImage src={story.image} alt={story.title} className="mb-3" />

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

        <h3
          className={cn(
            "font-semibold leading-snug tracking-tight group-hover:text-primary",
            hasImage ? "line-clamp-2" : "line-clamp-3 text-lg",
          )}
        >
          {story.title}
        </h3>
        <p
          className={cn(
            "mt-1.5 text-sm text-muted-foreground",
            hasImage ? "line-clamp-2" : "line-clamp-6 leading-relaxed",
          )}
        >
          {story.summary}
        </p>

        {/* Imageless: the coverage line — who is actually reporting this. Real outlet names
            only (a wall of prettified domains is noise, not evidence); enough of them to wrap
            and fill, clamped so this card never out-stretches its row. */}
        {!hasImage && cleanPublishers.length > 0 && (
          <p className="mt-3 line-clamp-4 text-xs leading-relaxed text-muted-foreground">
            <span className="font-medium text-foreground/70">
              {cleanPublishers.slice(0, 10).join(" · ")}
            </span>
            {(story.publishers?.length ?? 0) > Math.min(cleanPublishers.length, 10) && " …"}
          </p>
        )}

        {/* One structural slack point: grid rows stretch cards to equal height, and whatever
            space this card can't fill with content sits HERE, above the spectrum bar — an
            anchored footer with grouped text above it, not a hole in the middle of the copy. */}
        <div className="flex-1" />

        <div className="mt-4">
          <SpectrumBar distribution={story.distribution} height={8} showLegend={false} />
        </div>

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
    </motion.div>
  );
}
