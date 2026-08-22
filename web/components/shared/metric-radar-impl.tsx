"use client";

import { PolarAngleAxis, PolarGrid, PolarRadiusAxis, Radar, RadarChart, Tooltip } from "recharts";
import type { Metric } from "@ih/core/domain/types";
import { METRICS, RADAR_METRICS } from "@ih/core/logic/metrics";
import { useMeasure } from "@/hooks/use-measure";

/** Interactive radar of the six scored health metrics. */
export function MetricRadar({ metrics, size = 320 }: { metrics: Metric[]; size?: number }) {
  const { ref, width } = useMeasure<HTMLDivElement>(size);
  // MB1: cap at the measured container width (never `size + 40`, which pushed the SVG past a
  // narrow card's content box and scrolled the report page sideways). On a phone the radar fills
  // the card; on desktop it caps at its natural `size`.
  const dim = Math.min(width, size);

  const data = RADAR_METRICS.map((key) => {
    const m = metrics.find((x) => x.key === key);
    // An unavailable metric (empty-state card) has no real score — plot a gap (null), never a
    // fabricated 0 that would read as a genuinely low score.
    const score = m && m.available !== false ? m.score : null;
    return { metric: METRICS[key].short, score, full: 100 };
  });

  return (
    <div ref={ref} className="flex w-full min-w-0 justify-center overflow-hidden" style={{ height: dim }}>
      <RadarChart width={dim} height={dim} data={data} outerRadius="72%">
        <PolarGrid stroke="hsl(var(--border))" />
        <PolarAngleAxis dataKey="metric" tick={{ fontSize: 12, fill: "hsl(var(--muted-foreground))" }} />
        <PolarRadiusAxis domain={[0, 100]} tick={false} axisLine={false} />
        <Radar
          name="You"
          dataKey="score"
          stroke="hsl(var(--primary))"
          strokeWidth={2}
          fill="hsl(var(--primary))"
          fillOpacity={0.22}
          animationDuration={900}
        />
        <Tooltip
          contentStyle={{
            borderRadius: 12,
            border: "1px solid hsl(var(--border))",
            background: "hsl(var(--popover))",
            color: "hsl(var(--popover-foreground))",
            fontSize: 12,
          }}
          formatter={(v: number) => [`${Math.round(v)} / 100`, "Score"]}
        />
      </RadarChart>
    </div>
  );
}
