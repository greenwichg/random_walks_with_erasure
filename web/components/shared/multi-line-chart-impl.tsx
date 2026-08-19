"use client";

import { Line, LineChart, Tooltip, XAxis, YAxis } from "recharts";
import { useMeasure } from "@/hooks/use-measure";
import { useTranslation } from "@/lib/i18n";
import type { BarSeries } from "@/components/shared/stacked-bar";
import { exactFormat, seriesMax, tickCapacity, tickLabels, yAxis } from "@/lib/chart-axis";

/**
 * A themed multi-series line chart for share-over-time data (the Emotional Tone card).
 *
 * Every series gets a common zero baseline, so trends and period-to-period comparisons read
 * directly — the job a 100% stack can't do for its middle segments. The legend reuses the
 * SpectrumBar chip style (identity is never color-alone); the shared tooltip preserves the
 * composition-at-a-point reading the stack used to provide. Dots always render so a reader
 * with a single report snapshot still sees their data point. Same measured-width approach,
 * axis styling and tooltip theming as TrendChart / StackedBar.
 */
export function MultiLineChart({
  data,
  series,
  height = 220,
  percent = false,
  xKey = "date",
}: {
  data: Record<string, number | string>[];
  series: BarSeries[];
  height?: number;
  percent?: boolean;
  xKey?: string;
}) {
  const { formatDate } = useTranslation();
  const { ref, width } = useMeasure<HTMLDivElement>();

  const fmt = (d: unknown) =>
    typeof d === "string" && d.includes("-")
      ? formatDate(d, { month: "short", day: "numeric" })
      : String(d ?? "");
  // Same-day report snapshots share a date. `tickLabels` marks each run of equal dates once, at
  // its midpoint, and thins runs to what the measured width can hold — the de-duplication this
  // chart used to do inline, now shared with TrendChart and StackedBar so all three agree.
  const labels = tickLabels(data.map((row) => row[xKey]), fmt, tickCapacity(width));
  const kind = percent ? "percent" : "count";
  const axis = yAxis(kind, seriesMax(data, series.map((s) => s.key)));
  const exact = exactFormat(kind);

  return (
    <div className="space-y-2.5">
      {/* MB1: min-w-0 + overflow-hidden — see TrendChart. */}
      <div ref={ref} className="w-full min-w-0 overflow-hidden" style={{ height }}>
        <LineChart
          width={width}
          height={height}
          data={data}
          margin={{ top: 8, right: 12, left: 0, bottom: 0 }}
        >
          <XAxis
            dataKey={xKey}
            interval={0}
            tickFormatter={(_: string, i: number) => labels[i] ?? ""}
            tick={{ fontSize: 11, fill: "hsl(var(--muted-foreground))" }}
            tickLine={false}
            axisLine={false}
          />
          <YAxis
            // `["auto","auto"]` used to float the non-percent case off zero, which draws small
            // differences as large ones. Both cases are now zero-anchored with round ticks.
            domain={axis.domain}
            ticks={axis.ticks}
            tick={{ fontSize: 11, fill: "hsl(var(--muted-foreground))" }}
            tickLine={false}
            axisLine={false}
            width={42}
            tickFormatter={axis.format}
          />
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
            formatter={(v: number, name: string) => [exact(v), name]}
            labelFormatter={(d) =>
              typeof d === "string" && d.includes("-")
                ? formatDate(d, { month: "long", day: "numeric" })
                : String(d)
            }
          />
          {series.map((s) => (
            <Line
              key={s.key}
              dataKey={s.key}
              name={s.label}
              stroke={s.color}
              strokeWidth={2}
              dot={{ r: 2.5, fill: s.color, strokeWidth: 0 }}
              activeDot={{ r: 4 }}
              animationDuration={800}
            />
          ))}
        </LineChart>
      </div>
      {/* identity is never color-alone — the SpectrumBar chip pattern, values live in the tooltip */}
      <div className="flex flex-wrap items-center gap-x-4 gap-y-1 text-xs">
        {series.map((s) => (
          <div key={s.key} className="flex items-center gap-1.5">
            <span className="h-2 w-2 rounded-full" style={{ background: s.color }} />
            <span className="text-muted-foreground">{s.label}</span>
          </div>
        ))}
      </div>
    </div>
  );
}
