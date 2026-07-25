"use client";

import Link from "next/link";
import type { ViewpointDistribution } from "@/types/domain";
import type { TopicCount } from "@/lib/home";
import { SectionHeader } from "@/components/shared/section-header";
import { SpectrumBar } from "@/components/shared/spectrum-bar";
import { useTranslation } from "@/lib/i18n";
import { cn } from "@/lib/utils";

/**
 * Small editorial modules that continue the companion rail past the Information Health panel.
 *
 * All three read from data the page already holds — the single `/api/stories` payload — so the
 * rail gets longer without a single extra request.
 */

/**
 * Today's aggregate left/centre/right split — ONE question, answered once: "how balanced is
 * today's coverage?". The per-story bars elsewhere answer "how is THIS event covered".
 *
 * The one-sided count deliberately does NOT appear here: the Daily Briefing leads the page with it,
 * and repeating the same figure in the rail dilutes both. (This reverses the Phase-1.3 merge — the
 * merge fixed a widget-stack problem but created a cross-section repetition problem.)
 */
export function CoverageSnapshot({ mix, events }: { mix: ViewpointDistribution; events: number }) {
  const { t, formatCompact } = useTranslation();
  const total = mix.left + mix.center + mix.right;
  if (total <= 0) return null;

  return (
    <section aria-labelledby="coverage-heading" className="rounded-lg border bg-card p-4">
      <SectionHeader id="coverage-heading" title={t("home.coverage.title")} className="mb-3" />
      <SpectrumBar distribution={mix} height={10} />
      <p className="mt-3 text-xs text-muted-foreground">
        {t("home.coverage.body", { n: formatCompact(events) })}
      </p>
    </section>
  );
}

/**
 * The full topic index, as a vertical list with counts. The horizontal rail at the top of the page
 * is a quick filter that truncates; this is the browsable index, and it drives the SAME in-page
 * filter so the two controls can never disagree.
 */
export function TrendingTopicsPanel({
  topics,
  active,
  onSelect,
}: {
  topics: TopicCount[];
  active: string | null;
  onSelect: (topic: string | null) => void;
}) {
  const { t, formatCompact } = useTranslation();
  if (topics.length === 0) return null;

  return (
    <section aria-labelledby="topics-panel-heading" className="rounded-lg border bg-card p-4">
      <SectionHeader id="topics-panel-heading" title={t("home.trending.title")} className="mb-3" />
      <ul className="space-y-0.5">
        {topics.map((entry) => {
          const on = active === entry.topic;
          return (
            <li key={entry.topic}>
              <button
                type="button"
                aria-pressed={on}
                onClick={() => onSelect(on ? null : entry.topic)}
                className={cn(
                  "flex w-full items-baseline justify-between gap-3 rounded-md px-2 py-1.5 text-left text-sm transition-colors",
                  "focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring focus-visible:ring-offset-2",
                  on ? "bg-accent font-medium text-foreground" : "text-muted-foreground hover:bg-accent/60 hover:text-foreground",
                )}
              >
                <span className="truncate">{entry.topic}</span>
                <span className="shrink-0 text-xs tabular-nums opacity-70">
                  {formatCompact(entry.count)}
                </span>
              </button>
            </li>
          );
        })}
      </ul>
      <Link
        href="/stories"
        className="mt-3 inline-flex rounded text-xs font-medium text-primary transition-opacity hover:opacity-80 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring focus-visible:ring-offset-2"
      >
        {t("home.viewAll")}
      </Link>
    </section>
  );
}
