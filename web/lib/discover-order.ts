/**
 * River ordering — publisher interleave for Discover's scan rows.
 *
 * One outlet's feed poll lands as a burst (measured on the live river: Sportskeeda filed 6 of the
 * 12 visible rows), and recency-only order renders the burst verbatim. The front-page tier is
 * already publisher-diverse; this extends the idea to the river with the weakest rule that fixes
 * the symptom: no two ADJACENT rows from the same publisher while any other publisher's row is
 * still pending. Greedy and deterministic — walk the list, and when the next item repeats the
 * previous publisher, pull forward the nearest item from a different one.
 *
 * Nothing is hidden and nothing is demoted out of the visible set: the output is a permutation,
 * every deferred item lands at the next legal slot, and when only the bursting publisher remains
 * (or a publisher filter makes everything one outlet) the input order passes through unchanged.
 * Pure function of the full fetched list, computed once per fetch — Load More reveals more of a
 * FIXED order, so rows the reader has seen never move.
 */
export function interleavePublishers<T extends { publisher?: string }>(items: T[]): T[] {
  const out: T[] = [];
  const rest = [...items];
  while (rest.length) {
    const prev = out.length ? out[out.length - 1]?.publisher : undefined;
    let i = rest.findIndex((it) => it.publisher !== prev);
    if (i < 0) i = 0; // only the bursting publisher left: emit in original order, hide nothing
    out.push(rest.splice(i, 1)[0] as T);
  }
  return out;
}

// --------------------------------------------------------------------------- //
// River rhythm (the approved mock spec): time landmarks + featured beats.
// --------------------------------------------------------------------------- //

export type MarkLabel = "pastHour" | "earlierToday" | "yesterday" | "earlier";
export type RiverItem<T> =
  | { kind: "row"; article: T }
  | { kind: "beat"; article: T }
  | { kind: "mark"; label: MarkLabel };

/** Every 9th river slot is a featured beat… */
export const BEAT_EVERY = 9;
/** …taken by the next beat-worthy article within this many slots (same rule shape as the
 *  front-page lead pick). No candidate in the window → the slot stays a quiet row. */
export const BEAT_LOOKAHEAD = 6;

/**
 * Which landmark bucket an article belongs to, from its STORED `publishedAt` and the reader's
 * clock. "Today"/"yesterday" are the reader's local calendar days — that is what those words
 * mean to the person scrolling. Undated (or unparseable) articles land in "earlier": a missing
 * timestamp must never claim freshness.
 */
export function bucketLabel(publishedAt: string | undefined, now: Date): MarkLabel {
  const t = publishedAt ? new Date(publishedAt) : null;
  if (!t || isNaN(+t)) return "earlier";
  if (+now - +t <= 3_600_000) return "pastHour"; // small future skew tolerated by <=
  const midnight = new Date(now);
  midnight.setHours(0, 0, 0, 0);
  if (+t >= +midnight) return "earlierToday";
  const yesterday = new Date(midnight);
  yesterday.setDate(yesterday.getDate() - 1);
  if (+t >= +yesterday) return "yesterday";
  return "earlier";
}

const MARK_ORDER: readonly MarkLabel[] = ["pastHour", "earlierToday", "yesterday", "earlier"];

/**
 * Compose the river: bucket by landmark FIRST (each row sits under a header that is true of it),
 * interleave publishers WITHIN each bucket (bursts are almost always intra-hour, so the spread
 * survives), then promote a beat at every `BEAT_EVERY`th article slot counted globally — the
 * beat pulls the nearest beatable article forward within `BEAT_LOOKAHEAD` slots, never across a
 * landmark boundary (a beat must not move an article under a header that would lie about it).
 * Deterministic for a given (items, now); a permutation — nothing is dropped or duplicated.
 */
export function composeRiver<T extends { publisher?: string; publishedAt?: string }>(
  items: T[],
  opts: { now: Date; beatable: (a: T) => boolean },
): RiverItem<T>[] {
  const buckets = new Map<MarkLabel, T[]>();
  for (const a of items) {
    const k = bucketLabel(a.publishedAt, opts.now);
    const g = buckets.get(k);
    if (g) g.push(a);
    else buckets.set(k, [a]);
  }
  const out: RiverItem<T>[] = [];
  let slot = 0;
  for (const label of MARK_ORDER) {
    const group = buckets.get(label);
    if (!group || group.length === 0) continue;
    out.push({ kind: "mark", label });
    const arr = interleavePublishers(group);
    for (let i = 0; i < arr.length; i++) {
      slot++;
      if (slot % BEAT_EVERY === 0) {
        let j = -1;
        for (let k = i; k < Math.min(arr.length, i + BEAT_LOOKAHEAD); k++) {
          if (opts.beatable(arr[k] as T)) {
            j = k;
            break;
          }
        }
        if (j >= 0) {
          const [b] = arr.splice(j, 1);
          arr.splice(i, 0, b as T); // pull forward; everyone else keeps relative order
          out.push({ kind: "beat", article: arr[i] as T });
          continue;
        }
      }
      out.push({ kind: "row", article: arr[i] as T });
    }
  }
  return out;
}

/**
 * The first `budget` ARTICLE slots of a composed river (marks ride along free — they are
 * headers, not content), with any trailing header dropped so Load More never leaves a label
 * over nothing.
 */
export function sliceRiver<T>(seq: RiverItem<T>[], budget: number): RiverItem<T>[] {
  const out: RiverItem<T>[] = [];
  let n = 0;
  for (const it of seq) {
    if (it.kind === "mark") {
      out.push(it);
      continue;
    }
    if (n >= budget) break;
    out.push(it);
    n++;
  }
  while (out.length && (out[out.length - 1] as RiverItem<T>).kind === "mark") out.pop();
  return out;
}
