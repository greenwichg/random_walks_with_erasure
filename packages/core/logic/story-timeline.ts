/**
 * Story-timeline presentation (pure) — turn the intelligence endpoint's raw event log into the
 * condensed rows the timeline renders.
 *
 * The raw log is honest but visually congested: a story that 15 publishers join inside two hours
 * arrives as 15 near-identical "X joined the coverage" rows, every one datelined with the same
 * day. Two derivations fix that without touching the data:
 *
 *  - DAY MARKERS — a `day` row is emitted whenever the LOCAL calendar day changes, so the per-row
 *    dateline can shrink to a time of day instead of repeating one identical date down the column.
 *    Local because that is the clock the divider is rendered in; see `dayOf`.
 *  - JOIN RUNS — consecutive `publisher_join` events within one day collapse into a single `joins`
 *    row carrying every publisher name (rendered as chips). Any other event type (first report,
 *    milestone, perspective expansion, latest) breaks the run: those are the beats worth reading
 *    individually, and they keep their own rows.
 *
 * Total: input order is preserved and nothing is dropped — a collapsed run still names every
 * publisher. The one ambient input is the runtime's time zone, which `dayOf` needs so that a day
 * divider is grouped by the same clock it is labelled in.
 */
import type { StoryTimelineEvent } from "../domain/types.ts";

export type TimelineRow =
  /** Calendar-day divider; `iso` is the first event's timestamp on that day. */
  | { kind: "day"; iso: string }
  /** A single event, rendered as before. */
  | { kind: "event"; event: StoryTimelineEvent }
  /** A run of consecutive publisher joins on one day, collapsed to one row. */
  | { kind: "joins"; publishers: string[]; date: string };

/**
 * The LOCAL calendar day of an event ("2026-07-25"), or "" when there is no date.
 *
 * Local, not the ISO prefix, because the divider this key produces is RENDERED in local time
 * (`formatDate` → `toLocaleDateString`). Keying the grouping off the UTC prefix while labelling it
 * locally splits one local day in two anywhere west of UTC: 18:00Z and the next day's 02:00Z are
 * both Aug 29 in Los Angeles, but land under two separate dividers that BOTH read "Aug 29". That
 * is not a cosmetic mismatch — it tells the reader the coverage spanned two days when it did not.
 * Key and label now come from the same clock, so they cannot disagree.
 *
 * An unparseable string keeps the old prefix behaviour: it is still a stable grouping key, and
 * inventing a date for junk would be worse than grouping it consistently.
 */
function dayOf(iso: string | undefined): string {
  if (typeof iso !== "string") return "";
  const d = new Date(iso);
  if (Number.isNaN(d.getTime())) return iso.slice(0, 10);
  const pad = (n: number) => String(n).padStart(2, "0");
  return `${d.getFullYear()}-${pad(d.getMonth() + 1)}-${pad(d.getDate())}`;
}

/**
 * Condense the raw timeline. `minRun` is the smallest join-run worth collapsing (default 2 — two
 * identical neighbouring rows already read as repetition).
 */
export function condenseTimeline(
  events: StoryTimelineEvent[],
  { minRun = 2 }: { minRun?: number } = {},
): TimelineRow[] {
  const out: TimelineRow[] = [];
  let currentDay: string | null = null;
  /** Buffered consecutive publisher_join events (all on `currentDay`). */
  let run: StoryTimelineEvent[] = [];

  const flushRun = () => {
    if (run.length === 0) return;
    if (run.length >= minRun) {
      const last = run[run.length - 1]!;
      out.push({
        kind: "joins",
        publishers: run.map((e) => e.publisher).filter((p): p is string => !!p && !!p.trim()),
        date: last.date,
      });
    } else {
      for (const event of run) out.push({ kind: "event", event });
    }
    run = [];
  };

  for (const event of events) {
    const day = dayOf(event.date);
    if (day !== currentDay) {
      flushRun();
      currentDay = day;
      if (day) out.push({ kind: "day", iso: event.date });
    }
    // A join with no publisher name has nothing to contribute to a grouped row — keep it singular.
    if (event.type === "publisher_join" && event.publisher) {
      run.push(event);
    } else {
      flushRun();
      out.push({ kind: "event", event });
    }
  }
  flushRun();
  return out;
}
