/* eslint-disable no-undef */
/**
 * Hidden View service worker — Phase B1 (Browser Push Foundation).
 *
 * This worker exists so a browser can hold a push subscription; it does NOT yet render notifications
 * from the notification metadata table. That is Phase B2, and it will replace `renderGeneric` below
 * with a metadata-driven renderer. See docs/BROWSER_PUSH_ARCHITECTURE.md.
 *
 * WHY THERE IS A `push` HANDLER AT ALL IN A COMMIT THAT SENDS NOTHING. Architecture §2 P4: a worker
 * that receives a push and does not call `showNotification()` makes the browser display its own
 * message ("This site has been updated in the background"). §6 then requires that a worker meeting a
 * kind it does not understand still renders — generically, tappably, never a raw i18n key. A B1
 * worker is exactly "a worker that understands no kinds", so the generic path IS §6's floor rather
 * than an early piece of B2's ceiling. A device that installs this worker today and receives a push
 * from a later deploy is precisely the forward-compatibility case the specification describes, and it
 * behaves correctly.
 *
 * No imports, no bundler: this file is served verbatim from /sw.js so its scope is the whole origin.
 */

const LANG_CACHE = "ih-prefs-v1";
const LANG_URL = "/__ih/lang"; // synthetic key; never fetched from the network
const SUPPORTED = ["en", "es", "fr", "de", "pt"];
const DEFAULT_LANG = "en";

// --------------------------------------------------------------------------------------------- //
// Lifecycle. Take over promptly so a reader who grants permission is not talking to a worker from a
// previous deploy, which is the state §6's backward-compatibility rule exists to survive but which
// there is no reason to prolong.
// --------------------------------------------------------------------------------------------- //
self.addEventListener("install", (event) => {
  event.waitUntil(self.skipWaiting());
});

self.addEventListener("activate", (event) => {
  event.waitUntil(self.clients.claim());
});

// --------------------------------------------------------------------------------------------- //
// Language. Architecture §4: the reader's CURRENT language is authoritative because it is correct at
// render time; the payload's is a fallback because a push can sit under its TTL for hours and a
// language captured at send time can be stale by the time it renders.
//
// The Cache API is the store because it is readable from both the page and a worker with no schema
// and no versioning, and because a worker woken by a push may have no client open to ask. It is
// evictable under storage pressure — which is exactly the empty-store case the payload fallback
// exists for, so the eviction is survivable rather than a defect.
// --------------------------------------------------------------------------------------------- //
function normalizeLang(value) {
  return SUPPORTED.includes(value) ? value : null;
}

async function readStoredLang() {
  try {
    const cache = await caches.open(LANG_CACHE);
    const hit = await cache.match(LANG_URL);
    return hit ? normalizeLang((await hit.text()).trim()) : null;
  } catch {
    return null; // storage unavailable (private mode, quota) — the caller falls back
  }
}

/** §4's resolution order: stored → payload → default. Exported shape mirrors `lib/push-lang.ts`. */
async function resolveLang(payloadLang) {
  return (await readStoredLang()) || normalizeLang(payloadLang) || DEFAULT_LANG;
}

// --------------------------------------------------------------------------------------------- //
// Push. B1's obligation is the §6 floor and nothing more: always render, never a raw key, always
// tappable.
// --------------------------------------------------------------------------------------------- //
function safeParse(event) {
  try {
    return event.data ? event.data.json() : null;
  } catch {
    return null; // a payload we cannot read is still a push we must render
  }
}

/**
 * The generic render. Deliberately carries no per-kind knowledge: B1 has no metadata table, and §6
 * requires this exact behaviour from any worker meeting a kind it does not know.
 *
 * `tag` is the notification's dedupe key when present — the fourth of the platform's four idempotency
 * levels — so a repeat collapses at the OS level instead of stacking.
 */
async function renderGeneric(data) {
  const lang = await resolveLang(data && data.lang);
  const titles = {
    en: "Hidden View", es: "Hidden View", fr: "Hidden View",
    de: "Hidden View", pt: "Hidden View",
  };
  const bodies = {
    en: "You have a new notification.",
    es: "Tienes una nueva notificación.",
    fr: "Vous avez une nouvelle notification.",
    de: "Sie haben eine neue Benachrichtigung.",
    pt: "Você tem uma nova notificação.",
  };
  return self.registration.showNotification(titles[lang] || titles.en, {
    body: bodies[lang] || bodies.en,
    icon: "/android-chrome-192x192.png",
    badge: "/favicon-32x32.png",
    tag: (data && data.dedupeKey) || undefined,
    timestamp: data && data.createdAt ? Date.parse(data.createdAt) || undefined : undefined,
    data: { href: (data && data.href) || "/", notificationId: data && data.notificationId },
  });
}

self.addEventListener("push", (event) => {
  // Unconditionally inside waitUntil: there is no branch of this handler that may end without a
  // notification having been shown.
  event.waitUntil(renderGeneric(safeParse(event)));
});

self.addEventListener("notificationclick", (event) => {
  event.notification.close();
  const href = (event.notification.data && event.notification.data.href) || "/";
  event.waitUntil(
    (async () => {
      // Prefer focusing an open tab over opening a new one: a reader who already has the app open
      // does not want a second copy of it.
      const all = await self.clients.matchAll({ type: "window", includeUncontrolled: true });
      for (const client of all) {
        if (client.url.includes(href) && "focus" in client) return client.focus();
      }
      return self.clients.openWindow(href);
    })(),
  );
});

// --------------------------------------------------------------------------------------------- //
// Subscription refresh. The browser may rotate a subscription on its own schedule and tells the
// worker — not the page, which may not be open. Re-subscribing here and re-registering keeps the
// device reachable; without it the endpoint silently becomes undeliverable and the reader simply
// stops receiving anything.
//
// `newSubscription` is provided by Chromium and absent in some implementations, so the resubscribe is
// attempted from the stored options when it is missing.
// --------------------------------------------------------------------------------------------- //
async function registerSubscription(subscription) {
  if (!subscription) return;
  const json = subscription.toJSON ? subscription.toJSON() : subscription;
  const keys = json.keys || {};
  await fetch("/api/push/subscriptions", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    credentials: "include",
    body: JSON.stringify({
      endpoint: json.endpoint,
      p256dh: keys.p256dh,
      auth: keys.auth,
      expirationTime: json.expirationTime ?? null,
    }),
  });
}

self.addEventListener("pushsubscriptionchange", (event) => {
  event.waitUntil(
    (async () => {
      let next = event.newSubscription;
      if (!next) {
        const old = event.oldSubscription;
        const options = old && old.options ? old.options : null;
        if (options) {
          next = await self.registration.pushManager.subscribe({
            userVisibleOnly: true,
            applicationServerKey: options.applicationServerKey,
          });
        }
      }
      await registerSubscription(next);
    })().catch(() => {
      // A failed re-registration must not throw inside the event: the browser has already rotated
      // the subscription either way, and the page repairs it on the reader's next visit.
    }),
  );
});
