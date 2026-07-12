"use client";

import { Bar, BarChart, Tooltip, XAxis, YAxis } from "recharts";
import { useMeasure } from "@/hooks/use-measure";
import { useTranslation } from "@/lib/i18n";

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

  return (
    <div className={showLegend ? "space-y-2.5" : undefined}>
    <div ref={ref} className="w-full" style={{ height }}>
      <BarChart width={width} height={height} data={data} margin={{ top: 8, right: 12, left: 0, bottom: 0 }} barCategoryGap={stacked ? "18%" : "26%"}>
        <XAxis
          dataKey={xKey}
          tickFormatter={(d: string) =>
            typeof d === "string" && d.includes("-")
              ? formatDate(d, { month: "short", day: "numeric" })
              : d
          }
          tick={{ fontSize: 11, fill: "hsl(var(--muted-foreground))" }}
          tickLine={false}
          axisLine={false}
          minTickGap={24}
        />
        <YAxis
          tick={{ fontSize: 11, fill: "hsl(var(--muted-foreground))" }}
          tickLine={false}
          axisLine={false}
          width={42}
          tickFormatter={(v: number) => (percent ? `${Math.round(v * 100)}%` : `${v}`)}
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
          formatter={(v: number, name: string) => [percent ? `${Math.round(v * 100)}%` : v, name]}
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
