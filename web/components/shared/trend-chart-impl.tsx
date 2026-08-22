"use client";

import { Area, AreaChart, Tooltip, XAxis, YAxis } from "recharts";
import type { TrendPoint } from "@ih/core/domain/types";
import { useTranslation } from "@/lib/i18n";
import { useMeasure } from "@/hooks/use-measure";
import { tickCapacity, tickLabels, yAxis } from "@ih/core/logic/chart-axis";

/**
 * A themed area chart for the overall-score trend + analytics.
 *
 * Renders at an explicitly measured width (via useMeasure) instead of Recharts'
 * ResponsiveContainer, which can collapse to 0 inside flex/grid parents and in
 * headless environments. This paints correctly on the first frame and reflows
 * on resize.
 */
export function TrendChart({
  data,
  dataKey = "overall",
  height = 220,
  color = "hsl(var(--primary))",
  showAxis = true,
}: {
  data: TrendPoint[];
  dataKey?: string;
  height?: number;
  color?: string;
  showAxis?: boolean;
}) {
  const { formatDate } = useTranslation();
  const { ref, width } = useMeasure<HTMLDivElement>();
  const gradientId = `grad-${dataKey}`;
  // Every TrendChart in this app plots a 0–100 score (Information Health, its metrics, the score
  // history), so the score axis is the default rather than something each caller must remember —
  // the old default auto-scaled to the data's own extent and drew a 4-point wobble as a mountain.
  const axis = yAxis("score");
  const fmt = (d: unknown) =>
    typeof d === "string" ? formatDate(d, { month: "short", day: "numeric" }) : String(d ?? "");
  // Score points are one per SAVED REPORT, dated by day: several reports in one afternoon share a
  // date. Label each run of equal dates once, at its midpoint, instead of repeating it per point.
  const labels = tickLabels(data.map((d) => d.date), fmt, tickCapacity(width));

  return (
    // MB1: `min-w-0 overflow-hidden` lets this wrapper shrink below the seeded SVG width inside a
    // grid/flex parent (defeats the `min-width:auto` floor) and clips the pre-measure frame, so a
    // chart can never drag the page into horizontal scroll.
    <div ref={ref} className="w-full min-w-0 overflow-hidden" style={{ height }}>
      <AreaChart
        width={width}
        height={height}
        data={data}
        margin={{ top: 8, right: 12, left: showAxis ? 0 : 0, bottom: 0 }}
      >
        <defs>
          <linearGradient id={gradientId} x1="0" y1="0" x2="0" y2="1">
            <stop offset="0%" stopColor={color} stopOpacity={0.28} />
            <stop offset="100%" stopColor={color} stopOpacity={0} />
          </linearGradient>
        </defs>
        {showAxis && (
          <XAxis
            dataKey="date"
            interval={0}
            tickFormatter={(_: string, i: number) => labels[i] ?? ""}
            tick={{ fontSize: 11, fill: "hsl(var(--muted-foreground))" }}
            tickLine={false}
            axisLine={false}
          />
        )}
        {showAxis && (
          <YAxis
            domain={axis.domain}
            ticks={axis.ticks}
            tickFormatter={axis.format}
            tick={{ fontSize: 11, fill: "hsl(var(--muted-foreground))" }}
            tickLine={false}
            axisLine={false}
            width={40}
          />
        )}
        <Tooltip
          cursor={{ stroke: "hsl(var(--border))", strokeWidth: 1 }}
          contentStyle={{
            borderRadius: 12,
            border: "1px solid hsl(var(--border))",
            background: "hsl(var(--popover))",
            color: "hsl(var(--popover-foreground))",
            boxShadow: "0 8px 24px -12px rgb(0 0 0 / 0.2)",
            fontSize: 12,
          }}
          labelFormatter={(d) => formatDate(d as string, { month: "long", day: "numeric" })}
        />
        <Area
          type="monotone"
          dataKey={dataKey}
          stroke={color}
          strokeWidth={2.5}
          fill={`url(#${gradientId})`}
          animationDuration={900}
        />
      </AreaChart>
    </div>
  );
}
