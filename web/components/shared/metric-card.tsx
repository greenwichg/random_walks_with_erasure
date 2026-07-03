"use client";

import { motion } from "framer-motion";
import Link from "next/link";
import type { Metric } from "@/types/domain";
import { METRICS } from "@/lib/metrics";
import { DeltaBadge } from "@/components/shared/delta-badge";
import { InfoTooltip } from "@/components/shared/info-tooltip";
import { cn } from "@/lib/utils";

const HUE_BG: Record<string, string> = {
  primary: "bg-primary/10 text-primary",
  left: "bg-left/12 text-left",
  center: "bg-center/12 text-center",
  right: "bg-right/12 text-right",
  positive: "bg-positive/12 text-positive",
  caution: "bg-caution/15 text-caution",
};
const HUE_BAR: Record<string, string> = {
  primary: "bg-primary",
  left: "bg-left",
  center: "bg-center",
  right: "bg-right",
  positive: "bg-positive",
  caution: "bg-caution",
};

/** The reusable metric tile: icon, tooltip, score, trend, mini bar. */
export function MetricCard({ metric, href, index = 0 }: { metric: Metric; href?: string; index?: number }) {
  const meta = METRICS[metric.key];
  const Icon = meta.icon;

  const body = (
    <motion.div
      initial={{ opacity: 0, y: 8 }}
      animate={{ opacity: 1, y: 0 }}
      transition={{ delay: index * 0.04, duration: 0.4, ease: [0.16, 1, 0.3, 1] }}
      whileHover={href ? { y: -2 } : undefined}
      className={cn(
        "group flex h-full flex-col rounded-lg border bg-card p-4 shadow-soft transition-shadow",
        href && "cursor-pointer hover:shadow-card",
      )}
    >
      <div className="flex items-center gap-2">
        <span className={cn("grid h-8 w-8 place-items-center rounded-lg", HUE_BG[meta.hue])}>
          <Icon className="h-[1.05rem] w-[1.05rem]" />
        </span>
        <span className="text-sm font-medium">{meta.label}</span>
        <InfoTooltip text={meta.tooltip} className="ml-auto" />
      </div>

      <div className="mt-3 flex items-baseline gap-2">
        <span className="text-2xl font-semibold tabular-nums tracking-tight">{Math.round(metric.score)}</span>
        <span className="text-xs text-muted-foreground">/ 100</span>
        <DeltaBadge value={metric.delta} className="ml-auto" />
      </div>

      <div className="mt-3 h-1.5 overflow-hidden rounded-full bg-muted">
        <motion.div
          className={cn("h-full rounded-full", HUE_BAR[meta.hue])}
          initial={{ width: 0 }}
          animate={{ width: `${Math.max(0, Math.min(100, metric.score))}%` }}
          transition={{ delay: index * 0.04 + 0.15, duration: 0.8, ease: [0.16, 1, 0.3, 1] }}
        />
      </div>

      {typeof metric.benchmark === "number" && (
        <p className="mt-2 text-xs text-muted-foreground">
          Typical reader: <span className="font-medium text-foreground">{metric.benchmark}</span>
        </p>
      )}
    </motion.div>
  );

  return href ? (
    <Link href={href} className="block h-full">
      {body}
    </Link>
  ) : (
    body
  );
}
