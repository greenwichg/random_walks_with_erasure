"use client";

import * as React from "react";
import Link from "next/link";
import type { Story } from "@ih/core/domain/types";
import { CardImage } from "@/components/shared/card-image";
import { BiasStrip } from "@/components/shared/bias-strip";
import { useTranslation } from "@/lib/i18n";
import { cn } from "@/lib/utils";

/**
 * The lead — one story at display scale: picture (or the shared newspaper fallback), the labelled coverage
 * strip, the headline, and a dateline of counted facts. Shared by the desktop front page, the
 * desktop topic sections and the mobile feed, at two headline sizes.
 *
 * A hero URL that fails to LOAD hands its slot to the plate: the engine never downloads images,
 * so a dead or hotlink-protected URL is only observable in the reader's browser.
 */
export function LeadStory({
  story,
  size = "lg",
  headingLevel: Heading = "h2",
  className,
}: {
  story: Story;
  /** `lg` for a page lead, `md` inside a topic section on mobile. */
  size?: "md" | "lg";
  headingLevel?: "h2" | "h3";
  className?: string;
}) {
  const { t, formatCompact } = useTranslation();
  const publisherCount = story.publisherCount ?? story.publishers?.length ?? null;

  return (
    <article className={cn("group", className)}>
      <Link
        href={`/stories/${story.id}`}
        className="block rounded-md focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring focus-visible:ring-offset-2"
      >
        {/* Picture or the shared fallback, then the labelled strip — one composition for every
            lead, so a story without art is still a lead and not a different card shape. */}
        <CardImage
          src={story.image}
          alt={story.title}
          priority
          aspect="aspect-[16/9]"
          className="rounded-md"
        />
        <div className="mt-3">
          <BiasStrip distribution={story.distribution} labels />
        </div>
        <Heading
          className={cn(
            "mt-3 text-balance font-bold leading-[1.15] tracking-tight transition-colors group-hover:text-primary",
            size === "lg" ? "text-[26px]" : "text-[21px]",
          )}
        >
          {story.title}
        </Heading>
        <p className="mt-2 text-[12px] text-muted-foreground">
          {[
            story.topic,
            t("storyCard.sources", { n: formatCompact(story.totalCoverage) }),
            publisherCount != null ? t("stories.publishers", { n: formatCompact(publisherCount) }) : "",
          ]
            .filter(Boolean)
            .join(" · ")}
        </p>
      </Link>
    </article>
  );
}
