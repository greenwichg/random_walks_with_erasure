"use client";

import { Area, AreaChart, Tooltip, XAxis, YAxis } from "recharts";
import type { TrendPoint } from "@/types/domain";
import { useMeasure } from "@/hooks/use-measure";

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
  domain,
}: {
  data: TrendPoint[];
  dataKey?: string;
  height?: number;
  color?: string;
  showAxis?: boolean;
  domain?: [number, number];
}) {
  const { ref, width } = useMeasure<HTMLDivElement>();
  const gradientId = `grad-${dataKey}`;

  return (
    <div ref={ref} className="w-full" style={{ height }}>
      <AreaChart
        width={width}
        height={height}
        data={data}
        margin={{ top: 8, right: 8, left: showAxis ? -16 : 0, bottom: 0 }}
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
            tickFormatter={(d: string) => new Date(d).toLocaleDateString("en", { month: "short", day: "numeric" })}
            tick={{ fontSize: 11, fill: "hsl(var(--muted-foreground))" }}
            tickLine={false}
            axisLine={false}
            minTickGap={40}
          />
        )}
        {showAxis && (
          <YAxis
            domain={domain ?? ["dataMin - 6", "dataMax + 6"]}
            tick={{ fontSize: 11, fill: "hsl(var(--muted-foreground))" }}
            tickLine={false}
            axisLine={false}
            width={36}
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
          labelFormatter={(d) => new Date(d as string).toLocaleDateString("en", { month: "long", day: "numeric" })}
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
