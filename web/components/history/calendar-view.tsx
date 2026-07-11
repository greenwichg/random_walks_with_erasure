"use client";

import { useTranslation } from "@/lib/i18n";
import { dayKey } from "@/lib/history-insights";
import { cn } from "@/lib/utils";

/**
 * The 5-week reading-activity heatmap. Phase 2: each day is a keyboard-accessible button — clicking a
 * day with reads selects it (toggles off if already selected), which the page uses to filter the
 * Timeline and open the Daily Summary. Days bucket by the shared local `dayKey`, matching the
 * Timeline's day grouping so selection stays in sync across the two modes.
 */
export function CalendarView({
  entries,
  selectedDay,
  onSelect,
}: {
  entries: { readAt: string }[];
  selectedDay: string | null;
  onSelect: (day: string | null) => void;
}) {
  const { t, formatDate } = useTranslation();

  const counts = new Map<string, number>();
  entries.forEach((e) => {
    const k = dayKey(e.readAt);
    counts.set(k, (counts.get(k) ?? 0) + 1);
  });
  const days = Array.from({ length: 35 }, (_, i) => {
    const d = new Date();
    d.setHours(0, 0, 0, 0);
    d.setDate(d.getDate() - (34 - i));
    const key = dayKey(d.toISOString());
    return { date: d, key, count: counts.get(key) ?? 0 };
  });
  const max = Math.max(...days.map((d) => d.count), 1);
  const level = (c: number) => (c === 0 ? 0 : Math.ceil((c / max) * 4));
  const shades = ["bg-muted", "bg-primary/25", "bg-primary/45", "bg-primary/70", "bg-primary"];

  return (
    <div className="rounded-lg border bg-card p-6">
      <div className="mb-4 flex items-center justify-between">
        <h3 className="text-sm font-semibold">{t("history.last5weeks")}</h3>
        <div className="flex items-center gap-1.5 text-xs text-muted-foreground">
          {t("history.less")}
          {shades.map((s, i) => (
            <span key={i} className={cn("h-3 w-3 rounded-sm", s)} />
          ))}
          {t("history.more")}
        </div>
      </div>
      <div className="grid grid-flow-col grid-rows-7 gap-1.5">
        {days.map((d) => {
          const label = `${formatDate(d.date.toISOString(), { weekday: "long", month: "short", day: "numeric" })} · ${t("history.readCount", { n: d.count })}`;
          const selected = selectedDay === d.key;
          const clickable = d.count > 0;
          return (
            <button
              key={d.key}
              type="button"
              disabled={!clickable}
              onClick={() => onSelect(selected ? null : d.key)}
              aria-pressed={selected}
              aria-label={label}
              title={label}
              className={cn(
                "aspect-square rounded-sm outline-none transition-all",
                shades[level(d.count)],
                clickable ? "cursor-pointer hover:ring-2 hover:ring-primary/40 focus-visible:ring-2 focus-visible:ring-primary" : "cursor-default",
                selected && "ring-2 ring-primary ring-offset-1 ring-offset-background",
              )}
            />
          );
        })}
      </div>
    </div>
  );
}
