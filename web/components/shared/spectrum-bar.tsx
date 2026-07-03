"use client";

import { motion } from "framer-motion";
import type { ViewpointDistribution } from "@/types/domain";
import { cn } from "@/lib/utils";

/**
 * A left / center / right segmented bar with legend — the political-distribution
 * primitive. Reused on the report and on story pages.
 */
export function SpectrumBar({
  distribution,
  height = 12,
  showLegend = true,
  className,
}: {
  distribution: ViewpointDistribution;
  height?: number;
  showLegend?: boolean;
  className?: string;
}) {
  const segments = [
    { key: "left", label: "Left", value: distribution.left, color: "hsl(var(--left))" },
    { key: "center", label: "Center", value: distribution.center, color: "hsl(var(--center))" },
    { key: "right", label: "Right", value: distribution.right, color: "hsl(var(--right))" },
  ];
  const total = segments.reduce((a, s) => a + s.value, 0) || 1;

  return (
    <div className={cn("space-y-2.5", className)}>
      <div className="flex overflow-hidden rounded-full bg-muted" style={{ height }}>
        {segments.map((s, i) => (
          <motion.div
            key={s.key}
            initial={{ width: 0 }}
            animate={{ width: `${(s.value / total) * 100}%` }}
            transition={{ delay: i * 0.08, duration: 0.7, ease: [0.16, 1, 0.3, 1] }}
            style={{ background: s.color }}
            title={`${s.label} ${Math.round((s.value / total) * 100)}%`}
          />
        ))}
      </div>
      {showLegend && (
        <div className="flex items-center justify-between text-xs">
          {segments.map((s) => (
            <div key={s.key} className="flex items-center gap-1.5">
              <span className="h-2 w-2 rounded-full" style={{ background: s.color }} />
              <span className="text-muted-foreground">{s.label}</span>
              <span className="font-medium tabular-nums">{Math.round((s.value / total) * 100)}%</span>
            </div>
          ))}
        </div>
      )}
    </div>
  );
}
