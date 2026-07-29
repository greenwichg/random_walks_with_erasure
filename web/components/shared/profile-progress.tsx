"use client";

import Link from "next/link";
import { Sparkles, CircleCheck, Compass } from "lucide-react";
import type { Coverage, ReportMode } from "@/types/domain";
import { coverageStatus } from "@/lib/coverage";
import { Button } from "@/components/ui/button";
import { cn } from "@/lib/utils";
import { useTranslation } from "@/lib/i18n";

/**
 * The Estimate → Measured status + coverage progress, shared by the Dashboard, Health Report, and
 * Analytics so the onboarding context never disappears and the terminology is identical everywhere.
 *
 * - **Estimate / still building** → a bordered card: an "Estimate" badge, "Building your Information
 *   Health profile", a reads-toward-threshold progress bar, the remaining count, and a CTA to read.
 * - **Measured** → a compact one-line chip ("Measured · based on N reads · Confidence X%") so the
 *   context stays visible without taking space.
 *
 * Presentation only — it reads the backend's `mode`/`coverage`/`confidence` and never recomputes a
 * score. Fully responsive: the estimate card stacks its CTA below the text on narrow screens.
 */
export function ProfileProgress({
  mode,
  coverage,
  confidence,
  sample = false,
  cta = true,
  className,
}: {
  mode?: ReportMode | null;
  coverage?: Coverage | null;
  /** Axis confidence 0–1 (measured-only); shown in the Measured chip when present. */
  confidence?: number | null;
  /** True when the report belongs to the exhibit account rather than this reader — see
   *  `HealthReport.sample`. Checked BEFORE the measured branch, because the whole point is that
   *  such a report is `mode: "measured"` and would otherwise render as this reader's measurement. */
  sample?: boolean;
  cta?: boolean;
  className?: string;
}) {
  const { t } = useTranslation();
  const s = coverageStatus(mode, coverage);

  if (sample) {
    // An example profile. It says so, and it never quotes a read count — the count belongs to
    // somebody else, and repeating it is exactly how "Measured · based on 30 reads" reached a
    // reader who had read nothing.
    return (
      <div
        className={cn(
          "inline-flex flex-wrap items-center gap-x-2 gap-y-1 rounded-full border border-muted-foreground/30 bg-muted/40 px-3 py-1.5 text-xs",
          className,
        )}
        role="status"
      >
        <span className="inline-flex items-center gap-1.5 font-medium text-muted-foreground">
          <Compass className="h-3.5 w-3.5" aria-hidden /> {t("coverage.sample.badge")}
        </span>
        <span className="text-muted-foreground">{t("coverage.sample.note")}</span>
      </div>
    );
  }

  if (!s.isEstimate) {
    // Measured — keep the context visible, compactly.
    const hasConfidence = typeof confidence === "number" && Number.isFinite(confidence);
    return (
      <div
        className={cn(
          "inline-flex flex-wrap items-center gap-x-2 gap-y-1 rounded-full border border-positive/30 bg-positive/[0.06] px-3 py-1.5 text-xs",
          className,
        )}
      >
        <span className="inline-flex items-center gap-1.5 font-medium text-positive">
          <CircleCheck className="h-3.5 w-3.5" aria-hidden /> {t("coverage.measured.badge")}
        </span>
        <span className="text-muted-foreground">{t("coverage.measured.basedOn", { reads: s.reads })}</span>
        {hasConfidence && (
          <span className="text-muted-foreground">
            · {t("coverage.confidence", { pct: Math.round((confidence as number) * 100) })}
          </span>
        )}
      </div>
    );
  }

  // Estimate — an explicit "building your profile" card with progress + next action.
  return (
    <div
      className={cn(
        "flex flex-col gap-3 rounded-xl border border-primary/25 bg-primary/[0.04] p-4 sm:flex-row sm:items-center sm:justify-between",
        className,
      )}
      role="status"
    >
      <div className="min-w-0 flex-1">
        <div className="flex items-center gap-2">
          <span className="inline-flex items-center gap-1.5 rounded-full border border-primary/30 bg-primary/10 px-2 py-0.5 text-[11px] font-semibold text-primary">
            <Sparkles className="h-3 w-3" aria-hidden /> {t("coverage.estimate.badge")}
          </span>
          <span className="text-sm font-semibold tracking-tight">{t("coverage.building.title")}</span>
        </div>
        <div className="mt-2.5 flex items-center gap-3">
          <div
            className="h-1.5 flex-1 overflow-hidden rounded-full bg-muted"
            role="progressbar"
            aria-valuemin={0}
            aria-valuemax={s.threshold}
            aria-valuenow={s.reads}
            aria-label={t("coverage.progress", { reads: s.reads, threshold: s.threshold })}
          >
            <div className="h-full rounded-full bg-primary transition-[width] duration-700" style={{ width: `${s.pct}%` }} />
          </div>
          <span className="shrink-0 text-xs font-medium tabular-nums text-muted-foreground">
            {t("coverage.progress", { reads: s.reads, threshold: s.threshold })}
          </span>
        </div>
        <p className="mt-2 text-xs text-muted-foreground">
          {/* In Estimate mode `remaining` is always > 0 — reaching the threshold makes the report Measured. */}
          {t("coverage.remaining", { n: s.remaining })}
        </p>
      </div>
      {cta && (
        <Button asChild size="sm" className="shrink-0 self-start sm:self-center">
          <Link href="/discover">
            <Compass className="h-4 w-4" /> {t("metric.emptyState.cta")}
          </Link>
        </Button>
      )}
    </div>
  );
}
