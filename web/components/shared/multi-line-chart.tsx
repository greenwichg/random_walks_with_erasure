"use client";

import { Line, LineChart, Tooltip, XAxis, YAxis } from "recharts";
import { useMeasure } from "@/hooks/use-measure";
import { useTranslation } from "@/lib/i18n";
import type { BarSeries } from "@/components/shared/stacked-bar";

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
  // Same-day report snapshots share a date — label only the first of each run so the axis
  // never repeats itself ("Jul 12" x4). Blank ticks keep their slot; interval={0} keeps the
  // tick index aligned with the row index.
  const tickLabels = data.map((row, i) =>
    i > 0 && fmt(row[xKey]) === fmt(data[i - 1]?.[xKey]) ? "" : fmt(row[xKey]),
  );
  // MB1 Phase 4: on a narrow (phone) width, thin the axis to ~6 labels so daily points don't
  // collide into an unreadable smear; on wider screens keep every tick (interval 0). The
  // tickFormatter keys off the real data index, so same-day de-duplication above still applies.
  const tickInterval = width > 0 && width < 520 ? Math.max(1, Math.ceil(data.length / 6)) - 1 : 0;

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
            interval={tickInterval}
            tickFormatter={(_: string, i: number) => tickLabels[i] ?? ""}
            tick={{ fontSize: 11, fill: "hsl(var(--muted-foreground))" }}
            tickLine={false}
            axisLine={false}
          />
          <YAxis
            domain={percent ? [0, 1] : ["auto", "auto"]}
            ticks={percent ? [0, 0.25, 0.5, 0.75, 1] : undefined}
            tick={{ fontSize: 11, fill: "hsl(var(--muted-foreground))" }}
            tickLine={false}
            axisLine={false}
            width={42}
            tickFormatter={(v: number) => (percent ? `${Math.round(v * 100)}%` : `${v}`)}
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
            formatter={(v: number, name: string) => [percent ? `${Math.round(v * 100)}%` : v, name]}
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
