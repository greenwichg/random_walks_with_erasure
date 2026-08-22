"use client";

import type { PublisherCount } from "@ih/core/logic/home";
import { SectionHeader } from "@/components/shared/section-header";
import { useTranslation } from "@/lib/i18n";

/**
 * Who is actually carrying today's coverage — publishers ranked by how many of the loaded events
 * they appear in, with a bar showing relative presence.
 *
 * Counted from the payload, never a curated masthead: the product doesn't claim a newsroom it
 * can't see in the corpus. There is no "Follow" affordance because there is no follow contract in
 * the engine — a button that silently did nothing would be worse than its absence.
 */
export function PublisherSpotlight({
  publishers,
  titleKey = "home.publishers.title",
  countKey = "home.publishers.count",
}: {
  publishers: PublisherCount[];
  /** Override the heading/count copy so the same module serves other scopes (e.g. one story's
   *  publishers, where the count means articles, not events). */
  titleKey?: string;
  countKey?: string;
}) {
  const { t, formatCompact } = useTranslation();
  if (publishers.length === 0) return null;

  const max = publishers[0]?.stories || 1;

  return (
    <section aria-labelledby="publishers-heading" className="rounded-lg border bg-card p-4">
      <SectionHeader id="publishers-heading" title={t(titleKey)} className="mb-3" />
      <ul className="space-y-3">
        {publishers.map((entry) => (
          <li key={entry.publisher}>
            <div className="flex items-baseline justify-between gap-3 text-sm">
              <span className="truncate font-medium">{entry.publisher}</span>
              <span className="shrink-0 text-xs tabular-nums text-muted-foreground">
                {t(countKey, { n: formatCompact(entry.stories) })}
              </span>
            </div>
            {/* Presentational only — the count beside it carries the same value for screen readers. */}
            <div aria-hidden className="mt-1.5 h-1 overflow-hidden rounded-full bg-muted">
              <div
                className="h-full rounded-full bg-foreground/30"
                style={{ width: `${Math.max(4, (entry.stories / max) * 100)}%` }}
              />
            </div>
          </li>
        ))}
      </ul>
    </section>
  );
}
