/**
 * Pure, dependency-free helpers shared by the content script, the service worker, and the
 * unit tests. No DOM, no `chrome`, no network — just the two decisions the extension makes
 * locally: "is this an article?" and "have we sent this URL recently?". Everything that needs
 * scoring, classification, or recommendation lives in the backend, never here.
 *
 * Loaded three ways: listed before content.js in the manifest (shared isolated-world scope),
 * `importScripts`-ed by the service worker, and `require`-d by common.test.js under Node.
 */

// --- Strengthened, precision-first article detection (metadata-only) -------------------------- //
// When the extension can run web-wide (any HTTPS page), `isArticlePage` is the SOLE guard that
// separates the "Should ingest" set (genuine articles, any publisher) from the "Should NOT ingest"
// set (search, category, home, product, video, webmail, dashboards, docs). It therefore relies only
// on strong, publisher-declared structured signals — never the old `<article>+<h1>` DOM heuristic,
// which self-identifies on GitHub/Wikipedia/Stack Overflow/marketing pages and would false-positive
// across the open web. We read STANDARD METADATA ONLY and never inspect article body text.

/** JSON-LD `@type`s (lower-cased) that denote an article. Exact set + the whole *NewsArticle subtree
 *  (endsWith check), so Opinion/Analysis/Reportage/… subtypes and BlogPosting (Substack/blogs) match,
 *  while WebPage / SearchResultsPage / QAPage / Product / VideoObject do not. */
const _LD_ARTICLE_EXACT = new Set([
  "article", "blogposting", "liveblogposting", "report", "reportagenewsarticle",
]);
/** OpenGraph `og:type`s that a publisher uses to declare a page is NOT an article. An explicit
 *  non-article declaration overrides any stray Article JSON-LD (precision over recall). */
const _OG_NONARTICLE = new Set(["website", "profile", "product", "book", "game", "place"]);

/**
 * Classify a page from standard metadata, returning both the decision and the (closed-set) signal
 * or rejection reason — the reason doubles as the anonymous detection-telemetry label (no URL, no
 * free text). Decision order (precision-first):
 *   1. `og:type=article`                         → accept  (signal "og:type")   — the near-universal signal
 *   2. `og:type` present & a non-article type    → reject  (reason "nonarticle-og")
 *   3. an Article-family JSON-LD `@type`         → accept  (signal "jsonld")
 *   4. `article:published_time` + a headline     → accept  (signal "published_time")  — narrow corroborated fallback
 *   5. otherwise                                 → reject  (reason "no-signal")
 * A page with no standard article metadata at all (reason "no-signal") is a documented, accepted
 * limitation: it is indistinguishable from a non-article without reading body text, which we never do.
 *
 * @param {{ogType?: string|null, ldTypes?: string[], hasArticlePublishedTime?: boolean, hasHeadline?: boolean}} sig
 * @returns {{article: boolean, signal: "og:type"|"jsonld"|"published_time"|"nonarticle-og"|"no-signal"}}
 */
function classifyPage(sig) {
  // Normalize before comparing: trim whitespace, then lower-case — so a padded `og:type=" article "`
  // or a JSON-LD `@type` of `"NewsArticle "` is not misclassified (F4).
  const ogType = (sig.ogType || "").trim().toLowerCase();
  if (ogType === "article") return { article: true, signal: "og:type" };
  if (ogType && (_OG_NONARTICLE.has(ogType) || ogType.startsWith("video.") || ogType.startsWith("music.")))
    return { article: false, signal: "nonarticle-og" };

  const ld = (sig.ldTypes || []).map((t) => String(t).trim().toLowerCase());
  if (ld.some((t) => t.endsWith("newsarticle") || _LD_ARTICLE_EXACT.has(t)))
    return { article: true, signal: "jsonld" };

  // Narrow corroborated fallback: a CMS that stamps an OpenGraph article publish-time is declaring an
  // article; require a headline too. Recovers minimal blogs that emit `article:*` meta but no og:type,
  // without the open-web false positives of a bare `<article>+<h1>` test.
  if (sig.hasArticlePublishedTime && sig.hasHeadline)
    return { article: true, signal: "published_time" };

  return { article: false, signal: "no-signal" };
}

/** Boolean convenience wrapper over {@link classifyPage} (unchanged call-site contract). */
function isArticlePage(sig) {
  return classifyPage(sig).article;
}

