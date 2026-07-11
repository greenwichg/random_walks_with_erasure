"use client";

import { motion } from "framer-motion";
import Link from "next/link";
import { Layers, Newspaper, FileText, ArrowRight } from "lucide-react";
import { StatCard } from "@/components/dashboard/stat-card";
import { LEAN_META } from "@/lib/metrics";
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
      <div className="grid gap-4 sm:grid-cols-2 lg:grid-cols-4">
        <StatCard icon={Layers} label={t("history.ih.topics")} value={`${insights.topicCount}`} hue="primary" index={0} />
        <StatCard icon={Newspaper} label={t("history.ih.sources")} value={`${insights.publisherCount}`} hue="left" index={1} />
        <BalanceTile insights={insights} index={2} />
        <StatCard icon={FileText} label={t("history.ih.reporting")} value={`${pct(insights.reportingShare)}%`} hue="positive" index={3} />
      </div>
    </section>
  );
}

/** Political-balance tile: a compact left/center/right distribution bar (inline styles because
 *  Tailwind's `bg-center` is a background-position utility, not the centre hue). Mirrors StatCard chrome. */
function BalanceTile({ insights, index }: { insights: HistoryInsights; index: number }) {
  const { t } = useTranslation();
  const { leanShare } = insights;
  const seg = ([bucket, share]: ["left" | "center" | "right", number]) =>
    share > 0 ? (
      <span key={bucket} style={{ width: `${share * 100}%`, backgroundColor: LEAN_META[bucket].color }} />
    ) : null;
  const legend: ["left" | "center" | "right", number][] = [
    ["left", leanShare.left],
    ["center", leanShare.center],
    ["right", leanShare.right],
  ];
  return (
    <motion.div
      initial={{ opacity: 0, y: 8 }}
      animate={{ opacity: 1, y: 0 }}
      transition={{ delay: index * 0.05, duration: 0.4, ease: [0.16, 1, 0.3, 1] }}
      className="flex flex-col justify-center gap-2 rounded-lg border bg-card p-4 shadow-soft"
    >
      <p className="text-xs font-medium text-muted-foreground">{t("history.ih.balance")}</p>
      <div className="flex h-2 overflow-hidden rounded-full bg-muted">
        {legend.map(seg)}
      </div>
      <div className="flex items-center gap-x-3 gap-y-0.5 text-[0.7rem] tabular-nums text-muted-foreground">
        {legend.map(([bucket, share]) => (
          <span key={bucket} className="inline-flex items-center gap-1">
            <span className="h-2 w-2 rounded-full" style={{ backgroundColor: LEAN_META[bucket].color }} />
            {Math.round(share * 100)}%
          </span>
        ))}
      </div>
    </motion.div>
  );
}
