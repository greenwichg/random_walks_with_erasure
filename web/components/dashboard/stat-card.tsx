"use client";

import { motion } from "framer-motion";
import type { LucideIcon } from "lucide-react";
import { cn } from "@/lib/utils";

/** Compact KPI tile for the dashboard's "Today" strip. */
export function StatCard({
  icon: Icon,
  label,
  value,
  sub,
  hue = "primary",
  index = 0,
}: {
  icon: LucideIcon;
  label: string;
  value: string;
  sub?: string;
  hue?: "primary" | "left" | "center" | "right" | "positive" | "caution";
  index?: number;
}) {
  const bg: Record<string, string> = {
    primary: "bg-primary/10 text-primary",
    left: "bg-lean-left/12 text-lean-left",
    center: "bg-lean-center/12 text-lean-center",
    right: "bg-lean-right/12 text-lean-right",
    positive: "bg-positive/12 text-positive",
    caution: "bg-caution/15 text-caution",
  };
  return (
    <motion.div
      initial={{ opacity: 0, y: 8 }}
      animate={{ opacity: 1, y: 0 }}
      transition={{ delay: index * 0.05, duration: 0.4, ease: [0.16, 1, 0.3, 1] }}
      className="flex items-center gap-3 rounded-lg border bg-card p-4 shadow-soft"
    >
      <span className={cn("grid h-10 w-10 shrink-0 place-items-center rounded-xl", bg[hue])}>
        <Icon className="h-5 w-5" />
      </span>
      <div className="min-w-0">
        <div className="flex items-baseline gap-1.5">
          <span className="text-xl font-semibold tabular-nums tracking-tight">{value}</span>
          {sub && <span className="truncate text-xs text-muted-foreground">{sub}</span>}
        </div>
        <p className="truncate text-xs text-muted-foreground">{label}</p>
      </div>
    </motion.div>
  );
}
