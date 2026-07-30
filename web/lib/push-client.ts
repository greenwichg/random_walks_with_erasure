/**
 * Browser push — the browser-API half (B1). Deliberately thin: everything that can be decided by a
 * pure function lives in `lib/push.ts` and is tested there, so what remains here is the sequence of
 * platform calls, each of which either works or throws.
 *
 * Contract: `docs/BROWSER_PUSH_ARCHITECTURE.md`.
 */
import {
  LANG_CACHE,
  LANG_URL,
  normalizePermission,
  serializeSubscription,
  shouldRepairSubscription,
  subscriptionMatchesKey,
  urlBase64ToUint8Array,
  type PushPermission,
  type PushReason,
} from "./push";

export const SW_URL = "/sw.js";

/** Every capability this feature needs, present. Checked as a set because a browser missing any one
 *  of them cannot hold a subscription, and partial support is not a degraded mode. */
export function pushSupported(): boolean {
  return (
    typeof window !== "undefined" &&
    "serviceWorker" in navigator &&
    "PushManager" in window &&
    "Notification" in window
  );
}

export function currentPermission(): PushPermission {
  if (typeof window === "undefined" || !("Notification" in window)) return "unsupported";
  return normalizePermission(Notification.permission);
}

/**
 * Publish the reader's language where the service worker can read it (architecture §4).
 *
 * Written by the page rather than messaged to the worker, because a worker woken by a push may have
 * no client open to ask — and may itself have been terminated since the last page load. The Cache API
 * is the store: readable from both contexts, no schema, no versioning.
 *
 * Never throws. A reader whose storage is unavailable (private mode, quota) simply falls back to the
 * payload's language at render time, which is what that field is for.
 */
export async function publishLanguage(lang: string): Promise<void> {
  try {
    if (typeof caches === "undefined") return;
    const cache = await caches.open(LANG_CACHE);
    await cache.put(LANG_URL, new Response(lang, { headers: { "Content-Type": "text/plain" } }));
  } catch {
    /* storage unavailable — §4's fallback chain covers it */
  }
}

/** Register the worker and wait until it is ready to be subscribed against. */
export async function registerServiceWorker(): Promise<ServiceWorkerRegistration | null> {
  if (!pushSupported()) return null;
  try {
    await navigator.serviceWorker.register(SW_URL);
    return await navigator.serviceWorker.ready;
  } catch {
    return null;
  }
}

/**
 * The subscription this browser currently holds, if any.
 *
 * `getRegistration()`, deliberately, and not `ready`: `navigator.serviceWorker.ready` never settles
 * when nothing is registered. That was survivable while this was only called after registering, but
 * it is called now on a deployment with push switched off — where the worker is not registered and
 * `ready` would hang the caller forever rather than answering "no subscription".
 */
export async function currentSubscription(): Promise<PushSubscription | null> {
  if (!pushSupported()) return null;
  try {
    const reg = await navigator.serviceWorker.getRegistration();
    return reg ? await reg.pushManager.getSubscription() : null;
  } catch {
    return null;
  }
}

/**
 * Whether the held subscription is still usable against the key the server serves now.
 *
 * A VAPID rotation invalidates every existing subscription, and the device cannot detect that on its
 * own — sends simply start failing. Comparing the key the subscription was created with against the
 * current one is how a browser finds out it must re-subscribe.
 */
export async function subscriptionIsCurrent(serverKey: string): Promise<boolean> {
  const sub = await currentSubscription();
  if (!sub) return false;
  const options = sub.options as PushSubscriptionOptions | undefined;
  return subscriptionMatchesKey(options?.applicationServerKey, serverKey);
}

/** Tell the engine to forget an endpoint. Best-effort: used to retire a rotated-away subscription,
 *  where failing to clean up leaves a stale row rather than breaking the live one. */
async function deregisterEndpoint(endpoint: string, reason: PushReason): Promise<void> {
  try {
    await fetch(
      `/api/push/subscriptions?endpoint=${encodeURIComponent(endpoint)}&reason=${reason}`,
      { method: "DELETE" },
    );
  } catch {
    /* the new subscription is already registered; a stale row is the lesser failure */
  }
}

