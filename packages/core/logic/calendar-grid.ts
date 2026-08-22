/**
 * The Reading-History calendar grid — pure date/count layout, no React and no i18n, so the
 * geometry is testable under bare `node --test` and the component stays a rendering concern.
 *
 * The previous Calendar mode was a contribution-style heatmap: 35 undated squares filled
 * column-first. It was weekday-consistent by accident of consecutive days (row r always held the
 * same weekday), but nothing said WHICH weekday, no cell carried its date, and the first row
 * started on whatever weekday fell 34 days back. This builds a real calendar instead: whole weeks
 * from the locale's first weekday, every day carrying its own date, with the reading window marked
 * inside a rectangle of complete weeks.
 *
 * The counts, the 0-4 intensity levels and the day keys are unchanged — the same `dayKey` the
 * Timeline groups by, so day selection stays in sync across both modes.
 */
import { dayKey } from "./history-insights.ts";

/** Days of reading history the calendar covers (five weeks, matching the previous heatmap). */
export const WINDOW_DAYS = 35;

export interface CalendarCell {
  /** Local day key (YYYY-MM-DD) — the selection identity shared with the Timeline. */
  key: string;
  date: Date;
  count: number;
  /** 0 (none) … 4 (busiest), scaled against the busiest day in the window. */
  level: number;
  /** False for the leading/trailing days that only exist to complete a week. */
  inWindow: boolean;
  isToday: boolean;
  /** The 1st of a month — the component prints the month name here for context. */
  isFirstOfMonth: boolean;
}

export interface CalendarGrid {
  /** Complete weeks, each exactly 7 cells, in display order. */
  weeks: CalendarCell[][];
  /** Seven reference dates in display order, for localized weekday headers. */
  weekdays: Date[];
  windowStart: Date;
  windowEnd: Date;
  /** Busiest day in the window (>= 1), the denominator behind `level`. */
  max: number;
}

/**
 * First weekday per supported language: Sunday for `en` (US convention, the catalog's locale),
 * Monday elsewhere. Deliberately a table rather than `Intl.Locale.weekInfo`, which is still absent
 * on some engines and would silently change the grid's shape depending on the browser.
 */
export function firstDayOfWeek(lang: string): number {
  return lang === "en" ? 0 : 1;
}

/** Local midnight of a date, via component arithmetic so DST shifts cannot move the day. */
function atMidnight(d: Date): Date {
  return new Date(d.getFullYear(), d.getMonth(), d.getDate());
}

/** `n` days after `d`, normalized by the Date constructor (DST-safe; never ms arithmetic). */
function addDays(d: Date, n: number): Date {
  return new Date(d.getFullYear(), d.getMonth(), d.getDate() + n);
}

export function buildCalendarGrid(
  entries: { readAt: string }[],
  opts: { today?: Date; weekStartsOn?: number; windowDays?: number } = {},
): CalendarGrid {
  const windowDays = opts.windowDays ?? WINDOW_DAYS;
  const weekStartsOn = ((opts.weekStartsOn ?? 0) % 7 + 7) % 7;
  const today = atMidnight(opts.today ?? new Date());
  const windowStart = addDays(today, -(windowDays - 1));

  const counts = new Map<string, number>();
  for (const e of entries) {
    const k = dayKey(e.readAt);
    counts.set(k, (counts.get(k) ?? 0) + 1);
  }

  // Pad to whole weeks. Both offsets are derived from the weekday of the window's own edges, so
  // the total is always a multiple of 7 (35 when the window already starts on the first weekday,
  // 42 otherwise) and every row rendered is a complete week.
  const leading = (windowStart.getDay() - weekStartsOn + 7) % 7;
  const trailing = (weekStartsOn + 6 - today.getDay() + 7) % 7;
  const gridStart = addDays(windowStart, -leading);
  const total = leading + windowDays + trailing;

  // Day keys are zero-padded YYYY-MM-DD, so string comparison IS date comparison — no timezone
  // arithmetic needed to decide what falls inside the window.
  const startKey = dayKey(windowStart.toISOString());
  const todayKey = dayKey(today.toISOString());

  const cells: CalendarCell[] = [];
  for (let i = 0; i < total; i++) {
    const date = addDays(gridStart, i);
    const key = dayKey(date.toISOString());
    const inWindow = key >= startKey && key <= todayKey;
    cells.push({
      key,
      date,
      count: inWindow ? counts.get(key) ?? 0 : 0,
      level: 0,
      inWindow,
      isToday: key === todayKey,
      isFirstOfMonth: date.getDate() === 1,
    });
  }

  const max = Math.max(1, ...cells.filter((c) => c.inWindow).map((c) => c.count));
  for (const c of cells) c.level = c.count === 0 ? 0 : Math.ceil((c.count / max) * 4);

  const weeks: CalendarCell[][] = [];
  for (let i = 0; i < cells.length; i += 7) weeks.push(cells.slice(i, i + 7));

  return {
    weeks,
    weekdays: cells.slice(0, 7).map((c) => c.date),
    windowStart,
    windowEnd: today,
    max,
  };
}
