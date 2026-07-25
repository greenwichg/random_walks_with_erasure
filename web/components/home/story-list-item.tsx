"use client";

import Link from "next/link";
import { EyeOff } from "lucide-react";
import type { Story } from "@/types/domain";
import { SpectrumBar } from "@/components/shared/spectrum-bar";
import { ArticleImage } from "@/components/shared/article-image";
import { LEAN_META } from "@/lib/metrics";
import { useTranslation } from "@/lib/i18n";
import { cn } from "@/lib/utils";

/**
 * A compact story row — the workhorse of the dense sections (Top Stories, category modules).
 *
 * Scan-optimised rather than card-heavy: headline first, then the coverage signals that make this
 * product different (how many sources, how the spectrum splits, whether one side is missing). The
 * thumbnail is optional so a text-only row stays perfectly aligned with an illustrated one.
 */
export function StoryListItem({
  story,
  rank,
  showImage = false,
  className,
}: {
  story: Story;
  /** 1-based position, rendered as a quiet ordinal when provided. */
  rank?: number;
  showImage?: boolean;
  className?: string;
}) {
  const { t, formatCompact } = useTranslation();

  return (
    <li className={cn("group", className)}>
      <Link
        href={`/stories/${story.id}`}
        className="flex gap-3.5 rounded-md py-2.5 transition-colors focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring focus-visible:ring-offset-2"
      >
        {rank != null && (
          <span
            aria-hidden
            className="mt-0.5 w-5 shrink-0 font-mono text-sm font-semibold tabular-nums text-muted-foreground/60"
          >
            {rank}
          </span>
        )}

        <div className="min-w-0 flex-1">
          <h3 className="line-clamp-2 text-sm font-semibold leading-snug tracking-tight transition-colors group-hover:text-primary">
            {story.title}
          </h3>

          <div className="mt-1.5 flex flex-wrap items-center gap-x-3 gap-y-1 text-xs text-muted-foreground">
            <span>{t("storyCard.sources", { n: formatCompact(story.totalCoverage) })}</span>
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

          <div className="mt-2 max-w-[16rem]">
            <SpectrumBar distribution={story.distribution} height={4} showLegend={false} />
          </div>
        </div>

        {showImage && story.image && (
          <ArticleImage
            src={story.image}
            alt={story.title}
            aspect="aspect-[4/3]"
            className="w-20 shrink-0 sm:w-24"
          />
        )}
      </Link>
    </li>
  );
}