/**
 * Ensure this device holds a subscription against `serverKey` and that the engine knows about it.
 *
 * The single path both `subscribe` (reader-initiated, may prompt) and `repairSubscription` (silent,
 * never prompts) go through, so the two cannot drift — a rotation must produce exactly the same
 * end state as a fresh opt-in.
 *
 * Ordering is deliberate throughout: the new subscription is registered with the engine **before**
 * the old endpoint is retired, so an interruption leaves a device reachable rather than unreachable;
 * and a failed registration rolls the local subscription back, so the two sides never disagree about
 * whether this device is subscribed.
 */
async function ensureSubscribed(
  reg: ServiceWorkerRegistration,
  serverKey: string,
  reason: PushReason,
): Promise<boolean> {
  let sub = await reg.pushManager.getSubscription();
  let retired: string | null = null;
  if (sub && !subscriptionMatchesKey((sub.options as PushSubscriptionOptions)?.applicationServerKey, serverKey)) {
    retired = sub.endpoint;             // created against a rotated-away key: unusable
    await sub.unsubscribe();
    sub = null;
  }
  if (!sub) {
    sub = await reg.pushManager.subscribe({
      userVisibleOnly: true,            // required by Chromium, and true of this product regardless
      applicationServerKey: urlBase64ToUint8Array(serverKey),
    });
  }

  const body = serializeSubscription(sub.toJSON() as never, navigator.userAgent);
  if (!body) return false;
  const res = await fetch("/api/push/subscriptions", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ ...body, reason }),
  });
  if (!res.ok) {
    await sub.unsubscribe();            // roll back, so the two sides cannot disagree
    return false;
  }
  // Only now that the replacement is live: the endpoint we rotated away from can never be delivered
  // to again, and leaving it behind means the engine pays to attempt a dead device on every fan-out.
  if (retired && retired !== body.endpoint) await deregisterEndpoint(retired, "repair_retire");
  return true;
}

/**
 * Ask for permission and subscribe, then register the subscription with the engine.
 *
 * `requestPermission` must be called from a user gesture, which is why this is the reader-initiated
 * path and `repairSubscription` is a separate, silent one.
 */
export async function subscribe(serverKey: string): Promise<"on" | "blocked" | "failed"> {
  if (!pushSupported()) return "failed";
  const permission = normalizePermission(await Notification.requestPermission());
  if (permission !== "granted") return permission === "denied" ? "blocked" : "failed";

  const reg = await registerServiceWorker();
  if (!reg) return "failed";
  return (await ensureSubscribed(reg, serverKey, "user")) ? "on" : "failed";
}

/**
 * Silently re-create a subscription the server's current VAPID key can actually be used with —
 * the automatic half of a key rotation.
 *
 * Runs on load, prompts for nothing, and does nothing at all unless
 * {@link shouldRepairSubscription} says this device holds a subscription bound to a key the server no
 * longer serves. Without it a rotation leaves the device dark until the reader happens to notice a
 * toggle that switched itself off.
 *
 * Returns whether it repaired anything. Never throws: a failed repair leaves the reader exactly where
 * a rotation had already left them, and the next visit tries again.
 */
export async function repairSubscription(serverKey: string, configured: boolean): Promise<boolean> {
  try {
    const sub = await currentSubscription();
    const keyMatches = subscriptionMatchesKey(
      (sub?.options as PushSubscriptionOptions | undefined)?.applicationServerKey,
      serverKey,
    );
    if (
      !shouldRepairSubscription({
        supported: pushSupported(),
        configured: configured && !!serverKey,
        permission: currentPermission(),
        hasSubscription: !!sub,
        keyMatches,
      })
    ) {
      return false;
    }
    const reg = await registerServiceWorker();
    return reg ? await ensureSubscribed(reg, serverKey, "repair") : false;
  } catch {
    return false;
  }
}

/**
 * Unsubscribe this device. The engine is told first: a row the sender still believes in is worse than
 * a browser subscription nobody uses, because the first produces sends to a dead endpoint while the
 * second produces nothing at all.
 */
export async function unsubscribe(): Promise<boolean> {
  const sub = await currentSubscription();
  if (!sub) return true;
  try {
    await fetch(
      `/api/push/subscriptions?endpoint=${encodeURIComponent(sub.endpoint)}&reason=user`,
      { method: "DELETE" },
    );
  } catch {
    /* fall through — the local unsubscribe below still matters */
  }
  try {
    return await sub.unsubscribe();
  } catch {
    return false;
  }
}
