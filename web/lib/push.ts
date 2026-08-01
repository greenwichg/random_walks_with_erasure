/**
 * Browser push — the pure half (B1). No React, no DOM, no runtime imports, so it runs under
 * `node --test` and is the part that can be proven rather than clicked.
 *
 * Everything here is a function of its arguments. The browser-API side (registering the worker,
 * asking for permission, subscribing) lives in `hooks/use-push.ts` and is deliberately thin, because
 * the parts that go wrong quietly — base64url conversion, the language fallback order, deriving what
 * the UI may offer from three independent capability signals — are all here.
 *
 * Contract: `docs/BROWSER_PUSH_ARCHITECTURE.md`.
 */
import { SUPPORTED, DEFAULT_LANG, type Lang } from "./i18n-core.ts";

/** Cache name + synthetic key holding the reader's language for the service worker (§4). */
export const LANG_CACHE = "ih-prefs-v1";
export const LANG_URL = "/__ih/lang";

/**
 * §4's resolution order, as a pure function so the order itself is testable:
 * **stored → payload → default**.
 *
 * The stored value wins because it is correct at *render* time. A push can sit in the push service
 * under its TTL, or wait for a device to come back online, so the language the engine knew at *send*
 * time may be hours stale. The payload value is not redundant, though: browser storage can be empty
 * (cleared site data, a restored device, a subscription that outlived the store), and that is the
 * case it covers.
 */
export function resolveLang(stored?: unknown, payload?: unknown): Lang {
  return normalizeLang(stored) ?? normalizeLang(payload) ?? DEFAULT_LANG;
}

function normalizeLang(value: unknown): Lang | null {
  return typeof value === "string" && (SUPPORTED as readonly string[]).includes(value)
    ? (value as Lang)
    : null;
}

/**
 * base64url → `Uint8Array`, for `pushManager.subscribe({ applicationServerKey })`, which accepts only
 * raw bytes.
 *
 * It fails *late* when it fails: a malformed key produces a `subscribe()` rejection at permission
 * time, which reads as "push is broken" rather than "the key is malformed". Hence a pure function
 * with its own tests.
 *
 * **No padding step.** VAPID keys are conventionally unpadded, and the obvious `padEnd` to a multiple
 * of four is dead code: `atob` implements WHATWG forgiving-base64 decode, which already accepts a
 * missing tail and rejects the one length (`% 4 === 1`) that cannot be valid either way. Measured on
 * both Node and Chromium — `atob("QQ")` and `atob("QUI")` decode, `atob("QUJD=")` throws with or
 * without the padding. A mutation test that deleted the step and survived is what surfaced it.
 */
export function urlBase64ToUint8Array(base64: string): Uint8Array<ArrayBuffer> {
  const trimmed = (base64 ?? "").trim();
  if (!trimmed) throw new Error("empty application server key");
  const standard = trimmed.replace(/-/g, "+").replace(/_/g, "/");
  const raw = atob(standard);
  // Allocated over an explicit ArrayBuffer so the result is `Uint8Array<ArrayBuffer>` — what
  // `applicationServerKey` requires. The default constructor widens to ArrayBufferLike (which admits
  // SharedArrayBuffer) and is rejected there.
  const out = new Uint8Array(new ArrayBuffer(raw.length));
  for (let i = 0; i < raw.length; i += 1) out[i] = raw.charCodeAt(i);
  return out;
}

/** The subset of `PushSubscription` this code needs — so the pure functions can be tested without a browser. */
export interface SubscriptionLike {
  endpoint: string;
  expirationTime?: number | null;
  keys?: { p256dh?: string; auth?: string };
}

/** The request body for `POST /api/push/subscriptions`, from a `PushSubscription.toJSON()`. */
export interface SubscriptionPayload {
  endpoint: string;
  p256dh: string;
  auth: string;
  expirationTime: number | null;
  userAgent?: string;
}

