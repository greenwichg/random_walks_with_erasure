"use client";

import { EyeOff } from "lucide-react";
import type { LeanBucket, StoryCoverage, ViewpointDistribution } from "@ih/core/domain/types";
import { SectionHeader } from "@/components/shared/section-header";
import { SpectrumBar } from "@/components/shared/spectrum-bar";
import { LEAN_META } from "@ih/core/logic/metrics";
import { useTranslation } from "@/lib/i18n";

const BUCKETS: LeanBucket[] = ["left", "center", "right"];

/**
 * The story's coverage breakdown, as the rail's insight module: the L/C/R spectrum with its
 * labelled legend, an explicit callout for every side with ZERO articles (the story-level blind
 * spot, stated as a fact — "no left coverage yet" — rather than only implied by an empty bar
 * segment), and the reporting-vs-opinion split when the rows carry registers.
 *
 * Everything is counted from the story's own coverage rows / distribution; the reporting split and
 * the missing-side callouts are facts the old page computed nowhere.
 */
export function StoryCoveragePanel({
  distribution,
  coverage,
}: {
  distribution: ViewpointDistribution;
  coverage: StoryCoverage[];
}) {
  const { t, formatCompact } = useTranslation();
  const total = distribution.left + distribution.center + distribution.right;

  const missing = BUCKETS.filter((b) => (distribution[b] ?? 0) === 0);
  let reporting = 0;
  let opinion = 0;
  for (const row of coverage) {
    if (row.register === "reporting") reporting += 1;
    else if (row.register === "opinion" || row.register === "mixed") opinion += 1;
  }

  if (total <= 0 && coverage.length === 0) return null;

  return (
    <section aria-labelledby="story-breakdown-heading" className="rounded-lg border bg-card p-4">
      <SectionHeader id="story-breakdown-heading" title={t("story.breakdown")} className="mb-3" />

      {total > 0 && <SpectrumBar distribution={distribution} height={10} />}

      {missing.length > 0 && total > 0 && (
        <ul className="mt-3 space-y-1.5">
          {missing.map((bucket) => (
            <li
              key={bucket}
              className="inline-flex items-center gap-1.5 text-xs font-medium"
              style={{ color: LEAN_META[bucket].color }}
            >
              <EyeOff className="h-3.5 w-3.5 shrink-0" aria-hidden />
              {t("story.noCoverage", { side: t(`filter.${bucket}`).toLowerCase() })}
            </li>
          ))}
        </ul>
      )}

      {reporting + opinion > 0 && (
        <p className="mt-3 border-t pt-3 text-xs text-muted-foreground">
          {t("story.registerSplit", {
            reporting: formatCompact(reporting),
            opinion: formatCompact(opinion),
          })}
        </p>
      )}
    </section>
  );
}
