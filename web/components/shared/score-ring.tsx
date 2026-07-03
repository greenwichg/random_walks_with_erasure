"use client";

import { motion } from "framer-motion";
import { scoreBand } from "@/lib/metrics";
import { cn } from "@/lib/utils";

const HUE: Record<string, string> = {
  positive: "hsl(var(--positive))",
  caution: "hsl(var(--caution))",
  negative: "hsl(var(--negative))",
};

/** Animated circular gauge for a 0–100 score, coloured by health band. */
export function ScoreRing({
  score,
  size = 128,
  strokeWidth = 10,
  label,
  className,
}: {
  score: number;
  size?: number;
  strokeWidth?: number;
  label?: string;
  className?: string;
}) {
  const radius = (size - strokeWidth) / 2;
  const circumference = 2 * Math.PI * radius;
  const clamped = Math.max(0, Math.min(100, score));
  const offset = circumference - (clamped / 100) * circumference;
  const band = scoreBand(clamped);
  const color = HUE[band.hue];

  return (
    <div className={cn("relative inline-grid place-items-center", className)} style={{ width: size, height: size }}>
      <svg width={size} height={size} className="-rotate-90">
        <circle
          cx={size / 2}
          cy={size / 2}
          r={radius}
          fill="none"
          stroke="hsl(var(--muted))"
          strokeWidth={strokeWidth}
        />
        <motion.circle
          cx={size / 2}
          cy={size / 2}
          r={radius}
          fill="none"
          stroke={color}
          strokeWidth={strokeWidth}
          strokeLinecap="round"
          strokeDasharray={circumference}
          initial={{ strokeDashoffset: circumference }}
          animate={{ strokeDashoffset: offset }}
          transition={{ duration: 1.1, ease: [0.16, 1, 0.3, 1] }}
        />
      </svg>
      <div className="absolute inset-0 grid place-items-center">
        <div className="text-center">
          <div className="text-3xl font-semibold tabular-nums tracking-tight">{Math.round(clamped)}</div>
          {label && <div className="text-[0.7rem] font-medium text-muted-foreground">{label}</div>}
        </div>
      </div>
    </div>
  );
}
