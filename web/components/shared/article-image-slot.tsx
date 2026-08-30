"use client";

import * as React from "react";
import type { Article } from "@ih/core/domain/types";
import { ArticleImage } from "@/components/shared/article-image";
import { PublisherLogo } from "@/components/shared/publisher-logo";
import { placeholderHues, monogram } from "@ih/core/logic/placeholder-art";
import { cn } from "@/lib/utils";

/**
 * The always-occupied article image slot — THE one implementation of "what fronts a card when
 * the art can't". Three ways an image doesn't lead a card, one outcome: absent, engine-flagged
 * branding (`imageSuspect` — furniture never masquerades as article art, the story-hero rule
 * applied to article surfaces), or failed to load in THIS browser (only observable here; the
 * engine never downloads images). All three render the PUBLISHER PLATE — the article-card
 * sibling of the stories' coverage plate, in the same slot geometry ArticleImage draws.
 *
 * The plate is built from the outlet's identity, nothing invented: a duotone wash in the
 * publisher's stable hue pair (hashed from the name — same outlet, same colour, every surface;
 * the wheel keeps clear of the lean axis so a wash never reads as a politics), a faint halftone
 * grid, the outlet's ghost monogram, and its mark in full colour on a raised chip via the
 * PublisherLogo fallback chain — monogram terminal when the chain runs out. Never stock art,
 * never a repeated grey box: the old dimmed-glyph fallback read as a broken image beside real
 * photos. Decorative (aria-hidden): the metadata row beside every card already NAMES the
 * publisher, and the anti-deception rule holds — a suspect image is still never shown AS art,
 * and the plate never pretends to be a photograph.
 *
 * Shared by DiscoverCard (Discover / Search / Saved) and RecommendationCard — pinned as a single
 * implementation by lib/discover-layout.test.ts.
 */
export function ArticleImageSlot({
  article,
  priority = false,
  className = "mb-3",
}: {
  article: Pick<
    Article,
    "image" | "imageSuspect" | "headline" | "publisher" | "publisherLogo" | "publisherLogoFallbacks"
  >;
  priority?: boolean;
  className?: string;
}) {
  const [imgFailed, setImgFailed] = React.useState(false);
  React.useEffect(() => setImgFailed(false), [article.image]);
  const hasImage = Boolean(article.image) && !article.imageSuspect && !imgFailed;
  if (hasImage) {
    return (
      <ArticleImage
        src={article.image}
        alt={article.headline}
        priority={priority}
        className={className}
        onHidden={() => setImgFailed(true)}
      />
    );
  }
  const { base, companion } = placeholderHues(article.publisher);
  const mark = monogram(article.publisher);
  return (
    <div
      className={cn(
        "relative grid aspect-[16/9] place-items-center overflow-hidden rounded-lg border bg-muted",
        className,
      )}
      aria-hidden="true"
      style={{
        // Layer order, top first: halftone grid (print heritage, foreground token so both themes
        // come for free), then the duotone — the publisher hue from the top-left, its companion
        // from the bottom-right. Low-alpha colour OVER the muted token ground is the coverage
        // plate's own technique: pastel over paper in light, tinted charcoal in dark, no
        // per-theme branch.
        backgroundImage: [
          "radial-gradient(hsl(var(--foreground) / 0.05) 1px, transparent 1.1px)",
          `linear-gradient(135deg, hsl(${base} 70% 55% / 0.28), transparent 62%)`,
          `linear-gradient(315deg, hsl(${companion} 70% 50% / 0.20), transparent 58%)`,
        ].join(", "),
        backgroundSize: "14px 14px, auto, auto",
      }}
    >
      {/* Ghost monogram — an editorial letterform, not a photo stand-in. Clipped by the slot. */}
      <span
        className="pointer-events-none absolute -bottom-7 -right-1 select-none text-[6.5rem] font-bold leading-none tracking-tighter"
        style={{ color: `hsl(${base} 62% 52% / 0.18)` }}
      >
        {mark}
      </span>
      {/* The outlet's mark, full colour, on a raised card chip — the same chip vocabulary as the
          coverage plate's publisher row, at plate scale. */}
      <div className="relative grid h-14 w-14 place-items-center rounded-full border bg-card p-2 shadow-soft">
        <PublisherLogo
          logo={article.publisherLogo}
          fallbacks={article.publisherLogoFallbacks}
          sizePx={40}
          className="h-10 w-10 object-contain"
          fallbackNode={
            <span className="text-lg font-bold" style={{ color: `hsl(${base} 60% 45%)` }}>
              {mark}
            </span>
          }
        />
      </div>
    </div>
  );
}
