"use client";

import * as React from "react";
import { EyeOff } from "lucide-react";
import type { StoryCoverage, ViewpointDistribution } from "@ih/core/domain/types";
import { BIAS_BUCKETS, groupOutletsByLean } from "@ih/core/logic/bias-distribution";
import { SpectrumBar } from "@/components/shared/spectrum-bar";
import { BiasDistribution } from "@/components/stories/bias-distribution";
import { EmptyBreakdown } from "@/components/stories/breakdown/empty-breakdown";
import { LEAN_META } from "@ih/core/logic/metrics";
import { useTranslation } from "@/lib/i18n";

/**
 * The BIAS tab: the headline share and L/C/R spectrum counted in OUTLETS, each side's outlets as
 * logo marks, the untracked strip (outlets the registry doesn't rate — shown, never counted), an
 * explicit callout for every side with ZERO outlets (the story-level blind spot, stated as a fact
 * — "no left coverage yet" — rather than only implied by an empty capsule), and the
 * reporting-vs-opinion split when the rows carry registers.
 *
 * Everything is counted from the story's own MEMBER coverage rows; the engine's article-share
 * `distribution` remains only as the fallback bar for the drift case where rows carry no lean at
 * all. Callers pass `splitCoverage(...).panel` — attached Tier B rows never voted (M4).
 *
 * The section chrome (heading, card, tab strip) belongs to `StoryBreakdown`, which is why this
 * renders a bare body: the three tabs must sit in one box, not three stacked cards.
 */
export function BiasBreakdown({
  distribution,
  coverage,
}: {
  distribution: ViewpointDistribution;
  coverage: StoryCoverage[];
}) {
  const { t, formatCompact } = useTranslation();
  const groups = React.useMemo(() => groupOutletsByLean(coverage), [coverage]);
  const total = distribution.left + distribution.center + distribution.right;

  // Blind-spot callouts come from the same outlet counts the capsules draw, so the words and the
  // picture can never disagree; the article-share fallback keeps the old rule.
  const missing =
    groups.ratedCount > 0
      ? BIAS_BUCKETS.filter((b) => groups.buckets[b].length === 0)
      : BIAS_BUCKETS.filter((b) => (distribution[b] ?? 0) === 0);

  let reporting = 0;
  let opinion = 0;
  for (const row of coverage) {
    if (row.register === "reporting") reporting += 1;
    else if (row.register === "opinion" || row.register === "mixed") opinion += 1;
  }

  if (total <= 0 && coverage.length === 0) {
    return <EmptyBreakdown>{t("story.bias.none")}</EmptyBreakdown>;
  }

  return (
    <div>
      {groups.ratedCount === 0 && total > 0 && <SpectrumBar distribution={distribution} height={10} />}
      <BiasDistribution groups={groups} />

      {missing.length > 0 && (groups.ratedCount > 0 || total > 0) && (
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
    </div>
  );
}
