"use client";

import Link from "next/link";
import { Compass } from "lucide-react";
import type { Coverage, MetricKey } from "@ih/core/domain/types";
import { METRIC_UNLOCK, coverageStatus } from "@ih/core/logic/coverage";
import { Button } from "@/components/ui/button";
import { useTranslation } from "@/lib/i18n";

/**
 * The empty state a metric card shows when the backend reports the metric is not yet measurable
 * (`metric.available === false`) — NEVER inferred from a real `score === 0`. It replaces only the
 * card body (score, progress bar, benchmark); the card's icon, label, and info tooltip stay visible,
 * so the dashboard layout is preserved and the card never looks broken.
 *
 * Progressive unlocking (every unavailable insight explains itself): the title says the metric is not
 * ready, the description says WHAT action unlocks it (metric-specific), and — for read-gated metrics —
 * a line shows current reads-toward-threshold progress. A primary CTA reuses the Discover navigation.
 */
export function MetricEmptyState({
  href = "/discover",
  showCta = true,
  metricKey,
  coverage,
}: {
  href?: string;
  /** The CTA is omitted where a link cannot nest (e.g. an accordion row that is itself a button). */
  showCta?: boolean;
  /** Selects the metric-specific "what unlocks this" hint; falls back to the generic reads hint. */
  metricKey?: MetricKey;
  /** Reads-toward-threshold progress, shown only for read-gated metrics (honest — reception/political
   *  metrics aren't unlocked by read count alone, so no count is shown for them). */
  coverage?: Coverage | null;
}) {
  const { t } = useTranslation();
  const unlockKey = metricKey ? METRIC_UNLOCK[metricKey] : "unlock.reads";
  const s = coverageStatus(undefined, coverage);
  // Only read-gated metrics are unlocked by the reads threshold — show progress just for those.
  const showProgress = unlockKey === "unlock.reads" && !!coverage && !s.sufficient;

  return (
    <div className="mt-3 flex flex-1 flex-col">
      <p className="text-sm font-medium text-foreground">{t("metric.emptyState.title")}</p>
      <p className="mt-1.5 text-xs leading-relaxed text-muted-foreground">{t(unlockKey)}</p>
      {showProgress && (
        <p className="mt-1.5 text-xs font-medium tabular-nums text-primary">
          {t("coverage.progress", { reads: s.reads, threshold: s.threshold })}
        </p>
      )}
      {showCta && (
        <Button asChild size="sm" variant="outline" className="mt-3 self-start">
          <Link href={href}>
            <Compass className="h-4 w-4" /> {t("metric.emptyState.cta")}
          </Link>
        </Button>
      )}
    </div>
  );
}
