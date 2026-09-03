"use client";

import * as React from "react";
import Link from "next/link";
import { ChevronDown } from "lucide-react";
import type { StoryCoverage } from "@ih/core/domain/types";
import {
  dominantFactuality,
  factualityAttribution,
  groupOutletsByFactuality,
} from "@ih/core/logic/factuality-distribution";
import type { DistributionSlice } from "@/components/shared/category-distribution";
import { CategoryDistribution } from "@/components/shared/category-distribution";
import { FactualityBadge } from "@/components/shared/factuality-badge";
import { EmptyBreakdown } from "@/components/stories/breakdown/empty-breakdown";
import { useTranslation } from "@/lib/i18n";
import { FACTUALITY_META as META, factualityColor as colorOf } from "@/lib/factuality-meta";
import { cn } from "@/lib/utils";

/** Day precision, as on the badge: a rater's revision cadence is months, and a time of day would
 *  imply a freshness the record does not have. */
const DATE_OPTS: Intl.DateTimeFormatOptions = { year: "numeric", month: "short", day: "numeric" };

/**
 * The FACTUALITY tab: how the outlets on this story are rated for factual reporting — the same
 * chart the Ownership tab draws, over the rater's own six-level scale, plus the one thing neither
 * of the other two tabs needs: an ATTRIBUTION line, and a full breakdown behind it that names
 * every outlet, its level, who rated it and when.
 *
 * The attribution is not decoration and not a tooltip. These verdicts are a third party's claim
 * about named news organisations; shown bare they read as ours, and read a year from now they
 * assert the rater still says so. So the credit sits under the chart in the same breath as the
 * percentages, and the full breakdown carries it per outlet with a link to the rater's own page —
 * the same `FactualityBadge` the publisher profile uses, so the two can never word it differently.
 *
 * Three absences, three different sentences, because they are three different facts:
 *   * the deployment doesn't publish ratings at all (`RWE_PUBLIC_FACTUALITY` off — the default);
 *   * it does, and none of the outlets on this story is rated;
 *   * it does, some are, and the unrated remainder is a counted, muted slice of the chart.
 * Collapsing any two of those into one message would state something false about the third.
 *
 * Members only (M4) — attached Tier B rows never voted and do not stand anywhere.
 */
export function FactualityBreakdown({
  coverage,
  published,
}: {
  coverage: StoryCoverage[];
  /** `Story.factualityPublished` — whether this DEPLOYMENT publishes verdicts, which is a
   *  different question from whether these outlets carry any. */
  published?: boolean;
}) {
  const { t, formatDate } = useTranslation();
  const [showAll, setShowAll] = React.useState(false);
  const groups = React.useMemo(() => groupOutletsByFactuality(coverage), [coverage]);
  const dominant = dominantFactuality(groups);
  const credit = factualityAttribution(groups);

  if (!published) return <EmptyBreakdown>{t("story.factuality.unpublished")}</EmptyBreakdown>;
  if (groups.ratedCount === 0 || !dominant) {
    return <EmptyBreakdown>{t("story.factuality.none")}</EmptyBreakdown>;
  }

  const slices: DistributionSlice[] = groups.slices.map((s) => ({
    key: s.level,
    label: t(META[s.level].labelKey),
    color: colorOf(s.level),
    outlets: s.outlets,
    muted: s.level === "unrated",
  }));

  // The full list walks the slices in ramp order, so it reads best-to-worst and the unrated
  // remainder lands last — the same order as the legend directly above it.
  const rated = groups.slices.flatMap((s) => s.outlets.filter((o) => o.rating));

  return (
    <div>
      <p className="mb-2.5 flex items-center gap-1.5 text-xs font-medium text-muted-foreground">
        <span
          aria-hidden
          className="h-1.5 w-1.5 shrink-0 rounded-full"
          style={{ background: colorOf(dominant.level) }}
        />
        {t("story.factualitySummary", {
          pct: dominant.pct,
          level: t(META[dominant.level].labelKey),
        })}
      </p>

      <CategoryDistribution slices={slices} defaultKey={dominant.level} />

      {credit && (
        <p className="mt-3 text-[11px] leading-relaxed text-muted-foreground">
          {t("story.factuality.attribution", {
            sources: credit.sources
              .map((s) => t(`publishers.factuality.source.${s}`))
              .join(", "),
            date: formatDate(credit.asOf, DATE_OPTS),
          })}
        </p>
      )}

      <button
        type="button"
        onClick={() => setShowAll((v) => !v)}
        aria-expanded={showAll}
        className={cn(
          "mt-3 flex w-full items-center justify-center gap-1.5 rounded-md border px-3 py-2",
          "text-xs font-semibold transition-colors hover:bg-accent",
          "focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring focus-visible:ring-offset-2",
        )}
      >
        {t("story.factuality.seeFull")}
        <ChevronDown
          className={cn("h-3.5 w-3.5 transition-transform", showAll && "rotate-180")}
          aria-hidden
        />
      </button>

      {showAll && (
        <ul className="mt-2 divide-y rounded-md border">
          {rated.map((outlet) => (
            /* Two lines, not one row: the badge carries its own attribution ("Media Bias/Fact
               Check · Aug 10, 2026"), which in a 4-column rail cannot share a line with an outlet
               name without both wrapping into each other. */
            <li key={outlet.publisher} className="px-3 py-2">
              <Link
                href={`/publishers/${encodeURIComponent(outlet.publisher)}`}
                className="block truncate text-xs font-medium hover:text-primary hover:underline"
              >
                {outlet.publisher}
              </Link>
              <div className="mt-1">
                <FactualityBadge factuality={outlet.rating} />
              </div>
            </li>
          ))}
        </ul>
      )}
    </div>
  );
}
