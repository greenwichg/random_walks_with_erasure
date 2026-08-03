/**
 * Story Continuation — the browser-side state (docs/STORY_CONTINUATION_DESIGN.md §6).
 *
 * The engine decides whether an offer EXISTS. Two of the design's nine gates are facts only the
 * browser holds, and they live here:
 *
 *   gate 8  dismissed      the reader declined this story once, permanently
 *   gate 9  chain cap      at most one continuation per story per session
 *
 * Two storage tiers, deliberately (§6.2):
 *
 *   sessionStorage  the ARMED candidate — prefetched at Read-click, waiting for the reader to come
 *                   back. Survives a reload in the same tab, which is the mobile eviction path, and
 *                   dies with the tab. That death is correct: a new session is the feed slot's job,
 *                   and §6.2 assigns cross-session continuation there rather than here.
 *   localStorage    dismissals + impression counts, under one key. These must outlive the session
 *                   or "permanently" is a lie, and the reader gets asked again tomorrow.
 *
 * Every function is total and never throws. Private mode, a full quota, and a disabled-storage
 * browser all degrade to "no strip" — which is the same thing an ineligible read produces, so the
 * failure mode is a feature that quietly does not appear rather than a page that breaks.
 */
import type { Continuation } from "@/types/domain";

/** sessionStorage: the candidate armed by the most recent Read click, awaiting a return. */
export const ARMED_KEY = "hv.continue.armed";
/** localStorage: `{ [storyId]: { d?: 1, n: impressions, t: epoch ms } }`. */
export const STATE_KEY = "hv.continue";

/** Entries older than this are pruned on every read, so the key cannot grow without bound. */
export const PRUNE_AFTER_MS = 30 * 24 * 60 * 60 * 1000; // 30 days
/** Impressions without engagement after which the offer is treated as declined (§6.3). */
export const MAX_IMPRESSIONS = 2;

/**
 * The dwell gate (§2.1), as a plain state machine so it can be tested at the millisecond rather
 * than through a renderer — and so `useVisibilityReturn` and its tests cannot hold two copies of
 * the same rule. Returns a function to feed visibility transitions into.
 *
 * Two decisions, both with a concrete failure mode if they go the other way:
 *
 *  * **`hiddenMs >= minHiddenMs`.** A bare visibilitychange fires on every alt-tab, notification
 *    glance and password-manager popup. A strip that appears after a four-second flick is noise
 *    attached to something the reader never did.
 *  * **A visible event with no preceding hide fires never.** The tab was already visible at mount,
 *    or the browser fired visibilitychange on a bfcache restore. No hide means no dwell was
 *    measured, and an unmeasured dwell is not a return.
 *
 * Deliberately NOT deduplicating repeats: each qualifying return is one event, and suppressing a
 * second offer is {@link mayShow}'s job. Conflating them would make the cap untestable and leave
 * the trigger holding state it does not own.
 */
export function createDwellGate(
  minHiddenMs: number,
  onReturn: (hiddenMs: number) => void,
): (state: "hidden" | "visible", now: number) => void {
  let hiddenAt: number | null = null;
  return (state, now) => {
    if (state === "hidden") {
      hiddenAt = now; // a second hide before any return replaces the first — the reader is still away
      return;
    }
    if (hiddenAt === null) return;
    const hiddenMs = now - hiddenAt;
    hiddenAt = null;
    if (hiddenMs >= minHiddenMs) onReturn(hiddenMs);
  };
}

/** The prefetched offer plus when it was armed — the client half of the freshness gate. */
export interface ArmedCandidate {
  /** Canonical URL of the article the reader opened. */
  anchorUrl: string;
  /** Epoch ms at the Read click. */
  armedAt: number;
  offer: Continuation;
}

interface StoryState {
  /** 1 when dismissed. Absent rather than `false` — the key is a third of the payload. */
  d?: 1;
  /** Impressions shown so far. */
  n: number;
  /** Epoch ms of the last write, for pruning. */
  t: number;
}

type State = Record<string, StoryState>;

