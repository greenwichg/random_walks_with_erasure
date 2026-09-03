"use client";

import * as React from "react";
import { StoryFallbackArt } from "@/components/shared/story-fallback-art";
import { cn } from "@/lib/utils";

/**
 * THE card image slot — one implementation of "what fronts a card", for stories and articles
 * alike. It is never empty: a card either shows its own art or the shared newspaper fallback
 * (`story-fallback-art.tsx`), in the identical box, at the identical aspect, cropped the same way.
 *
 * Four ways a card ends up without its own art, one outcome:
 *   * the story/article carries no image at all;
 *   * the engine flagged the image as branding (`imageSuspect`) — furniture never fronts as art;
 *   * the URL is dead or hotlink-protected, which ONLY this browser can discover (the engine
 *     never downloads images), so the swap happens on the `error` event;
 *   * the URL loads but decodes to nothing.
 *
 * Callers do not branch. Before this existed, ten surfaces each wrote their own
 * `image ? <ArticleImage/> : <SomePlate/>` — four of them simply rendered nothing, which is how a
 * list of thumbnails ended up with holes in the right-hand column. Passing `src` and letting this
 * decide is what makes the fallback consistent everywhere, which was the whole requirement.
 *
 * `priority` is for the handful of slots that ARE the page's largest contentful paint — the home
 * hero, a story detail's hero, the first cards of a grid. Lazy-loading those is the classic LCP
 * anti-pattern the flag exists to end; everything below the fold stays lazy.
 */
export function CardImage({
  src,
  alt,
  className,
  aspect = "aspect-[16/9]",
  priority = false,
  suspect = false,
  onFallback,
}: {
  src?: string | null;
  /** Empty string for a decorative thumbnail whose card already names the story in text. */
  alt: string;
  className?: string;
  aspect?: string;
  priority?: boolean;
  /** Engine-flagged branding — treated exactly like an absent image (anti-deception rule). */
  suspect?: boolean;
  /** Fired when a load ERROR (not absence) hands the slot to the fallback. Story surfaces use it
   *  for the `story_hero_error` beacon: a dead hero URL is measurable only from here. */
  onFallback?: () => void;
}) {
  const [failed, setFailed] = React.useState(false);
  React.useEffect(() => setFailed(false), [src]); // re-try when the source changes

  const usable = Boolean(src) && !suspect && !failed;
  // Lowercase `fetchpriority` through a spread: React 18 passes unknown lowercase attributes to
  // the DOM verbatim, while the camelCase prop warns until React 19.
  const priorityAttrs = priority ? ({ fetchpriority: "high" } as Record<string, string>) : {};

  return (
    <div className={cn("overflow-hidden rounded-lg bg-muted", aspect, className)}>
      {usable ? (
        // eslint-disable-next-line @next/next/no-img-element
        <img
          src={src ?? undefined}
          alt={alt}
          loading={priority ? "eager" : "lazy"}
          decoding="async"
          {...priorityAttrs}
          onError={() => {
            setFailed(true);
            onFallback?.();
          }}
          className="h-full w-full object-cover"
        />
      ) : (
        // Decorative: it states "no picture was published with this story", and the card names the
        // story, its publisher and its coverage in text. Announcing it would add nothing but noise
        // to every imageless card in a grid of twenty-four.
        <div aria-hidden="true" className="h-full w-full">
          <StoryFallbackArt />
        </div>
      )}
    </div>
  );
}
