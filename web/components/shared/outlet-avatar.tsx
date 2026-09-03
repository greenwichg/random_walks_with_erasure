"use client";

import type { OutletMark } from "@ih/core/logic/bias-distribution";
import { hostIconCandidates, logoCandidates } from "@ih/core/logic/publisher-logo";
import { monogram } from "@ih/core/logic/placeholder-art";
import { PublisherLogo } from "@/components/shared/publisher-logo";
import { cn } from "@/lib/utils";

/**
 * An outlet's mark on a plate — THE way a third-party logo is presented in the story breakdown.
 *
 * THE PLATE IS ALWAYS WHITE, in both themes, and that is the whole point rather than an oversight.
 * The marks here are other companies' artwork, drawn overwhelmingly for a light ground: favicons
 * and apple-touch icons live on browser tabs and home screens. Rendering them on the house's own
 * `--muted` put black wordmarks on a near-black surface in dark mode — The Hill, ABC and the New
 * York Post were literally invisible circles in the capsules, and no amount of sizing fixes that.
 * A white plate is the one ground on which every publisher's mark reads, which is why the
 * reference uses one too. The ring, not the fill, is what separates the plate from a white card in
 * the light theme.
 *
 * SIZE IS A RESOLUTION CONTRACT, not just a look. `PublisherLogo` demands roughly `sizePx * dpr`
 * real pixels and walks to the next candidate when an image cannot supply them, so asking for a
 * bigger content box does not stretch a small icon — it rejects it and falls through to a better
 * one, ending at the monogram. Growing the box therefore makes marks sharper, not blurrier; the
 * blur it replaces came from a 24px box that accepted almost anything.
 *
 * `logoPx` is deliberately ~2/3 of the plate: the remaining third is the optical padding that
 * keeps a square icon from touching the circle's edge, which is what separates a designed chip
 * from a cropped one.
 */
export function OutletAvatar({
  outlet,
  size,
  className,
}: {
  outlet: Pick<OutletMark, "publisher" | "url" | "logo" | "logoFallbacks">;
  /** Plate diameter in CSS px. The logo box, and so the resolution demanded, follows from it. */
  size: number;
  className?: string;
}) {
  const icons = logoCandidates(outlet.logo, outlet.logoFallbacks ?? hostIconCandidates(outlet.url));
  const logoPx = Math.round(size * 0.66);
  return (
    <span
      className={cn(
        "grid shrink-0 place-items-center overflow-hidden rounded-full bg-white ring-1 ring-black/10",
        className,
      )}
      style={{ width: size, height: size }}
    >
      <PublisherLogo
        logo={icons[0]}
        fallbacks={icons.slice(1)}
        sizePx={logoPx}
        className="object-contain"
        // Inline, because the box is a computed size rather than one of a handful of Tailwind steps.
        glyphClassName="text-black/40"
        fallbackNode={
          <span
            aria-hidden
            className="font-bold leading-none text-black/55"
            style={{ fontSize: Math.round(size * 0.34) }}
          >
            {monogram(outlet.publisher)}
          </span>
        }
      />
    </span>
  );
}