// --- Capture scope (dynamic content-script registration) -------------------------------------- //
// HTTPS-only (http news is effectively extinct; tighter surface). The detector runs on every page in
// scope but only *sends* on a positive classification. `CAPTURE_EXCLUDES` is defence-in-depth, NOT a
// security boundary — it keeps the detector off a few mainstream high-sensitivity origins even after
// the user opts in; the real guarantees are opt-in + metadata-only + positive-detection-only.
const CAPTURE_ORIGIN = "https://*/*";
const CAPTURE_MATCHES = ["https://*/*"];
const CAPTURE_EXCLUDES = [
  "https://mail.google.com/*", "https://docs.google.com/*", "https://drive.google.com/*",
  "https://calendar.google.com/*", "https://meet.google.com/*",
  "https://outlook.live.com/*", "https://outlook.office.com/*", "https://*.office.com/*",
];

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
 * Collect the standard page metadata an article read may carry to the backend (Commit 18 — the
 * extension as a catalog producer). Same privacy stance as detection: OpenGraph / standard meta
 * only, never article text. Pure: `metaContent` is the caller's selector→content lookup and
 * `page` carries the two non-meta fallbacks, so Node tests can drive it without a DOM.
 *
 * @param {(selector: string) => string|null} metaContent
 * @param {{docTitle?: string, docLang?: string}} [page]
 */
function collectArticleMeta(metaContent, page) {
  const pick = (...sels) => {
    for (const s of sels) {
      const v = metaContent(s);
      if (v && v.trim()) return v.trim();
    }
    return "";
  };
  let image = pick('meta[property="og:image"]', 'meta[property="og:image:url"]');
  try {
    const u = new URL(image);                        // forward only an absolute http(s) image URL
    if (u.protocol !== "http:" && u.protocol !== "https:") image = "";
  } catch {
    image = "";
  }
  return {
    title: pick('meta[property="og:title"]') || ((page && page.docTitle) || "").trim(),
    description: pick('meta[property="og:description"]', 'meta[name="description"]'),
    image,
    siteName: pick('meta[property="og:site_name"]'),
    publishedAt: pick('meta[property="article:published_time"]', 'meta[name="article:published_time"]'),
    author: pick('meta[name="author"]', 'meta[property="article:author"]'),
    language: ((page && page.docLang) || "").trim(),
  };
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

/**
 * Validate the stored connection config, returning "ok" or a specific reason. The Options page, the
 * service worker, and the tests all use this one function, so an incomplete configuration surfaces
 * the same clear cause everywhere instead of failing silently.
 *
 * @param {{appUrl?: string, token?: string}} cfg
 * @returns {"ok"|"no-url"|"no-token"|"bad-url"}
 */
function configStatus(cfg) {
  const appUrl = ((cfg && cfg.appUrl) || "").trim();
  const token = ((cfg && cfg.token) || "").trim();
  if (!appUrl) return "no-url";
  if (!token) return "no-token";
  try {
    const u = new URL(appUrl);
    if (u.protocol !== "http:" && u.protocol !== "https:") return "bad-url";
  } catch {
    return "bad-url";
  }
  return "ok";
}

/**
 * Map a reads-sync HTTP status to a stable failure reason the UI can explain clearly, so a failed
 * sync never looks the same as an idle one:
 *   - 401 / 403 → "bad-token"  : the API token is invalid or expired — regenerate it.
 *   - 404       → "wrong-url"   : reached a server, but not InfoDiet (no /api/me/reads) — check the URL.
 *   - >= 500    → "unavailable" : the backend is up but erroring.
 *   - else      → "status-<n>"  : an unexpected status.
 * A network-level failure (fetch threw: DNS, refused, timeout) is "unreachable", decided by the
 * caller (there is no HTTP status in that case).
 *
 * @param {number} status
 * @returns {"bad-token"|"wrong-url"|"unavailable"|string}
 */
function readsErrorReason(status) {
  if (status === 401 || status === 403) return "bad-token";
  if (status === 404) return "wrong-url";
  if (status >= 500) return "unavailable";
  return `status-${status}`;
}

// Export for Node tests + the local detector-verification utility; harmless no-op in the browser.
if (typeof module !== "undefined" && module.exports) {
  module.exports = { isArticlePage, classifyPage, collectArticleMeta, normalizeReadUrl, shouldSend,
                     pruneCache, configStatus, readsErrorReason,
                     CAPTURE_ORIGIN, CAPTURE_MATCHES, CAPTURE_EXCLUDES };
}
