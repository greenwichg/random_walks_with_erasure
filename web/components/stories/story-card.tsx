"use client";

import Link from "next/link";
import { motion } from "framer-motion";
import { Newspaper, ArrowRight, EyeOff } from "lucide-react";
import type { Story } from "@/types/domain";
import { SpectrumBar } from "@/components/shared/spectrum-bar";
import { ArticleImage } from "@/components/shared/article-image";
import { FreshnessBadge } from "@/components/stories/freshness-badge";
import { LEAN_META } from "@/lib/metrics";
import { compact, timeAgo, cn } from "@/lib/utils";

/** A clustered-story preview card — one event, coverage across the spectrum. */
export function StoryCard({ story, index = 0 }: { story: Story; index?: number }) {
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
            <span className="inline-flex items-center gap-1.5 rounded-full bg-primary/10 px-2.5 py-0.5 text-xs font-medium text-primary">
              {story.topic}
            </span>
            {story.freshness && (
              <FreshnessBadge band={story.freshness.band} score={story.freshness.score} />
            )}
          </div>
          <span className="inline-flex shrink-0 items-center gap-1 text-xs text-muted-foreground">
            <Newspaper className="h-3.5 w-3.5" />
            {compact(story.totalCoverage)} sources
          </span>
        </div>

        <h3 className="line-clamp-2 font-semibold leading-snug tracking-tight group-hover:text-primary">
          {story.title}
        </h3>
        <p className="mt-1.5 line-clamp-2 flex-1 text-sm text-muted-foreground">{story.summary}</p>

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
              Thin on the {LEAN_META[story.blindspotSide].label.toLowerCase()}
            </span>
          ) : (
            <span>Updated {timeAgo(story.updatedAt)}</span>
          )}
          <span className="inline-flex items-center gap-0.5 font-medium text-foreground/70 transition-colors group-hover:text-primary">
            Compare
            <ArrowRight className={cn("h-3.5 w-3.5 transition-transform group-hover:translate-x-0.5")} />
          </span>
        </div>
      </Link>
    </motion.div>
  );
}
