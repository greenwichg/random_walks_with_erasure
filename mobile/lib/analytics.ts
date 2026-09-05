import * as SecureStore from "expo-secure-store";

import { postJson } from "@ih/core/api/client";

/**
 * Product analytics — the native client of the same `/api/events` sink the web batches to.
 *
 * Same event names, same buffering (flush at 20 events or 3 s), same rule that measuring the
 * product must never break it: every path swallows its errors. Identity differs by platform: the
 * web keeps `anonId` in localStorage and `sessionId` in sessionStorage; here the anonymous id is a
 * stable value in the app's own store and the session id is one per process, which is what a
 * session is on a phone.
 */
interface AnalyticsEvent {
  event: string;
  props?: Record<string, unknown>;
  anonId?: string;
  sessionId?: string;
  clientTs?: string;
}

const ANON_KEY = "ih.anon.id";
const FLUSH_AT = 20;
const FLUSH_MS = 3000;

function uuid(): string {
  return `${Date.now().toString(36)}-${Math.random().toString(36).slice(2, 10)}`;
}

const sessionId = uuid();
let anonId: string | undefined;
let buffer: AnalyticsEvent[] = [];
let timer: ReturnType<typeof setTimeout> | null = null;

/** Resolve (and remember) the anonymous id. Best-effort; empty when the store refuses. */
async function ensureAnonId(): Promise<void> {
  if (anonId !== undefined) return;
  try {
    const stored = await SecureStore.getItemAsync(ANON_KEY);
    if (stored) {
      anonId = stored;
      return;
    }
    anonId = uuid();
    await SecureStore.setItemAsync(ANON_KEY, anonId);
  } catch {
    anonId = "";
  }
}

export function flushAnalytics(): void {
  if (timer) {
    clearTimeout(timer);
    timer = null;
  }
  if (!buffer.length) return;
  const batch = buffer;
  buffer = [];
  void ensureAnonId()
    .then(() =>
      postJson("/events", {
        events: batch.map((e) => ({ ...e, anonId: anonId || undefined })),
      }),
    )
    .catch(() => {
      /* analytics must never throw */
    });
}

/** Hostname of a URL for failure telemetry — never the full URL. */
export function urlHost(u?: string | null): string {
  try {
    return u ? new URL(u).hostname : "";
  } catch {
    return "";
  }
}

/** Record a product event. Buffered and flushed automatically; never throws. */
export function track(event: string, props?: Record<string, unknown>): void {
  if (!event) return;
  try {
    buffer.push({
      event,
      props: props && Object.keys(props).length ? props : undefined,
      sessionId,
      clientTs: new Date().toISOString(),
    });
    if (buffer.length >= FLUSH_AT) flushAnalytics();
    else if (!timer) timer = setTimeout(flushAnalytics, FLUSH_MS);
  } catch {
    /* swallow */
  }
}