/**
 * Flatten a browser subscription into the API's shape. Returns `null` rather than a partial body when
 * anything required is missing: the engine would reject it anyway, and failing here means the reader
 * sees "could not enable" instead of a 422 they cannot act on.
 */
export function serializeSubscription(
  sub: SubscriptionLike | null | undefined,
  userAgent?: string,
): SubscriptionPayload | null {
  const endpoint = sub?.endpoint;
  const p256dh = sub?.keys?.p256dh;
  const auth = sub?.keys?.auth;
  if (!endpoint || !p256dh || !auth) return null;
  return {
    endpoint,
    p256dh,
    auth,
    expirationTime: sub?.expirationTime ?? null,
    ...(userAgent ? { userAgent: userAgent.slice(0, 255) } : {}),
  };
}

/** Why a subscription changed, mirroring the engine's closed set. Operational logging only — it
 *  changes no behaviour, and it is what lets an operator answer "did the rotation actually repair
 *  devices?" from the log rather than by guessing. */
export type PushReason = "user" | "repair" | "worker" | "repair_retire";

/** The browser's permission for notifications, plus the case where the API does not exist at all. */
export type PushPermission = "default" | "granted" | "denied" | "unsupported";

/** Clamp anything the platform reports to the four states this code handles. */
export function normalizePermission(value: unknown): PushPermission {
  return value === "granted" || value === "denied" || value === "default" ? value : "unsupported";
}

export interface PushCapabilities {
  /** `serviceWorker` + `PushManager` + `Notification` all present. */
  supported: boolean;
  /** The server says push is configured and switched on. */
  configured: boolean;
  permission: PushPermission;
  /** This browser holds a subscription **against the key the server serves now**. */
  subscribed: boolean;
  /** This browser holds *any* subscription, including one bound to a key the server has retired —
   *  and, crucially, including when the server no longer offers push at all. */
  hasSubscription: boolean;
}

/**
 * What the UI may offer, derived from independent signals rather than guessed from one.
 *
 * `blocked` — once a reader denies notifications the browser makes `requestPermission()` a no-op
 * forever, so a control that keeps offering to enable them is a button that cannot work. The reader
 * has to change it in browser settings, and the UI has to say so.
 *
 * `paused` — the server has push switched off (rolled back, or the key was removed) while this device
 * is still registered. Without this state the control disappeared and the reader was stranded: the row
 * survives a rollback by design, the API keeps deletion open for exactly that reason, and hiding the
 * control was what made the open route unreachable. The only action offered is turning it off.
 */
export type PushUiState = "unavailable" | "blocked" | "paused" | "off" | "on";

export function pushUiState(caps: PushCapabilities): PushUiState {
  if (!caps.supported || caps.permission === "unsupported") return "unavailable";
  // Server-side unavailability is not the same as browser-side: a device may still be registered.
  if (!caps.configured) return caps.hasSubscription ? "paused" : "unavailable";
  if (caps.permission === "denied") return "blocked";
  return caps.subscribed && caps.permission === "granted" ? "on" : "off";
}

/**
 * Whether this device's subscription should be silently re-created against the server's current key.
 *
 * A VAPID rotation invalidates every existing subscription — the push service rejects sends signed by
 * a key the endpoint was not created against — and the device cannot detect that from a failed send,
 * because it never sees one. Left alone it simply goes dark: the engine keeps a row, the sender keeps
 * getting rejections, and the reader believes they are subscribed.
 *
 * Repair is silent and prompts for nothing, so the guards are what keep it honest:
 *
 * * **`permission !== "granted"`** — never. Re-subscribing needs no prompt only because consent is
 *   already given; without it this would be an attempt to subscribe someone who has not agreed.
 * * **no existing subscription** — never. A reader who never enabled push must not have it enabled
 *   for them by a key rotation; "repair" means restoring what they chose, not choosing for them.
 * * **the key already matches** — nothing to do, and re-subscribing would churn the endpoint for no
 *   reason (every rotation of an endpoint is a row the engine must reconcile).
 */
