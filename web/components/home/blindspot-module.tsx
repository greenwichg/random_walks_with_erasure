"use client";

import Link from "next/link";
import { EyeOff } from "lucide-react";
import type { Story } from "@/types/domain";
import { SectionHeader } from "@/components/shared/section-header";
import { SpectrumBar } from "@/components/shared/spectrum-bar";
import { LEAN_META } from "@/lib/metrics";
import { useTranslation } from "@/lib/i18n";

/**
 * Blind spots — events being covered mainly from one side, and which side is missing.
 *
 * This is the sharpest expression of the product's thesis on the home page: not "here is the news"
 * but "here is what you are *not* being shown". The flag is the Story Service's own
 * `blindspotSide`, the same signal the story cards carry, so the module can never claim a gap the
 * rest of the page disagrees with.
 *
 * The missing side is encoded twice — a coloured rail and a text label — so the meaning never
 * depends on colour alone.
 */
export function BlindspotModule({ stories }: { stories: Story[] }) {
  const { t, formatCompact } = useTranslation();
  if (stories.length === 0) return null;

  return (
    <section aria-labelledby="blindspots-heading">
      <SectionHeader
        id="blindspots-heading"
        title={t("home.blindspots.title")}
        eyebrow={t("home.blindspots.eyebrow")}
        href="/stories?blindspot=any"
        actionLabel={t("home.viewAll")}
      />
      <ul className="grid gap-3 sm:grid-cols-2">
        {stories.map((story) => {
          const side = story.blindspotSide!;
          const meta = LEAN_META[side];
          return (
            <li key={story.id}>
              <Link
                href={`/stories/${story.id}`}
                className="group flex h-full gap-3 rounded-md border bg-card p-4 transition-colors hover:bg-accent/50 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring focus-visible:ring-offset-2"
              >
                <span
                  aria-hidden
                  className="w-1 shrink-0 rounded-full"
                  style={{ background: meta.color }}
                />
                <div className="min-w-0 flex-1">
                  <p
                    className="mb-1 inline-flex items-center gap-1 text-[0.68rem] font-semibold uppercase tracking-wide"
                    style={{ color: meta.color }}
                  >
                    <EyeOff className="h-3 w-3" aria-hidden />
                    {t("storyCard.thinOn", { side: t(`filter.${side}`).toLowerCase() })}
                  </p>
                  <h3 className="line-clamp-2 text-sm font-semibold leading-snug tracking-tight transition-colors group-hover:text-primary">
                    {story.title}
                  </h3>
                  <div className="mt-2">
                    <SpectrumBar distribution={story.distribution} height={4} showLegend={false} />
                  </div>
                  <p className="mt-1.5 text-[0.68rem] text-muted-foreground">
                    {t("storyCard.sources", { n: formatCompact(story.totalCoverage) })}
                  </p>
                </div>
              </Link>
            </li>
          );
        })}
      </ul>
    </section>
  );
}
