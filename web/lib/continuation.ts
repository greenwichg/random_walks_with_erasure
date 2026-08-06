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
import { track } from "./analytics.ts";

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
  /** Impressions shown so far. Counts READ EPISODES, not renders — see {@link recordImpression}. */
  n: number;
  /** Epoch ms of the last write, for pruning. */
  t: number;
  /** `armedAt` of the episode the latest impression was counted for, so re-rendering that same
   *  offer after a reload or a navigation is free. Absent on state written before this existed. */
  a?: number;
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
/**
 * In-memory listeners for "the armed candidate changed".
 *
 * A page can hold sixty article cards, and each one needs to know whether IT is the card the reader
 * just opened from. Subscribing each to `visibilitychange` would put sixty DOM listeners on the
 * document to serve at most one strip; polling sessionStorage on every render would be a synchronous
 * read per card per keystroke. Instead the arming call — which happens once, on a click — notifies
 * cheap in-memory subscribers, and only the card whose URL matches goes on to attach the DOM
 * listener. Storage events are not used deliberately: they fire in OTHER tabs, never the one that
 * wrote, which is the opposite of what is needed here.
 */
const armedListeners = new Set<() => void>();

/** Subscribe to arming changes. Returns an unsubscribe. */
export function subscribeArmed(fn: () => void): () => void {
  armedListeners.add(fn);
  return () => void armedListeners.delete(fn);
}

/** Arm the candidate prefetched at Read-click. One at a time: reading another article supersedes
 *  the previous offer rather than stacking a second (§2.2, "superseded, not stacked"). */
export function armCandidate(anchorUrl: string, offer: Continuation, now = Date.now()): void {
  writeJSON(session(), ARMED_KEY, { anchorUrl, armedAt: now, offer } satisfies ArmedCandidate);
  notifyArmed();
}

function notifyArmed(): void {
  for (const fn of Array.from(armedListeners)) {
    try {
      fn();
    } catch {
      /* one bad subscriber must not stop the others */
    }
  }
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

/**
 * Whether recording a read of ``url`` should hold back the recommendations refetch.
 *
 * The feed excludes articles the reader has already read (``exclude_seen``), so refetching right
 * after a read DROPS the very article they just opened — unmounting the card, and with it the
 * ContinuationStrip that lives on it. The reader then returns to a feed where nothing is mounted
 * for that anchor, and the armed candidate stays valid and invisible forever. That is why the strip
 * never appeared on Recommendations.
 *
 * True only when a continuation is armed FOR THIS url, so the feed still refreshes immediately for
 * the ~95% of reads with no offer — which is the behaviour the 2026-08-02 read-invalidation fix
 * exists for. When it does hold back, the query is still marked stale and refreshes on the reader's
 * next navigation.
 */
export function shouldDeferFeedRefetch(url: string): boolean {
  const armed = readArmed();
  return armed !== null && armed.anchorUrl === url;
}

/** Disarm — after the strip is shown, opened, or dismissed. */
export function clearArmed(): void {
  try {
    session()?.removeItem(ARMED_KEY);
  } catch {
    /* nothing to clear */
  }
  notifyArmed();
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
    kept[id] = {
      n: typeof v.n === "number" ? v.n : 0,
      t: v.t,
      ...(v.d === 1 ? { d: 1 } : {}),
      ...(typeof v.a === "number" ? { a: v.a } : {}),
    };
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
export function recordImpression(storyId: string, now = Date.now(), armedAt?: number): number {
  const s = readState(now);
  const prev = s[storyId];
  // The SAME offer re-rendering is not a second impression. Since the trigger includes a mount
  // (which mobile requires — a discarded tab reloads rather than firing visibilitychange), a reader
  // who flicks to Discover and back would otherwise spend the whole two-impression budget in
  // seconds without having looked at the strip once, and the story would go permanently quiet.
  // §6.3's cap is about being asked repeatedly, and being asked again is a new READ EPISODE —
  // which `armedAt` identifies exactly.
  if (armedAt !== undefined && prev?.a === armedAt) return prev.n ?? 1;
  const n = (prev?.n ?? 0) + 1;
  s[storyId] = { ...(prev ?? {}), n, t: now, ...(armedAt === undefined ? {} : { a: armedAt }) };
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
export function mayShow(storyId: string, now = Date.now(), armedAt?: number): boolean {
  const e = readState(now)[storyId];
  if (!e) return true;
  if (e.d === 1) return false;                 // dismissed is dismissed, whatever is armed
  // Already counted for THIS episode: the reader is looking at an offer they were legitimately
  // shown, and reloading the page must not retract it.
  if (armedAt !== undefined && e.a === armedAt) return true;
  return (e.n ?? 0) < MAX_IMPRESSIONS;
}

// ---------------------------------------------------------------- the prefetch (§10.2)
/**
 * Ask the engine for this article's continuation and arm it. Called at Read-click, deliberately
 * BEFORE `window.open`: the request then overlaps the tab switch, so by the time the reader is back
 * the answer is already in sessionStorage and the strip appears without a fetch of its own.
 *
 * Silent on every failure. A null, a 401, a timeout and an outage are the same thing to the caller —
 * nothing is armed, so nothing renders. Never awaited by the click handler: the reader's tab must
 * open at once, and a comparison they have not asked for yet must never delay it.
 */
export function prefetchContinuation(anchorUrl: string): void {
  if (typeof fetch === "undefined") return;
  fetch(`/api/me/continuation?url=${encodeURIComponent(anchorUrl)}`, {
    credentials: "same-origin",
    // `keepalive`, because this request is issued on the same tick as `window.open` and the tab is
    // backgrounded before it completes. Desktop browsers let a pending fetch finish; MOBILE ones
    // are free to suspend or abandon work in a backgrounded tab, and an abandoned prefetch arms
    // nothing — the engine answers, the counters record an `offer`, and the browser never sees it.
    // `keepalive` is the platform's own "finish this even though the page is going away", already
    // used by the analytics beacon path for the same reason. The 64 KB limit it carries applies to
    // request bodies; this is a GET.
    keepalive: true,
  })
    .then((r) => (r.ok ? r.json() : null))
    .then((offer: Continuation | null) => {
      if (!offer || !offer.storyId || !offer.sibling?.url) return;

      // `eligible` is the ENGINE saying an offer exists — the number that sizes the audience
      // (design §7, and §9.1's measured 9.1%). It is recorded before arming so that the gap
      // between it and `armed` is exactly the client-side loss: storage refused, quota full,
      // private mode. Reporting one event for both would hide that difference, which is the only
      // reason the two exist separately.
      track("continuation_eligible", {
        storyId: offer.storyId,
        anchorLean: offer.anchor.lean,
        siblingLean: offer.sibling.lean,
        distance: offer.distance,
        candidateCount: offer.candidateCount,
      });

      armCandidate(anchorUrl, offer);
      // `hidden` is whether the publisher's tab had ALREADY taken focus when the answer landed —
      // the ordering the whole trigger depends on. Arming while hidden means the card enables its
      // visibility listener in a backgrounded tab, where the browser is free to defer the effect;
      // if that deferral runs past the reader's return, the hide is never observed and the return
      // is correctly-but-uselessly ignored. Cheap to record, and it is the difference between "the
      // gates rejected it" and "the trigger never fired".
      if (readArmed()) {
        track("continuation_armed", {
          storyId: offer.storyId,
          hidden: typeof document === "undefined" ? false : document.visibilityState === "hidden",
        });
      }
    })
    .catch(() => {
      /* offline, aborted, or a slow engine — the reader loses a strip and notices nothing */
    });
}