export function shouldRepairSubscription(caps: {
  supported: boolean;
  configured: boolean;
  permission: PushPermission;
  hasSubscription: boolean;
  keyMatches: boolean;
  /**
   * Whether the ENGINE has a row for the subscription this browser holds.
   *
   * The second way the two sides desynchronise, and the more common one. A push service answering
   * `410 Gone` makes the engine prune the row immediately (B2) — ordinary attrition, not an error —
   * but the browser is never told, so it keeps its subscription object and the toggle keeps reading
   * "on" while nothing can reach the reader. The key still matches, so the rotation repair above
   * does not fire; from every signal the device has, it is subscribed.
   *
   * `undefined` means "not established" — the check was skipped or the request failed — and is
   * treated as known, so an unreachable engine cannot trigger a re-subscribe storm.
   */
  knownToServer?: boolean;
}): boolean {
  if (!caps.supported || !caps.configured) return false;
  if (caps.permission !== "granted") return false;
  if (!caps.hasSubscription) return false;
  return !caps.keyMatches || caps.knownToServer === false;
}

/**
 * Whether a subscription belongs to the key the server is currently serving.
 *
 * Rotating the VAPID pair invalidates every existing subscription — the push service will reject
 * sends signed by the new key for an endpoint created against the old one. The device cannot detect
 * that on its own, so the check is: does the subscription we hold declare the server's current key?
 * A mismatch means re-subscribe, not "already on".
 */
export function subscriptionMatchesKey(
  applicationServerKey: ArrayBuffer | null | undefined,
  serverKey: string,
): boolean {
  if (!applicationServerKey || !serverKey) return false;
  const have = new Uint8Array(applicationServerKey);
  let want: Uint8Array;
  try {
    want = urlBase64ToUint8Array(serverKey);
  } catch {
    return false;
  }
  if (have.length !== want.length) return false;
  return have.every((byte, i) => byte === want[i]);
}

/**
 * At most one run of `job` per `key` at a time; every concurrent caller gets the same promise.
 *
 * Reconciliation is triggered from two independent places — the app-wide reconciler mounted in the
 * authenticated shell, and `usePush` on the settings page, which must await it before reading the
 * subscription state it renders. On the settings page both mount, and without this they would race:
 * two `pushManager.subscribe()` calls, two `POST`s, and an endpoint rotated out from under the
 * registration that is still in flight. Deduplicating at the *caller* would mean each new caller
 * remembering that the other exists, which is exactly the coupling that goes stale.
 *
 * The slot is released when the job settles rather than cached, so this coalesces concurrency and
 * nothing else. A repair that failed must be free to run again on the next trigger — caching the
 * result would turn one bad network moment into a session-long outage, which is the failure mode
 * this whole path exists to prevent.
 *
 * Keyed because the thing being guarded is per-VAPID-key: a server that started serving a different
 * key wants a new repair, not the answer to the question about the old one.
 *
 * Returned as a factory rather than exported as a module-level map so tests get a fresh one per case
 * and cannot leak state into each other.
 */
export function singleFlight<T>(): (key: string, job: () => Promise<T>) => Promise<T> {
  let pending: { key: string; promise: Promise<T> } | null = null;
  return (key, job) => {
    if (pending && pending.key === key) return pending.promise;
    // `job()` runs SYNCHRONOUSLY — deferring it to a microtask would leave a window in which the
    // work has been promised but not started, which is a distinction no caller wants to reason
    // about. The try/catch is what an `async` wrapper would have bought: a job that throws before
    // its first `await` becomes a rejection like any other, so callers see one failure shape.
    let promise: Promise<T>;
    try {
      promise = job();
    } catch (error) {
      promise = Promise.reject(error);
    }
    const entry = { key, promise };
    pending = entry;
    // Release on settle, success or not. `.finally` runs whether or not anyone awaits the result,
    // so a fire-and-forget caller still frees the slot for the next trigger.
    return promise.finally(() => {
      if (pending === entry) pending = null;
    });
  };
}
