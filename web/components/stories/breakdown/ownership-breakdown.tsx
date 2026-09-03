"use client";

import * as React from "react";
import type { StoryCoverage } from "@ih/core/domain/types";
import { dominantOwnership, groupOutletsByOwnership } from "@ih/core/logic/ownership-distribution";
import type { DistributionSlice } from "@/components/shared/category-distribution";
import { CategoryDistribution } from "@/components/shared/category-distribution";
import { EmptyBreakdown } from "@/components/stories/breakdown/empty-breakdown";
import { useTranslation } from "@/lib/i18n";
import { OWNERSHIP_META as META, ownershipColor as colorOf } from "@/lib/ownership-meta";

/**
 * The OWNERSHIP tab: who controls the outlets on this story. One summary sentence over the shared
 * distribution chart (segmented bar + two-ring radial + legend) — the same picture the Factuality
 * tab draws, one implementation, two vocabularies.
 *
 * Data honesty: ownership is the registry's sourced `ownership` column; outlets it doesn't
 * classify form the muted `unknown` slice, counted in every percentage (a story that is mostly
 * unclassified must say so) and never folded into "other". Nothing classified -> the tab states
 * that rather than drawing a chart of pure unknown, which is not a visualization but an apology.
 * Members only (M4).
 */
export function OwnershipBreakdown({ coverage }: { coverage: StoryCoverage[] }) {
  const { t } = useTranslation();
  const groups = React.useMemo(() => groupOutletsByOwnership(coverage), [coverage]);
  const dominant = dominantOwnership(groups);

  if (groups.knownCount === 0 || !dominant) {
    return <EmptyBreakdown>{t("story.ownership.none")}</EmptyBreakdown>;
  }

  const slices: DistributionSlice[] = groups.slices.map((s) => ({
    key: s.category,
    label: t(META[s.category].labelKey),
    color: colorOf(s.category),
    outlets: s.outlets,
    muted: s.category === "unknown",
  }));

  return (
    <div>
      <p className="mb-2.5 flex items-center gap-1.5 text-xs font-medium text-muted-foreground">
        <span
          aria-hidden
          className="h-1.5 w-1.5 shrink-0 rounded-full"
          style={{ background: colorOf(dominant.category) }}
        />
        {t("story.ownershipSummary", {
          pct: dominant.pct,
          category: t(META[dominant.category].labelKey),
        })}
      </p>
      <CategoryDistribution slices={slices} defaultKey={dominant.category} />
    </div>
  );
}
