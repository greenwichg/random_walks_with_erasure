/**
 * What the service worker is allowed to touch — the single most dangerous decision in the PWA,
 * extracted so it can be tested without a browser.
 *
 * A `fetch` handler sits in front of EVERY request the app makes. Get it wrong and the failure is
 * not a missing feature, it is the site: a worker that answers a request it should have declined
 * serves one reader another reader's data, or serves yesterday's news as today's. That is why the
 * rule here is *decline by default* and the allowed set is one line long.
 *
 * The worker may intervene in exactly one case: **a same-origin GET navigation** — a reader typing
 * a URL or following a link — and even then only to serve the offline shell after the network has
 * already failed. Everything else is not merely un-cached; it is never passed to `respondWith` at
 * all, so the browser performs it exactly as it would with no worker installed.
 *
 * `public/sw.js` carries a copy of `swShouldHandle`'s logic because it is served verbatim and no
 * bundler touches it. `sw-fetch-policy.test.ts` asserts the two agree, by reading the worker source.
 */

export interface SwRequestFacts {
  /** Absolute request URL. */
  url: string;
  /** HTTP method, any case. */
  method: string;
  /** `Request.mode` — "navigate" for a document load. */
  mode?: string;
  /** `Request.destination` — "document" for a page. */
  destination?: string;
  /** Whether the request carries credentials the worker must not see reused. */
  hasAuthorization?: boolean;
  /** The origin the worker itself is served from. */
  origin: string;
}

/**
 * `true` only for requests the worker may call `respondWith` on.
 *
 * Every clause is a refusal, and they are ordered cheapest-first. A request that fails ANY of them
 * is passed through untouched.
 */
export function swShouldHandle(req: SwRequestFacts): boolean {
  // 1. GET only. A POST/PATCH/DELETE is a write — a settings save, a read being recorded, an auth
  //    callback. A worker has no business replaying or answering one.
  if ((req.method || "").toUpperCase() !== "GET") return false;

  let target: URL;
  try {
    target = new URL(req.url);
  } catch {
    return false; // unparseable is not a thing we serve
  }

  // 2. Same-origin only. Cross-origin is publisher images, Google's OAuth endpoints, avatars. The
  //    worker must not sit between the reader and any of them.
  if (target.origin !== req.origin) return false;

  // 3. Never /api/*. This is the whole personalised surface — recommendations, reading history,
  //    settings, notifications, the session — plus NextAuth's own routes under /api/auth. Caching
  //    any of it would be a data-leak bug; intercepting it at all is an availability bug waiting
  //    to happen. The prefix check covers /api and /api/… but not /apiary.
  if (target.pathname === "/api" || target.pathname.startsWith("/api/")) return false;

  // 4. Never a request carrying an Authorization header. Redundant with (3) today and kept anyway:
  //    it is the invariant that stays true if an authenticated endpoint ever moves off /api.
  if (req.hasAuthorization) return false;

  // 5. Navigations only. Not scripts, styles, fonts, or images — Next.js already fingerprints and
  //    far-future-caches its own assets through the HTTP cache, which is better at this than we
  //    would be and does not go stale across a deploy.
  const isNavigation = req.mode === "navigate" || req.destination === "document";
  if (!isNavigation) return false;

  return true;
}

/**
 * Paths precached at install so the offline shell can be served without a network round trip.
 * Deliberately tiny and entirely impersonal: a static route and the icons that render it.
 */
export const SW_PRECACHE = ["/offline", "/site.webmanifest", "/icon.svg"] as const;

/** Bumping this evicts every previous cache on activate — see `sw.js`'s activate handler. */
export const SW_CACHE_VERSION = "ih-shell-v1";
