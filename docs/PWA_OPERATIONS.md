# PWA — installability, the service worker, and how to take it back

**Status:** shipped. The app is installable from Chromium-based browsers; iOS gets Add-to-Home-Screen
instructions. `RWE_PUSH_ENABLED` is **unchanged** by any of this.

The reason this document leads with rollback is that a service worker is the one thing a web deploy
ships that **does not go away when you redeploy**. It lives in the reader's browser, survives tab
closes and restarts, and keeps running the code it last installed. A bad worker is the only web bug
that can outlive the fix for it — so the way out is written down before the way in.

---

## 1. What the worker actually does

`public/sw.js` has two tenants:

| Tenant | Since | Events |
|---|---|---|
| Push (Phase B2) | earlier | `push`, `notificationclick`, `pushsubscriptionchange` |
| PWA shell (this) | now | `fetch`, plus its own `install` / `activate` |

They are separate blocks in one file on purpose: **one scope, one worker**. A second worker at the
same scope would replace the first, and push would stop. The PWA block can be deleted whole without
touching a line push depends on — which is tier 1 of the rollback below.

### The fetch handler declines by default

It may answer exactly one kind of request: a **same-origin GET navigation**, and then only with the
static `/offline` page, and then only after the network has already failed. Everything else never
reaches `respondWith` at all, so the browser performs it exactly as it would with no worker
installed:

- **all of `/api/*`** — recommendations, reading history, settings, notifications, the session, and
  NextAuth's own routes;
- **anything cross-origin** — publisher images, Google OAuth, avatars;
- **anything non-GET** — every write;
- **anything carrying `Authorization`**;
- **every sub-resource** — scripts, styles, fonts, images (Next fingerprints and far-future-caches
  those over HTTP, which does not go stale across a deploy).

The cache holds **three files**: `/offline`, `/site.webmanifest`, `/icon.svg`. Nothing personal,
no articles, no news. `lib/sw-fetch-policy.ts` is the policy; `lib/sw-fetch-policy.test.ts` proves
the worker's own copy has not drifted from it.

---

## 2. Rollback — three tiers, cheapest first

### Tier 1 — remove the PWA behaviour, keep the worker (preferred)

Delete the `PWA shell` block at the bottom of `public/sw.js` and deploy. Clients pick up the new
worker on their next navigation; the `fetch` handler is gone, so the worker stops intercepting
anything. Push is untouched. The app simply stops being installable.

Use this for: the fetch handler misbehaving, an offline page that is wrong, anything short of "the
worker itself is broken".

### Tier 2 — tear the worker down from the page (no deploy)

The worker listens for a message and removes itself:

```js
navigator.serviceWorker.controller?.postMessage("ih-unregister");
```

Run in the console of an affected browser, or ship it temporarily in a page effect to reach every
reader. It deletes the `ih-shell-*` caches and calls `registration.unregister()`.

Use this for: a specific reader stuck on a bad worker, or to verify the teardown path works before
you need it.

### Tier 3 — the kill switch (nuclear)

In `public/sw.js`:

```js
const SELF_DESTRUCT = true;
```

Deploy. Every client that picks up this worker deletes **every `ih-` cache** and unregisters itself
on activation, and the `fetch` handler returns immediately, so the worker is inert even before it
finishes tearing down.

> **This takes push down with it.** Unregistering the worker destroys the push subscription: readers
> would have to re-subscribe. Harmless today — production runs `RWE_PUSH_ENABLED=0` and nothing is
> subscribed — but check that flag before using tier 3, and prefer tier 1 whenever it will do.

After the fleet has drained, set it back to `false` and remove the flag in a later commit; a
permanently self-destructing worker means the app can never be installed again.

### Verifying a rollback took

```js
// In the console of a browser that had the worker:
(await navigator.serviceWorker.getRegistrations()).length   // -> 0 once unregistered
await caches.keys()                                         // -> no "ih-shell-*" entries
```

---

## 3. Verifying installability

Do not infer it from a checklist — Chromium's criteria have moved across versions. **Ask the
browser.** With the app running:

```bash
cd web && npx playwright test e2e/specs/pwa-install.spec.ts
```

That drives `Page.getAppManifest` and `Page.getInstallabilityErrors` over CDP and fails with
whatever the browser objected to. In a real browser, DevTools → Application → Manifest shows the
same information with an **Installability** section.

Measured at implementation time, against Chromium via the e2e harness:

```
SERVICE WORKER:          registered, scope "/", state "activated"
CONTROLS PAGE:           true
MANIFEST PARSE ERRORS:   []
INSTALLABILITY ERRORS:   []
OFFLINE SHELL CACHED:    200
```

### Testing offline behaviour — a warning

**Playwright's `context.setOffline(true)` does not reach service-worker fetches.** The worker's own
`fetch()` still succeeds against the real server, so an "offline" test written that way passes
while proving nothing — it was measured returning the live page, not the offline shell.

Test it with a **real outage** instead: stop the web server and navigate to a URL the browser has
never visited (so nothing can come from the HTTP cache).

```
NAVIGATION DURING OUTAGE -> 200 | "You're offline  Hidden View needs a connection…"
/api/me during OUTAGE    -> threw: TypeError   (correct: NOT served from cache)
```

The second line is the one that matters: `/api/*` fails exactly as it would with no worker present.

---

## 4. Manifest facts worth knowing

- **`background_color` is `#131416`**, the dark `--background` the app actually paints. A manifest
  cannot media-query, so this is one colour for both themes; the previous `#ffffff` was a white
  flash before a dark app on every cold launch.
- **`theme_color` is `#463acb`**, the brand purple already used as the icon fill. It does **not**
  need to match `viewport.themeColor` in `app/layout.tsx`, which is per-theme and governs the
  browser chrome; the manifest's governs the installed app's title bar and splash.
- **`id` is `/`** — set explicitly so the app's identity survives a future `start_url` change.
  Without it, changing `start_url` makes browsers treat the result as a *different* app and leave
  the old one installed and orphaned.
- **`start_url` is `/?source=pwa`**, which makes launches from the installed app distinguishable in
  RUM without adding any tracking.
- **The maskable icon** is generated from `icon.svg`'s geometry by `scripts/build-maskable-icon.py`,
  full-bleed (the OS mask supplies the shape) with the glyph inside the 80% safe zone — measured at
  149px extent against a 205px safe radius.
- **Screenshots are real captures** of the signed-in app, not mock-ups. Regenerate them with the
  temporary spec described in that script's header if the UI changes materially.

---

## 5. What this deliberately does not do

- **No `next-pwa` / Workbox.** Either would take over `public/sw.js`, and push was there first.
- **No caching of anything personal** — no articles, no recommendations, no session, no news.
  Offline means "here is a page telling you that you are offline", not "here is stale news".
- **No change to `RWE_PUSH_ENABLED`**, to auth, to OAuth, to notifications, to reading history, to
  recommendation settings, or to polling.
- **No install prompt where the browser has no install path.** Desktop Firefox sees nothing, because
  a banner a reader cannot act on is noise. iOS Safari gets instructions; everything Chromium-based
  gets the real `beforeinstallprompt` dialog.
