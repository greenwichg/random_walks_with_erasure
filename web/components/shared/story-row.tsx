"use client";

import Link from "next/link";
import type { Story } from "@ih/core/domain/types";
import { CardImage } from "@/components/shared/card-image";
import { BiasStrip } from "@/components/shared/bias-strip";
import { useTranslation } from "@/lib/i18n";
import { cn } from "@/lib/utils";

/**
 * ONE story row, shared by every list on both layouts — the reference's list item in both its
 * desktop and mobile forms, which are the same object at two scales:
 *
 *   kicker (topic · age) → headline → coverage strip + "N% Centre coverage: N sources"
 *   → optional "See the story" affordance, with an optional square thumbnail on the right.
 *
 * Sizes: `sm` for side columns and closing runs, `md` for a main column beside a lead, `lg` for
 * the mobile feed, where the row IS the page and the headline carries it. Rows are separated by
 * hairlines, never cards — the card is the surface they sit on.
 *
 * The freshness band is deliberately not repeated here (the age says it) and the topic is dropped
 * inside a single-topic section (`showTopic={false}`).
 */
export function StoryRow({
  story,
  size = "sm",
  thumb = false,
  showTopic = true,
  action = false,
  className,
}: {
  story: Story;
  size?: "sm" | "md" | "lg";
  thumb?: boolean;
  showTopic?: boolean;
  /** Render the "See the story" line under the coverage caption (the mobile feed's affordance).
   *  The whole row is already the link; this names the destination the way the reference does. */
  action?: boolean;
  className?: string;
}) {
  const { t, timeAgo } = useTranslation();
  const kicker = [showTopic ? story.topic : "", story.updatedAt ? timeAgo(story.updatedAt) : ""]
    .filter(Boolean)
    .join(" · ");

  return (
    <li className={cn("group border-b border-border/70 last:border-b-0", className)}>
      <Link
        href={`/stories/${story.id}`}
        className={cn(
          "flex gap-3 rounded-sm focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring focus-visible:ring-offset-2",
          size === "lg" ? "py-4" : size === "md" ? "py-3.5" : "py-3",
        )}
      >
        <div className="min-w-0 flex-1">
          {kicker && <p className="mb-1 text-[11px] leading-tight text-muted-foreground">{kicker}</p>}
          <h3
            className={cn(
              "font-semibold leading-snug tracking-tight transition-colors group-hover:text-primary",
              size === "lg" ? "text-[17px]" : size === "md" ? "text-[15px]" : "text-[14px]",
            )}
          >
            {story.title}
          </h3>
          <BiasStrip
            distribution={story.distribution}
            sources={story.totalCoverage}
            className={cn("mt-2", size === "lg" ? "max-w-none" : "max-w-[22rem]")}
          />
          {action && (
            <span className="mt-2 inline-block text-[13px] font-medium text-foreground underline underline-offset-4 group-hover:text-primary">
              {t("storyCard.seeStory")}
            </span>
          )}
        </div>
        {thumb && (
          // Every row that asks for a thumbnail gets one: art, or the shared fallback. The old
          // `&& story.image` left holes down the right-hand column of a mixed list.
          <CardImage
            src={story.image}
            alt=""
            aspect="aspect-square"
            className={cn("shrink-0 rounded-md", size === "lg" ? "w-[88px]" : "w-[72px]")}
          />
        )}
      </Link>
    </li>
  );
}
