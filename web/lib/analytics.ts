/**
 * Product analytics — vendor-agnostic client (PA1).
 *
 * The app records product events through `track(event, props)`; the concrete destination is a
 * swappable *provider*, so no vendor is hardcoded. The default `beaconProvider` batches events to the
 * same-origin `/api/events` proxy (which forwards to the engine sink); `setAnalyticsProvider()` swaps
 * in Google Analytics / Mixpanel / PostHog / Amplitude / a custom sink later — call sites never change.
 * This is the exact seam OBS1 established for error reporting (`reportError` / `setErrorReporter`).
 *
 * Identity: a stable `anonId` (localStorage) attributes anonymous, pre-account events; a `sessionId`
 * (sessionStorage) groups a browsing session. The signed-in `userId` is resolved SERVER-SIDE by the
 * sink from the trusted session — never asserted here — so the client can't spoof identity.
 *
 * Everything is best-effort: buffered, flushed on a timer / batch threshold / page hide, and it never
 * throws into the caller (measuring the product must not break it).
 */

export interface AnalyticsEvent {
  event: string;
  props?: Record<string, unknown>;
  anonId?: string;
  sessionId?: string;
  clientTs?: string;
}

export interface AnalyticsProvider {
  readonly name: string;
  /** Deliver a batch of events. Must be fire-and-forget and never throw. */
  send(events: AnalyticsEvent[]): void;
}

const ANON_KEY = "ih_anon_id";
const SESSION_KEY = "ih_session_id";
const ENDPOINT = "/api/events";
const FLUSH_AT = 20; // flush when the buffer reaches this many events
const FLUSH_MS = 3000; // …or this long after the first buffered event

function uuid(): string {
  try {
    if (typeof crypto !== "undefined" && crypto.randomUUID) return crypto.randomUUID();
  } catch {
    /* fall through */
  }
  return `${Date.now().toString(36)}-${Math.random().toString(36).slice(2, 10)}`;
}

/** A persistent random id for this browser (anonymous identity). Empty string on the server. */
export function anonId(): string {
  if (typeof window === "undefined") return "";
  try {
    let id = window.localStorage.getItem(ANON_KEY);
    if (!id) {
      id = uuid();
      window.localStorage.setItem(ANON_KEY, id);
    }
    return id;
  } catch {
    return ""; // storage blocked (private mode / cookies off) — still send, just unattributed
  }
}

/** A random id for this browsing session (groups events; drives retention). Empty on the server. */
export function sessionId(): string {
  if (typeof window === "undefined") return "";
  try {
    let id = window.sessionStorage.getItem(SESSION_KEY);
    if (!id) {
      id = uuid();
      window.sessionStorage.setItem(SESSION_KEY, id);
    }
    return id;
  } catch {
    return "";
  }
}

/** Default provider: batch to the same-origin sink. sendBeacon survives page unload; fetch is the
 *  fallback. Fire-and-forget; swallows every error. */
export const beaconProvider: AnalyticsProvider = {
  name: "beacon",
  send(events) {
    if (!events.length) return;
    try {
      const body = JSON.stringify({ events });
      if (typeof navigator !== "undefined" && navigator.sendBeacon) {
        navigator.sendBeacon(ENDPOINT, new Blob([body], { type: "application/json" }));
      } else if (typeof fetch !== "undefined") {
        void fetch(ENDPOINT, {
          method: "POST",
          body,
          keepalive: true,
          headers: { "content-type": "application/json" },
        }).catch(() => {});
      }
    } catch {
      /* analytics must never throw */
    }
  },
};

/** Dev/testing provider: log to the console, no network. */
export const consoleProvider: AnalyticsProvider = {
  name: "console",
  send(events) {
    // eslint-disable-next-line no-console
    for (const e of events) console.debug("[analytics]", e.event, e.props ?? {});
  },
};

/** Disable analytics entirely. */
export const noopProvider: AnalyticsProvider = { name: "noop", send() {} };

let _provider: AnalyticsProvider = beaconProvider;
let _buffer: AnalyticsEvent[] = [];
let _timer: ReturnType<typeof setTimeout> | null = null;
let _listenersBound = false;

/** Swap the analytics provider (call once at startup to plug in a vendor). */
export function setAnalyticsProvider(provider: AnalyticsProvider): void {
  _provider = provider;
}

export function currentAnalyticsProvider(): AnalyticsProvider {
  return _provider;
}

/** Send everything buffered right now. Safe to call anytime; never throws. */
export function flushAnalytics(): void {
  if (_timer) {
    clearTimeout(_timer);
    _timer = null;
  }
  if (!_buffer.length) return;
  const batch = _buffer;
  _buffer = [];
  try {
    _provider.send(batch);
  } catch {
    /* swallow */
  }
}

function bindFlushListeners(): void {
  if (_listenersBound || typeof window === "undefined") return;
  _listenersBound = true;
  // Flush on page hide / tab background — the moments a session ends and buffered events would be lost.
  const onHide = () => flushAnalytics();
  window.addEventListener("pagehide", onHide);
  window.addEventListener("visibilitychange", () => {
    if (document.visibilityState === "hidden") flushAnalytics();
  });
}

/** Hostname of a URL for failure telemetry — never the full URL (paths can carry per-article
 *  identifiers, and the server-side allowlist documents `host` as the stored field). */
export function urlHost(u?: string | null): string {
  try {
    return u ? new URL(u).hostname : "";
  } catch {
    return "";
  }
}

/**
 * Record a product event. Buffered and flushed automatically; a no-op on the server and never throws.
 * `props` are further allow-listed + truncated server-side, so only documented, pseudonymous fields
 * are ever stored.
 */
export function track(event: string, props?: Record<string, unknown>): void {
  if (typeof window === "undefined" || !event) return;
  try {
    bindFlushListeners();
    _buffer.push({
      event,
      props: props && Object.keys(props).length ? props : undefined,
      anonId: anonId() || undefined,
      sessionId: sessionId() || undefined,
      clientTs: new Date().toISOString(),
    });
    if (_buffer.length >= FLUSH_AT) {
      flushAnalytics();
    } else if (!_timer) {
      _timer = setTimeout(flushAnalytics, FLUSH_MS);
    }
  } catch {
    /* analytics must never throw into the caller */
  }
}
