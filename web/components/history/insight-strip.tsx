"use client";

import type { ReactNode } from "react";
import { motion } from "framer-motion";
import Link from "next/link";
import { Layers, Newspaper, FileText, ArrowRight } from "lucide-react";
import { StatCard } from "@/components/dashboard/stat-card";
import { DistributionBar } from "@/components/history/distribution-bar";
import { Skeleton } from "@/components/ui/skeleton";
import { LEAN_META } from "@/lib/metrics";
import { useReport } from "@/hooks/use-data";
import { useTranslation } from "@/lib/i18n";
import type { HistoryInsights } from "@/lib/history-insights";

/**
 * The Information Health strip (Phase 1) — four descriptive tiles over the reads currently in view
 * (Topic / Publisher diversity, Political balance, Reporting ratio). These are honest counts and
 * shares of *what's shown*, NOT the engine's scored metrics — one "View Information Health Report"
 * action bridges to those. Reacts to the page's filters (the caller passes the filtered summary).
 */
export function InsightStrip({ insights }: { insights: HistoryInsights }) {
  const { t } = useTranslation();
  const pct = (x: number) => Math.round(x * 100);
  return (
    <section aria-label={t("history.overviewEyebrow")}>
      <div className="mb-3 flex items-center justify-between">
        <h3 className="text-sm font-semibold text-muted-foreground">{t("history.overviewEyebrow")}</h3>
        <Link
          href="/report"
          className="inline-flex items-center gap-1 text-xs font-medium text-primary hover:underline"
        >
          {t("history.viewReport")} <ArrowRight className="h-3.5 w-3.5" />
        </Link>
      </div>
      <div className="grid grid-cols-1 gap-4 sm:grid-cols-2 lg:grid-cols-4">
        <StatCard icon={Layers} label={t("history.ih.topics")} value={`${insights.topicCount}`} hue="primary" index={0} />
        <StatCard icon={Newspaper} label={t("history.ih.sources")} value={`${insights.publisherCount}`} hue="left" index={1} />
        <BalanceTile index={2} />
        <StatCard icon={FileText} label={t("history.ih.reporting")} value={`${pct(insights.reportingShare)}%`} hue="positive" index={3} />
      </div>
    </section>
  );
}

/**
 * Political-balance tile: renders the CANONICAL Lean Distribution — the measured report's own
 * `viewpoint` (`report.viewpoint`), the exact numbers the Information Health Report shows — never a
 * separate frontend recomputation. While the report loads, a skeleton keeps the tile's place; below
 * the measured threshold (an estimate, whose viewpoint describes onboarding outlets, not reads) or
 * when the report is unavailable, the tile hides rather than falling back to a divergent computation.
 */
function BalanceTile({ index }: { index: number }) {
  const { t } = useTranslation();
  const { data: report, isLoading } = useReport();

  if (isLoading) {
    return (
      <TileShell index={index}>
        <Skeleton className="h-2 w-full rounded-full" />
      </TileShell>
    );
  }
  // Only a measured report's viewpoint is a truthful "your reading" distribution; anything else hides.
  if (!report || report.mode !== "measured") return null;

  const vp = report.viewpoint;
  const segments = (["left", "center", "right"] as const).map((k) => ({
    key: k,
    label: t(`filter.${k}`),
    value: vp[k],
    color: LEAN_META[k].color,
  }));
  return (
    <TileShell index={index}>
      <DistributionBar segments={segments} showLabels={false} />
    </TileShell>
  );
}

/** The tile chrome shared by the loading and loaded states — preserves the label, layout, and the
 *  StatCard-matching entrance animation. */
function TileShell({ index, children }: { index: number; children: ReactNode }) {
  const { t } = useTranslation();
  return (
    <motion.div
      initial={{ opacity: 0, y: 8 }}
      animate={{ opacity: 1, y: 0 }}
      transition={{ delay: index * 0.05, duration: 0.4, ease: [0.16, 1, 0.3, 1] }}
      className="flex flex-col justify-center gap-2 rounded-lg border bg-card p-4 shadow-soft"
    >
      <p className="text-xs font-medium text-muted-foreground">{t("history.ih.balance")}</p>
      {children}
    </motion.div>
  );
}
