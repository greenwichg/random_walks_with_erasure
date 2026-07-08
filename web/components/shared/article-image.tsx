"use client";

import * as React from "react";
import { cn } from "@/lib/utils";

/**
 * A lazy, self-hiding article / story hero image. Renders **nothing** when there is no URL or the image
 * fails to load, so every card degrades cleanly to its existing text-only layout. Native browser
 * caching only — no download, no proxy, no `next/image` remote config. The image URL stays canonical.
 */
export function ArticleImage({
  src,
  alt,
  className,
  aspect = "aspect-[16/9]",
}: {
  src?: string | null;
  alt: string;
  className?: string;
  aspect?: string;
}) {
  const [failed, setFailed] = React.useState(false);
  React.useEffect(() => setFailed(false), [src]); // re-try when the source changes

  if (!src || failed) return null;
  return (
    <div className={cn("overflow-hidden rounded-lg bg-muted", aspect, className)}>
      {/* eslint-disable-next-line @next/next/no-img-element */}
      <img
        src={src}
        alt={alt}
        loading="lazy"
        decoding="async"
        onError={() => setFailed(true)}
        className="h-full w-full object-cover"
      />
    </div>
  );
}
