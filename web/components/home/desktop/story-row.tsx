"use client";

import Link from "next/link";
import type { Story } from "@ih/core/domain/types";
import { ArticleImage } from "@/components/shared/article-image";
import { BiasStrip } from "@/components/home/desktop/bias-strip";
import { useTranslation } from "@/lib/i18n";
import { cn } from "@/lib/utils";

/**
 * One story in a desktop front-page list — the reference layout's row: a small grey kicker
 * (topic · age), the headline, the coverage strip with its caption, and an optional square
 * thumbnail on the right. Rows are separated by hairlines, not cards.
 *
 * `size` sets the headline scale: "sm" for the side columns and the closing lists, "md" for the
 * centre column beside the lead. The freshness band is deliberately not repeated here — the row's
 * age says it — and the topic is dropped inside a single-topic section (`showTopic={false}`).
 */
export function StoryRow({
  story,
  size = "sm",
  thumb = false,
  showTopic = true,
  className,
}: {
  story: Story;
  size?: "sm" | "md";
  thumb?: boolean;
  showTopic?: boolean;
  className?: string;
}) {
  const { timeAgo } = useTranslation();
  const kicker = [showTopic ? story.topic : "", story.updatedAt ? timeAgo(story.updatedAt) : ""]
    .filter(Boolean)
    .join(" · ");

  return (
    <li className={cn("group border-b border-border/70 last:border-b-0", className)}>
      <Link
        href={`/stories/${story.id}`}
        className={cn(
          "flex gap-3 rounded-sm py-3 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring focus-visible:ring-offset-2",
          size === "md" ? "py-3.5" : "py-3",
        )}
      >
        <div className="min-w-0 flex-1">
          {kicker && <p className="mb-1 text-[11px] leading-tight text-muted-foreground">{kicker}</p>}
          <h3
            className={cn(
              "font-semibold leading-snug tracking-tight transition-colors group-hover:text-primary",
              size === "md" ? "text-[15px]" : "text-[14px]",
            )}
          >
            {story.title}
          </h3>
          <BiasStrip distribution={story.distribution} sources={story.totalCoverage} className="mt-2 max-w-[22rem]" />
        </div>
        {thumb && story.image && (
          <ArticleImage
            src={story.image}
            alt=""
            aspect="aspect-square"
            className="w-[72px] shrink-0 rounded-md"
          />
        )}
      </Link>
    </li>
  );
}
