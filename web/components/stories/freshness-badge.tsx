"use client";

import { Flame, TrendingUp, Radio, Snowflake, Archive, type LucideIcon } from "lucide-react";
import type { FreshnessBand } from "@/types/domain";
import { useTranslation } from "@/lib/i18n";
import { cn } from "@/lib/utils";

/**
 * A compact freshness badge for a Story — the visible half of Story Intelligence's freshness band.
 * Color + icon encode the band (Breaking → Archived); an optional score shows the 0–100 freshness.
 * Semantic colors only (not the app accent), so an at-a-glance state read never fights the brand.
 */
const BAND_META: Record<FreshnessBand, { icon: LucideIcon; className: string }> = {
  Breaking: {
    icon: Flame,
    className: "bg-red-500/12 text-red-600 dark:text-red-400 ring-1 ring-red-500/20",
  },
  Developing: {
    icon: TrendingUp,
    className: "bg-amber-500/12 text-amber-600 dark:text-amber-400 ring-1 ring-amber-500/20",
  },
  Active: {
    icon: Radio,
    className: "bg-emerald-500/12 text-emerald-600 dark:text-emerald-400 ring-1 ring-emerald-500/20",
  },
  Cooling: {
    icon: Snowflake,
    className: "bg-sky-500/12 text-sky-600 dark:text-sky-400 ring-1 ring-sky-500/20",
  },
  Archived: {
    icon: Archive,
    className: "bg-muted text-muted-foreground ring-1 ring-border",
  },
};

export function FreshnessBadge({
  band,
  score,
  showScore = false,
  className,
}: {
  band: FreshnessBand;
  score?: number;
  showScore?: boolean;
  className?: string;
}) {
  const { t } = useTranslation();
  const meta = BAND_META[band];
  if (!meta) return null;
  const Icon = meta.icon;
  const bandLabel = t(`freshness.${band}`);
  return (
    <span
      className={cn(
        "inline-flex items-center gap-1 rounded-full px-2 py-0.5 text-xs font-medium",
        meta.className,
        className,
      )}
      title={typeof score === "number" ? t("freshness.title", { band: bandLabel, score }) : bandLabel}
    >
      <Icon className="h-3.5 w-3.5" aria-hidden />
      {bandLabel}
      {showScore && typeof score === "number" && (
        <span className="tabular-nums opacity-70">· {score}</span>
      )}
    </span>
  );
}
