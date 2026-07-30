/* eslint-disable no-undef */
/**
 * Hidden View service worker — Phase B2 (Push Delivery).
 *
 * Renders a delivered notification from the payload it was handed, with **no network call before
 * `showNotification()`**. That is architecture §2 P4 and it is the constraint the whole file is shaped
 * by: a worker that receives a push and does not show something makes the browser display its own
 * message ("This site has been updated in the background"), so every failure here is user-visible and
 * worse than silence. Nothing in the render path may depend on the network, on authentication, or on
 * storage that might be absent.
 *
 * Everything it needs is either in the message or in `sw-data.js`, generated at build time from
 * `lib/notification-kinds.ts` and `messages/*.json` — a derived copy, which §8 distinguishes from a
 * duplicated one. `importScripts` because this file is served verbatim and no bundler touches it.
 *
 * Rendering policy is deliberately NOT the inbox's (§3): an unknown kind degrades to app-level copy
 * that still navigates, where the inbox degrades to an inert row. A push has already interrupted the
 * reader; a dead tap would be the second insult.
 */

importScripts("/sw-data.js");

const LANG_CACHE = "ih-prefs-v1";
const LANG_URL = "/__ih/lang"; // synthetic key; never fetched from the network
// From sw-data.js, so the supported set cannot drift from the app's.
const SUPPORTED = self.IH_LANGS;
const DEFAULT_LANG = self.IH_DEFAULT_LANG;

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

/** §4's resolution order: stored → payload → default. Mirrors `lib/push.ts::resolveLang`. */
async function resolveLang(payloadLang) {
  return (await readStoredLang()) || normalizeLang(payloadLang) || DEFAULT_LANG;
}

// --------------------------------------------------------------------------------------------- //
// Push. Three obligations, in order: ALWAYS render (§2 P4), never a raw i18n key, always tappable
// (§6). Every branch below ends in a notification — there is no input that produces silence.
// --------------------------------------------------------------------------------------------- //
function safeParse(event) {
  try {
    return event.data ? event.data.json() : null;
  } catch {
    return null; // a payload we cannot read is still a push we must render
  }
}

/** Interpolate `{name}` placeholders, mirroring `lib/i18n-core.ts`. Missing params are left as-is. */
function interpolate(template, params) {
  if (!params) return template;
  return String(template).replace(/\{(\w+)\}/g, (m, k) =>
    params[k] === undefined || params[k] === null ? m : String(params[k]),
  );
}

/** A catalog lookup with the app's own fallback chain: active language → English → nothing. */
function translate(lang, key, params) {
  if (!key) return null;
  const catalogs = self.IH_MESSAGES || {};
  const hit = (catalogs[lang] || {})[key] ?? (catalogs.en || {})[key];
  // Never a raw key on a lock screen: absent means "render without it", not "render the key".
  return hit === undefined ? null : interpolate(hit, params);
}

/**
 * The payload → what `showNotification` is called with.
 *
 * `href` precedence is §2's rule applied: a worker that KNOWS the kind derives the destination from
 * its own metadata (fresher, and the same answer the inbox gives), and falls back to the server's
 * `href` only for a kind it has never heard of. The payload is a fallback for what the device cannot
 * derive, never an override of what it can.
 */
function renderOptions(data, lang) {
  const kinds = self.IH_KINDS || {};
  const meta = kinds[(data && data.kind) || ""] || self.IH_GENERIC_KIND;
  const known = Object.prototype.hasOwnProperty.call(kinds, (data && data.kind) || "");
  const payload = (data && data.payload) || {};

  const title = translate(lang, meta.titleKey, payload) || "Hidden View";
  const body = translate(lang, meta.bodyKey, payload);

  let href = (data && data.href) || "/";
  if (known) {
    if (meta.deepLinkField && meta.deepLinkPath) {
      const value = payload[meta.deepLinkField];
      href =
        typeof value === "string" && value.trim()
          ? meta.deepLinkPath + encodeURIComponent(value.trim())
          : meta.href || href;
    } else if (meta.href) {
      href = meta.href;
    }
  }

  return [
    title,
    {
      body: body || undefined,
      icon: "/android-chrome-192x192.png",
      badge: "/favicon-32x32.png",
      // The fourth idempotency level: one tag per notification, so a repeat collapses at the OS
      // level instead of stacking a second banner for the same event.
      tag: (data && data.dedupeKey) || undefined,
      timestamp: data && data.createdAt ? Date.parse(data.createdAt) || undefined : undefined,
      data: { href: href, notificationId: data && data.notificationId },
    },
  ];
}

async function render(data) {
  const lang = await resolveLang(data && data.lang);
  const [title, options] = renderOptions(data, lang);
  return self.registration.showNotification(title, options);
}

self.addEventListener("push", (event) => {
  // Unconditionally inside waitUntil: there is no branch of this handler that may end without a
  // notification having been shown — including a payload that will not parse, a kind this build has
  // never heard of, and a schema version from the future (§6). All three land on the generic row.
  event.waitUntil(render(safeParse(event)));
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