function readJSON<T>(store: Storage | null, key: string): T | null {
  if (!store) return null;
  try {
    const raw = store.getItem(key);
    return raw ? (JSON.parse(raw) as T) : null;
  } catch {
    return null; // absent, private mode, or malformed — all "nothing armed / nothing recorded"
  }
}

function writeJSON(store: Storage | null, key: string, value: unknown): void {
  if (!store) return;
  try {
    store.setItem(key, JSON.stringify(value));
  } catch {
    /* quota or private mode — the strip simply does not persist */
  }
}

function session(): Storage | null {
  try {
    return typeof window === "undefined" ? null : window.sessionStorage;
  } catch {
    return null;
  }
}

function local(): Storage | null {
  try {
    return typeof window === "undefined" ? null : window.localStorage;
  } catch {
    return null;
  }
}

// ---------------------------------------------------------------- the armed candidate (session)
/** Arm the candidate prefetched at Read-click. One at a time: reading another article supersedes
 *  the previous offer rather than stacking a second (§2.2, "superseded, not stacked"). */
export function armCandidate(anchorUrl: string, offer: Continuation, now = Date.now()): void {
  writeJSON(session(), ARMED_KEY, { anchorUrl, armedAt: now, offer } satisfies ArmedCandidate);
}

/** The armed candidate, or `null`. Validates the shape rather than trusting it: this is parsed
 *  from storage a previous (possibly older) build wrote, and a half-populated offer would render a
 *  strip with an empty publisher name. */
export function readArmed(): ArmedCandidate | null {
  const c = readJSON<ArmedCandidate>(session(), ARMED_KEY);
  if (!c || typeof c.anchorUrl !== "string" || typeof c.armedAt !== "number") return null;
  const o = c.offer;
  if (!o || typeof o.storyId !== "string" || !o.sibling || typeof o.sibling.url !== "string") {
    return null;
  }
  return c;
}

/** Disarm — after the strip is shown, opened, or dismissed. */
export function clearArmed(): void {
  try {
    session()?.removeItem(ARMED_KEY);
  } catch {
    /* nothing to clear */
  }
}

// ---------------------------------------------------------------- dismissals + impressions (local)
/** The persisted state, pruned of entries older than 30 days. */
export function readState(now = Date.now()): State {
  const raw = readJSON<State>(local(), STATE_KEY);
  if (!raw || typeof raw !== "object") return {};
  const kept: State = {};
  let pruned = false;
  for (const [id, v] of Object.entries(raw)) {
    if (!v || typeof v !== "object" || typeof v.t !== "number") {
      pruned = true;
      continue;
    }
    if (now - v.t > PRUNE_AFTER_MS) {
      pruned = true;
      continue;
    }
    kept[id] = { n: typeof v.n === "number" ? v.n : 0, t: v.t, ...(v.d === 1 ? { d: 1 } : {}) };
  }
  if (pruned) writeJSON(local(), STATE_KEY, kept); // prune on read, so no separate sweep is needed
  return kept;
}

/** Record that the reader dismissed this story's offer. Per STORY, not per card: dismissing on one
 *  member suppresses every other member of the same cluster (§6.1). */
export function dismissStory(storyId: string, now = Date.now()): void {
  const s = readState(now);
  s[storyId] = { ...(s[storyId] ?? { n: 0 }), d: 1, t: now };
  writeJSON(local(), STATE_KEY, s);
}

/** Record one impression and return the 1-based index just shown (for `impressionIndex`). */
export function recordImpression(storyId: string, now = Date.now()): number {
  const s = readState(now);
  const n = (s[storyId]?.n ?? 0) + 1;
  s[storyId] = { ...(s[storyId] ?? {}), n, t: now };
  writeJSON(local(), STATE_KEY, s);
  return n;
}

/**
 * Whether this story may still be offered: not dismissed, and under the impression cap.
 *
 * The cap exists because on mobile a reload IS the return path, so without it the strip would come
 * back on every page view for the whole freshness window. After two impressions with no engagement,
 * silence is the honest reading of the reader's answer (§6.3).
 */
export function mayShow(storyId: string, now = Date.now()): boolean {
  const e = readState(now)[storyId];
  if (!e) return true;
  return e.d !== 1 && (e.n ?? 0) < MAX_IMPRESSIONS;
}
