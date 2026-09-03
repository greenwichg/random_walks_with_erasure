"use client";

import Link from "next/link";
import type { Story } from "@ih/core/domain/types";
import { CardImage } from "@/components/shared/card-image";
import { BiasStrip } from "@/components/shared/bias-strip";
import { useTranslation } from "@/lib/i18n";
import { cn } from "@/lib/utils";

/**
 * A picture card for a single story — the reference's blind-spot and topic cards, shared by the
 * desktop rail, the desktop topic sections and the mobile feed.
 *
 * Picture (or the shared newspaper fallback when the story has none) → labelled coverage strip → kicker →
 * headline. A plate never gets a strip beneath it: the plate already carries the labelled band,
 * and saying it twice was the thing the desktop pass removed.
 */
export function SpotCard({
  story,
  showTopic = true,
  className,
}: {
  story: Story;
  showTopic?: boolean;
  className?: string;
}) {
  const { timeAgo } = useTranslation();
  const kicker = [showTopic ? story.topic : "", story.updatedAt ? timeAgo(story.updatedAt) : ""]
    .filter(Boolean)
    .join(" · ");

  return (
    <li className={cn("group", className)}>
      <Link
        href={`/stories/${story.id}`}
        className="block rounded-md focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring focus-visible:ring-offset-2"
      >
        {/* Picture or the shared fallback — a spot card keeps its shape either way. */}
        <CardImage src={story.image} alt="" aspect="aspect-[16/9]" className="rounded-md" />
        <div className="mt-2">
          <BiasStrip distribution={story.distribution} labels />
        </div>
        {kicker && <p className="mt-1.5 text-[11px] text-muted-foreground">{kicker}</p>}
        <h3 className="mt-1 text-[13px] font-semibold leading-snug tracking-tight transition-colors group-hover:text-primary">
          {story.title}
        </h3>
      </Link>
    </li>
  );
}
