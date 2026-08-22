"use client";

import { Bar, BarChart, Tooltip, XAxis, YAxis } from "recharts";
import { useMeasure } from "@/hooks/use-measure";
import { useTranslation } from "@/lib/i18n";
import { exactFormat, seriesMax, tickCapacity, tickLabels, yAxis } from "@ih/core/logic/chart-axis";

export interface BarSeries {
  key: string;
  label: string;
  color: string;
}

/** A measured stacked/grouped bar chart (reporting, acceptance…). */
export function StackedBar({
  data,
  series,
  height = 220,
  stacked = true,
  percent = false,
  xKey = "date",
  showLegend = false,
}: {
  data: Record<string, number | string>[];
  series: BarSeries[];
  height?: number;
  stacked?: boolean;
  percent?: boolean;
  xKey?: string;
  /** SpectrumBar-style chip legend (identity never color-alone); off by default so existing
   * cards render byte-identically. */
  showLegend?: boolean;
}) {
  const { formatDate } = useTranslation();
  const { ref, width } = useMeasure<HTMLDivElement>();
  // A percentage chart is fixed to 0–100%; a count chart is zero-anchored (a bar read against a
  // non-zero baseline misstates every ratio it draws) and topped at a round multiple of a round
  // step, rather than at whatever the tallest bar happens to be.
  const kind = percent ? "percent" : "count";
  const axis = percent
    ? yAxis("percent")
    : yAxis("count", seriesMax(data, series.map((s) => s.key), stacked));
  const exact = exactFormat(kind);
  const fmt = (d: unknown) =>
    typeof d === "string" && d.includes("-")
      ? formatDate(d, { month: "short", day: "numeric" })
      : String(d ?? "");
  const labels = tickLabels(data.map((row) => row[xKey]), fmt, tickCapacity(width));

  return (
    <div className={showLegend ? "space-y-2.5" : undefined}>
    {/* MB1: min-w-0 + overflow-hidden — see TrendChart. */}
    <div ref={ref} className="w-full min-w-0 overflow-hidden" style={{ height }}>
      <BarChart width={width} height={height} data={data} margin={{ top: 8, right: 12, left: 0, bottom: 0 }} barCategoryGap={stacked ? "18%" : "26%"}>
        <XAxis
          dataKey={xKey}
          interval={0}
          tickFormatter={(_: string, i: number) => labels[i] ?? ""}
          tick={{ fontSize: 11, fill: "hsl(var(--muted-foreground))" }}
          tickLine={false}
          axisLine={false}
        />
        <YAxis
          domain={axis.domain}
          ticks={axis.ticks}
          tick={{ fontSize: 11, fill: "hsl(var(--muted-foreground))" }}
          tickLine={false}
          axisLine={false}
          width={42}
          tickFormatter={axis.format}
        />
        <Tooltip
          cursor={{ fill: "hsl(var(--muted) / 0.5)" }}
          contentStyle={{
            borderRadius: 12,
            border: "1px solid hsl(var(--border))",
            background: "hsl(var(--popover))",
            color: "hsl(var(--popover-foreground))",
            fontSize: 12,
          }}
          formatter={(v: number, name: string) => [exact(v), name]}
          labelFormatter={(d) =>
            typeof d === "string" && d.includes("-")
              ? formatDate(d, { month: "long", day: "numeric" })
              : d
          }
        />
        {series.map((s, i) => (
          <Bar
            key={s.key}
            dataKey={s.key}
            name={s.label}
            stackId={stacked ? "a" : undefined}
            fill={s.color}
            radius={stacked ? (i === series.length - 1 ? [4, 4, 0, 0] : [0, 0, 0, 0]) : [4, 4, 0, 0]}
            animationDuration={800}
          />
        ))}
      </BarChart>
    </div>
    {showLegend && (
      <div className="flex flex-wrap items-center gap-x-4 gap-y-1 text-xs">
        {series.map((s) => (
          <div key={s.key} className="flex items-center gap-1.5">
            <span className="h-2 w-2 rounded-full" style={{ background: s.color }} />
            <span className="text-muted-foreground">{s.label}</span>
          </div>
        ))}
      </div>
    )}
    </div>
  );
}
