"use client";

import Link from "next/link";
import { Compass } from "lucide-react";
import { Button } from "@/components/ui/button";
import { useTranslation } from "@/lib/i18n";

/**
 * The empty state a metric card shows when the backend reports the metric is not yet measurable
 * (`metric.available === false`) — NEVER inferred from a real `score === 0`. It replaces only the
 * card body (the score, progress bar, and benchmark); the card's icon, label, and info tooltip stay
 * visible, so the dashboard layout is preserved and the card never looks broken or errored. The copy
 * reads as "waiting for more activity", and a single primary CTA ("Explore Articles") reuses the
 * existing Discover navigation so the reader can generate the activity that unlocks the metric.
 *
 * Accessibility: plain semantic text (no injected heading level — the card's metric label remains the
 * card's heading), fully screen-reader readable, and the CTA is a real keyboard-focusable link.
 */
export function MetricEmptyState({
  href = "/discover",
  showCta = true,
}: {
  href?: string;
  /** The CTA is omitted where a link cannot nest (e.g. an accordion row that is itself a button). */
  showCta?: boolean;
}) {
  const { t } = useTranslation();
  return (
    <div className="mt-3 flex flex-1 flex-col">
      <p className="text-sm font-medium text-foreground">{t("metric.emptyState.title")}</p>
      <p className="mt-1.5 text-xs leading-relaxed text-muted-foreground">
        {t("metric.emptyState.description")}
      </p>
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
