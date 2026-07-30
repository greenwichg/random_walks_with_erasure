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
  subscribed: boolean;
}

/**
 * What the UI may offer, derived from four independent signals rather than guessed from one.
 *
 * `blocked` is the state that most needs naming: once a reader denies notifications, the browser
 * makes `requestPermission()` a no-op forever, so a control that keeps offering to enable them is a
 * button that cannot work. The reader has to change it in browser settings, and the UI has to say so.
 */
export type PushUiState = "unavailable" | "blocked" | "off" | "on";

export function pushUiState(caps: PushCapabilities): PushUiState {
  if (!caps.supported || !caps.configured || caps.permission === "unsupported") return "unavailable";
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
}): boolean {
  if (!caps.supported || !caps.configured) return false;
  if (caps.permission !== "granted") return false;
  if (!caps.hasSubscription) return false;
  return !caps.keyMatches;
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
