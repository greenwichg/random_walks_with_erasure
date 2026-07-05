/**
 * Pure, dependency-free helpers shared by the content script, the service worker, and the
 * unit tests. No DOM, no `chrome`, no network — just the two decisions the extension makes
 * locally: "is this an article?" and "have we sent this URL recently?". Everything that needs
 * scoring, classification, or recommendation lives in the backend, never here.
 *
 * Loaded three ways: listed before content.js in the manifest (shared isolated-world scope),
 * `importScripts`-ed by the service worker, and `require`-d by common.test.js under Node.
 */

/**
 * Decide whether a page is a news *article* from cheap, standard signals only:
 *   - OpenGraph `og:type == "article"`
 *   - JSON-LD `@type` of Article / NewsArticle / ReportageNewsArticle / etc.
 *   - a fallback `<article>` element *combined with* a plausible headline
 * Section/front pages (which set none of these, or only `og:type=website`) are rejected, so
 * we never fabricate a "read" for a page the user merely browsed past.
 *
 * @param {{ogType?: string|null, ldTypes?: string[], hasArticleTag?: boolean, hasHeadline?: boolean}} sig
 */
function isArticlePage(sig) {
  const ogType = (sig.ogType || "").toLowerCase();
  if (ogType === "article") return true;

  const ld = (sig.ldTypes || []).map((t) => String(t).toLowerCase());
  if (ld.some((t) => t.includes("article") || t === "report" || t.includes("liveblogposting"))) {
    return true;
  }
  // Weakest signal: a real <article> element with a headline present. Requires both so a
  // template that always renders <article> on section pages doesn't trip it.
  return Boolean(sig.hasArticleTag && sig.hasHeadline);
}

/**
 * Normalise a URL to the read's identity for *local* de-duplication: lowercase host, drop a
 * leading `www.`, strip the query and fragment and any trailing slash. Deliberately close to
 * the backend's `canonical_url` so our local skip-set matches what the backend would dedupe —
 * but the backend stays authoritative (its dedup per (user, canonical_url) is permanent; ours
 * is only a short-TTL network optimisation). Returns "" if the URL can't be parsed.
 */
function normalizeReadUrl(url) {
  try {
    const u = new URL(url);
    if (u.protocol !== "http:" && u.protocol !== "https:") return "";
    const host = u.hostname.toLowerCase().replace(/^www\./, "");
    let path = u.pathname.replace(/\/+$/, "");
    return `${u.protocol}//${host}${path}`;
  } catch {
    return "";
  }
}

/**
 * Local de-dup decision: should we send `url` given a cache of `{normalizedUrl: sentAtMs}`?
 * True when we've never sent it, or the last send is older than `ttlMs`. Pure — the caller
 * owns loading/saving the cache.
 *
 * @param {Record<string, number>} cache
 * @param {string} normalizedUrl
 * @param {number} nowMs
 * @param {number} ttlMs
 */
function shouldSend(cache, normalizedUrl, nowMs, ttlMs) {
  if (!normalizedUrl) return false;
  const last = cache[normalizedUrl];
  return typeof last !== "number" || nowMs - last >= ttlMs;
}

/**
 * Drop cache entries older than `ttlMs` and keep only the newest `max`, so the local skip-set
 * stays bounded and privacy-light (it never grows without limit or persists stale history).
 */
function pruneCache(cache, nowMs, ttlMs, max = 500) {
  const fresh = Object.entries(cache).filter(([, ts]) => nowMs - ts < ttlMs);
  fresh.sort((a, b) => b[1] - a[1]);
  return Object.fromEntries(fresh.slice(0, max));
}

// Export for Node tests; harmless no-op in the browser (module is undefined there).
if (typeof module !== "undefined" && module.exports) {
  module.exports = { isArticlePage, normalizeReadUrl, shouldSend, pruneCache };
}
