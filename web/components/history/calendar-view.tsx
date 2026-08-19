"use client";

import { useMemo } from "react";
import { useTranslation } from "@/lib/i18n";
import { buildCalendarGrid, firstDayOfWeek } from "@/lib/calendar-grid";
import { cn } from "@/lib/utils";

/**
 * The reading calendar: five weeks laid out as an actual calendar — weekday columns from the
 * locale's first weekday, a dated cell for every day, and month names where a month turns over.
 *
 * It replaces a contribution-style heatmap of 35 undated squares. The reading data is unchanged:
 * the same per-day counts, the same 0-4 intensity shading, and the same `dayKey` identity the
 * Timeline groups by, so clicking a day still filters the Timeline and opens the Daily Summary
 * (and clicking the selected day again clears it). The geometry lives in lib/calendar-grid.ts,
 * where it is unit-tested; this file only renders.
 *
 * Intensity is now carried twice over — by shade AND by the printed count — because a dated cell
 * has room to say what the square could only imply.
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
  const { t, formatDate, lang } = useTranslation();

  const grid = useMemo(
    () => buildCalendarGrid(entries, { weekStartsOn: firstDayOfWeek(lang) }),
    [entries, lang],
  );

  const shades = ["bg-muted", "bg-primary/25", "bg-primary/45", "bg-primary/70", "bg-primary"];
  // Ink follows the shade, because cells now carry text rather than being bare colour swatches.
  // Only the top level flips to the inverted foreground: `text-primary-foreground` is white in
  // BOTH themes, and white on `bg-primary/70` measures 3.7:1 in light mode — under the 4.5:1
  // floor for this text size. `text-foreground` is theme-aware (near-black on light, near-white
  // on dark) and clears the floor on every level it covers.
  const inks = [
    "text-muted-foreground",
    "text-foreground",
    "text-foreground",
    "text-foreground",
    "text-primary-foreground",
  ];

  const range = `${formatDate(grid.windowStart.toISOString(), {
    month: "short",
    day: "numeric",
  })} – ${formatDate(grid.windowEnd.toISOString(), {
    month: "short",
    day: "numeric",
    year: "numeric",
  })}`;

  return (
    <div className="rounded-lg border bg-card p-6">
      <div className="mb-4 flex flex-wrap items-start justify-between gap-3">
        <div>
          <h3 className="text-sm font-semibold">{t("history.last5weeks")}</h3>
          <p className="mt-0.5 text-xs text-muted-foreground">{range}</p>
        </div>
        <div className="flex items-center gap-1.5 text-xs text-muted-foreground">
          {t("history.less")}
          {shades.map((s, i) => (
            <span key={i} className={cn("h-3 w-3 rounded-sm", s)} />
          ))}
          {t("history.more")}
        </div>
      </div>

      {/* Weekday headers are decorative: every cell's aria-label already names its full date. */}
      <div className="grid grid-cols-7 gap-1.5" aria-hidden="true">
        {grid.weekdays.map((d) => (
          <div
            key={d.getDay()}
            className="pb-1 text-center text-[11px] font-medium uppercase tracking-wide text-muted-foreground"
          >
            {formatDate(d.toISOString(), { weekday: "short" })}
          </div>
        ))}
      </div>

      <div className="grid grid-cols-7 gap-1.5">
        {grid.weeks.flat().map((cell) => {
          const iso = cell.date.toISOString();
          // The 1st carries its month so the calendar says where it is in the year without a
          // separate month header; every other day is just its number.
          const dayLabel = formatDate(
            iso,
            cell.isFirstOfMonth ? { month: "short", day: "numeric" } : { day: "numeric" },
          );

          if (!cell.inWindow) {
            return (
              <div
                key={cell.key}
                className="flex aspect-square min-h-12 items-start rounded-md p-2 text-xs tabular-nums text-muted-foreground/40"
              >
                {dayLabel}
              </div>
            );
          }

          const label = `${formatDate(iso, {
            weekday: "long",
            month: "short",
            day: "numeric",
          })} · ${t("history.readCount", { n: cell.count })}`;
          const selected = selectedDay === cell.key;
          const clickable = cell.count > 0;

          return (
            <button
              key={cell.key}
              type="button"
              disabled={!clickable}
              onClick={() => onSelect(selected ? null : cell.key)}
              aria-pressed={selected}
              aria-label={label}
              title={label}
              className={cn(
                "flex aspect-square min-h-12 flex-col items-start rounded-md p-2 text-left outline-none transition-all",
                shades[cell.level],
                inks[cell.level],
                cell.isToday && "ring-1 ring-inset ring-primary/70",
                clickable
                  ? "cursor-pointer hover:ring-2 hover:ring-primary/40 focus-visible:ring-2 focus-visible:ring-primary"
                  : "cursor-default",
                selected && "ring-2 ring-primary ring-offset-1 ring-offset-background",
              )}
            >
              <span className={cn("text-xs tabular-nums", cell.isToday && "font-bold")}>
                {dayLabel}
              </span>
              {cell.count > 0 && (
                <span className="mt-auto text-[11px] tabular-nums opacity-90">
                  {t("history.readCount", { n: cell.count })}
                </span>
              )}
            </button>
          );
        })}
      </div>
    </div>
  );
}
