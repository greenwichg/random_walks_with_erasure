"use client";

import * as React from "react";
import { Building2 } from "lucide-react";
import { logoCandidates, isTooLowRes, nextCandidate } from "@/lib/publisher-logo";
import { cn } from "@/lib/utils";

/**
 * A publisher's mark, at the best resolution the publisher actually exposes.
 *
 * Walks the candidate chain the engine supplies (curated -> Wikimedia -> Apple touch icon ->
 * favicon), dropping to the next one when an image fails to load OR loads too small for the box
 * it has to fill. That second condition is the point: a 16px `favicon.ico` does not error, it
 * renders — blurrily — at 4.5x upscale in the 36px profile header, which is exactly what shipped
 * before. Failing loudly on a 404 while accepting a silent quality failure is backwards.
 *
 * When the chain runs out, the glyph. A publisher exposing nothing usable should look like it has
 * no logo, not like it has a bad one.
 *
 * `sizePx` is the CSS size of the CONTENT box (after any padding), because that is what decides
 * how many real pixels the image needs. Aspect ratio is always preserved — `object-contain`
 * letterboxes a wordmark rather than distorting it.
 */
export function PublisherLogo({
  logo,
  fallbacks,
  sizePx,
  className,
  glyphClassName,
  loading = "lazy",
  fallbackNode,
}: {
  logo?: string | null;
  fallbacks?: string[] | null;
  sizePx: number;
  className?: string;
  glyphClassName?: string;
  loading?: "lazy" | "eager";
  /** What exhaustion renders instead of the default building glyph — the coverage plate's chips
   *  show a monogram, which carries more identity at 20px than an anonymous glyph. */
  fallbackNode?: React.ReactNode;
}) {
  const candidates = React.useMemo(() => logoCandidates(logo, fallbacks), [logo, fallbacks]);
  const [current, setCurrent] = React.useState<string | null>(() => candidates[0] ?? null);

  // A new publisher restarts the walk — otherwise a card recycled in a list keeps the previous
  // outlet's exhausted state and shows the glyph for a publisher that has a perfectly good mark.
  React.useEffect(() => setCurrent(candidates[0] ?? null), [candidates]);

  const advance = React.useCallback(
    () => setCurrent((c) => nextCandidate(candidates, c)),
    [candidates],
  );

  if (!current) {
    if (fallbackNode !== undefined) return <>{fallbackNode}</>;
    return <Building2 className={cn(glyphClassName ?? className)} aria-hidden="true" />;
  }

  return (
    // eslint-disable-next-line @next/next/no-img-element
    <img
      src={current}
      alt=""
      loading={loading}
      // Intrinsic hint matches the box it is drawn into, so the browser reserves the right space
      // and never reports a layout shift for a logo that swapped mid-walk.
      width={sizePx}
      height={sizePx}
      onError={advance}
      onLoad={(e) => {
        const dpr = typeof window === "undefined" ? 1 : window.devicePixelRatio || 1;
        if (isTooLowRes(e.currentTarget.naturalWidth, sizePx, dpr, current)) advance();
      }}
      className={cn("object-contain", className)}
    />
  );
}
