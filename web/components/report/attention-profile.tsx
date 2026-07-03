"use client";

import { Cell, Pie, PieChart, Tooltip } from "recharts";
import type { EmotionShare } from "@/types/domain";
import { EMOTION_META } from "@/lib/metrics";
import { useMeasure } from "@/hooks/use-measure";

/** Donut of the emotional makeup of a reader's diet (fear/outrage/…/neutral). */
export function AttentionProfile({ attention }: { attention: EmotionShare }) {
  const { ref, width } = useMeasure<HTMLDivElement>(240);
  const dim = Math.min(width, 240);

  const data = (Object.keys(attention) as (keyof EmotionShare)[]).map((key) => ({
    key,
    name: EMOTION_META[key].label,
    value: Math.round(attention[key] * 100),
    color: EMOTION_META[key].color,
  }));
  const charged = Math.round((attention.fear + attention.outrage) * 100);

  return (
    <div className="flex flex-col items-center gap-4 sm:flex-row sm:gap-6">
      <div ref={ref} className="relative flex w-full justify-center sm:w-auto" style={{ height: dim }}>
        <PieChart width={dim} height={dim}>
          <Pie
            data={data}
            dataKey="value"
            nameKey="name"
            innerRadius="62%"
            outerRadius="92%"
            paddingAngle={2}
            stroke="none"
            animationDuration={800}
          >
            {data.map((d) => (
              <Cell key={d.key} fill={d.color} />
            ))}
          </Pie>
          <Tooltip
            contentStyle={{
              borderRadius: 12,
              border: "1px solid hsl(var(--border))",
              background: "hsl(var(--popover))",
              color: "hsl(var(--popover-foreground))",
              fontSize: 12,
            }}
            formatter={(v: number, n: string) => [`${v}%`, n]}
          />
        </PieChart>
        <div className="pointer-events-none absolute inset-0 flex flex-col items-center justify-center">
          <span className="text-2xl font-semibold tabular-nums">{charged}%</span>
          <span className="text-[0.7rem] text-muted-foreground">charged</span>
        </div>
      </div>

      <div className="grid w-full grid-cols-2 gap-x-4 gap-y-2 sm:w-auto sm:grid-cols-1">
        {data.map((d) => (
          <div key={d.key} className="flex items-center gap-2 text-sm">
            <span className="h-2.5 w-2.5 rounded-full" style={{ background: d.color }} />
            <span className="text-muted-foreground">{d.name}</span>
            <span className="ml-auto font-medium tabular-nums">{d.value}%</span>
          </div>
        ))}
      </div>
    </div>
  );
}
