"use client";

import { motion } from "framer-motion";
import { cn } from "@/lib/utils";
import { ChartEmpty } from "./states";

export interface BarItem {
  label: string;
  value: number; // 0–1 share
  count?: number;
  color?: string;
  sublabel?: string;
}

/** A ranked horizontal bar list — used for topic + source distributions. */
export function BarList({ items, className }: { items: BarItem[]; className?: string }) {
  // An empty list rendered as an empty <div>: the card kept its title and tooltip above a blank
  // space, which reads as a card that failed rather than one with nothing to rank yet. No height
  // is reserved — unlike a chart this has no intrinsic size to hold open.
  if (!items.length) return <ChartEmpty className={className} />;
  const max = Math.max(...items.map((i) => i.value), 0.0001);
  return (
    <div className={cn("space-y-3", className)}>
      {items.map((item, i) => (
        <div key={item.label} className="group">
          <div className="mb-1 flex items-center justify-between gap-3 text-sm">
            <span className="flex min-w-0 items-center gap-2">
              <span className="truncate font-medium">{item.label}</span>
              {item.sublabel && <span className="shrink-0 text-xs text-muted-foreground">{item.sublabel}</span>}
            </span>
            <span className="shrink-0 tabular-nums text-muted-foreground">
              {Math.round(item.value * 100)}%
              {typeof item.count === "number" && <span className="ml-1 opacity-60">· {item.count}</span>}
            </span>
          </div>
          <div className="h-2 overflow-hidden rounded-full bg-muted">
            <motion.div
              className="h-full rounded-full"
              style={{ background: item.color ?? "hsl(var(--primary))" }}
              initial={{ width: 0 }}
              animate={{ width: `${(item.value / max) * 100}%` }}
              transition={{ delay: i * 0.05, duration: 0.7, ease: [0.16, 1, 0.3, 1] }}
            />
          </div>
        </div>
      ))}
    </div>
  );
}
