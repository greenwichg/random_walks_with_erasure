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
  subscriptionMatchesKey,
  urlBase64ToUint8Array,
  type PushPermission,
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

/** The subscription this browser currently holds, if any. */
export async function currentSubscription(): Promise<PushSubscription | null> {
  if (!pushSupported()) return null;
  try {
    const reg = await navigator.serviceWorker.ready;
    return await reg.pushManager.getSubscription();
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

/**
 * Ask for permission and subscribe, then register the subscription with the engine.
 *
 * Order matters and is not interchangeable: `requestPermission` must be called from a user gesture,
 * and a subscription created before the engine knows about it is a device the sender cannot reach.
 * If registration fails the local subscription is rolled back, so the browser and the engine never
 * disagree about whether this device is subscribed.
 */
export async function subscribe(serverKey: string): Promise<"on" | "blocked" | "failed"> {
  if (!pushSupported()) return "failed";
  const permission = normalizePermission(await Notification.requestPermission());
  if (permission !== "granted") return permission === "denied" ? "blocked" : "failed";

  const reg = await registerServiceWorker();
  if (!reg) return "failed";

  let sub = await reg.pushManager.getSubscription();
  if (sub && !subscriptionMatchesKey((sub.options as PushSubscriptionOptions)?.applicationServerKey, serverKey)) {
    await sub.unsubscribe();            // created against a rotated-away key: unusable
    sub = null;
  }
  if (!sub) {
    sub = await reg.pushManager.subscribe({
      userVisibleOnly: true,            // required by Chromium, and true of this product regardless
      applicationServerKey: urlBase64ToUint8Array(serverKey),
    });
  }

  const body = serializeSubscription(sub.toJSON() as never, navigator.userAgent);
  if (!body) return "failed";
  const res = await fetch("/api/push/subscriptions", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(body),
  });
  if (!res.ok) {
    await sub.unsubscribe();            // roll back, so the two sides cannot disagree
    return "failed";
  }
  return "on";
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
    await fetch(`/api/push/subscriptions?endpoint=${encodeURIComponent(sub.endpoint)}`, {
      method: "DELETE",
    });
  } catch {
    /* fall through — the local unsubscribe below still matters */
  }
  try {
    return await sub.unsubscribe();
  } catch {
    return false;
  }
}
