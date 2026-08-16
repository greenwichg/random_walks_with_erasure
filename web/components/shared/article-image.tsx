"use client";

import * as React from "react";
import { cn } from "@/lib/utils";

/**
 * A lazy, self-hiding article / story hero image. Renders **nothing** when there is no URL or the image
 * fails to load, so every card degrades cleanly to its existing text-only layout. Native browser
 * caching only — no download, no proxy, no `next/image` remote config. The image URL stays canonical.
 *
 * `priority` is for the handful of images that ARE the page's largest contentful paint — the home
 * hero, a story detail's hero, the first cards of a grid. Lazy-loading those is the classic LCP
 * anti-pattern this flag exists to end (R4): the browser defers the request behind an
 * intersection check and fetches it at low priority, so the element the reader is waiting for
 * queues behind everything that is not. Measured on this app with the RUM harness before the flag
 * existed: the LCP element on /, /stories and /discover was exactly this component's `<img>`,
 * starting 380–470 ms into the load. Everything below the fold stays lazy — that part of the
 * original design was right.
 */
export function ArticleImage({
  src,
  alt,
  className,
  aspect = "aspect-[16/9]",
  priority = false,
  onHidden,
}: {
  src?: string | null;
  alt: string;
  className?: string;
  aspect?: string;
  priority?: boolean;
  /** Fired when a load ERROR hides the image (never for an absent src — the caller already knows
   *  absence). Story surfaces use it to swap in the coverage plate instead of leaving the void
   *  the reserved slot becomes: the engine never downloads images, so a dead or
   *  hotlink-protected hero URL is only discoverable here, in the reader's browser. */
  onHidden?: () => void;
}) {
  const [failed, setFailed] = React.useState(false);
  React.useEffect(() => setFailed(false), [src]); // re-try when the source changes

  if (!src || failed) return null;
  // Lowercase `fetchpriority` through a spread: React 18 passes unknown lowercase attributes to the
  // DOM verbatim, while the camelCase prop warns until React 19. The record-typed spread keeps
  // TypeScript out of the argument without an `any`.
  const priorityAttrs = priority ? ({ fetchpriority: "high" } as Record<string, string>) : {};
  return (
    <div className={cn("overflow-hidden rounded-lg bg-muted", aspect, className)}>
      {/* eslint-disable-next-line @next/next/no-img-element */}
      <img
        src={src}
        alt={alt}
        loading={priority ? "eager" : "lazy"}
        decoding="async"
        {...priorityAttrs}
        onError={() => {
          setFailed(true);
          onHidden?.();
        }}
        className="h-full w-full object-cover"
      />
    </div>
  );
}
