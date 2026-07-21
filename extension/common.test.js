/**
 * Unit tests for the extension's pure logic (run: `node --test extension/`). These cover the
 * only real decisions the extension makes locally — article detection and local de-dup — while
 * the Chrome-API glue (content script, service worker, options) is exercised end-to-end against
 * the running stack (see extension/README.md → "End-to-end verification").
 */
const { test } = require("node:test");
const assert = require("node:assert");
const { isArticlePage, classifyPage, collectArticleMeta, normalizeReadUrl, shouldSend, pruneCache,
        configStatus, readsErrorReason, CAPTURE_MATCHES, CAPTURE_EXCLUDES } = require("./common.js");

test("classifyPage — strong signals accept with the right telemetry label", () => {
  assert.deepEqual(classifyPage({ ogType: "article" }), { article: true, signal: "og:type" });
  assert.deepEqual(classifyPage({ ogType: "Article" }), { article: true, signal: "og:type" }); // case-insensitive
  assert.deepEqual(classifyPage({ ldTypes: ["NewsArticle"] }), { article: true, signal: "jsonld" });
  assert.deepEqual(classifyPage({ ldTypes: ["ReportageNewsArticle"] }), { article: true, signal: "jsonld" });
  assert.deepEqual(classifyPage({ ldTypes: ["OpinionNewsArticle"] }), { article: true, signal: "jsonld" }); // *NewsArticle subtree
  assert.deepEqual(classifyPage({ ldTypes: ["BlogPosting"] }), { article: true, signal: "jsonld" }); // Substack/blogs
  assert.deepEqual(classifyPage({ ldTypes: ["LiveBlogPosting"] }), { article: true, signal: "jsonld" });
  assert.deepEqual(classifyPage({ ldTypes: ["Report"] }), { article: true, signal: "jsonld" });
});

test("classifyPage — narrow published_time fallback (recovers minimal blogs, needs a headline)", () => {
  assert.deepEqual(classifyPage({ hasArticlePublishedTime: true, hasHeadline: true }),
                   { article: true, signal: "published_time" });
  assert.equal(classifyPage({ hasArticlePublishedTime: true, hasHeadline: false }).article, false); // no headline
});

test("classifyPage — the bare <article>+<h1> DOM heuristic is REMOVED (open-web false positives)", () => {
  // The single most important regression: GitHub/Wikipedia/SO/marketing pages all render <article>+<h1>.
  assert.equal(isArticlePage({ hasArticleTag: true, hasHeadline: true }), false);
});

test("classifyPage — non-article og:type is a hard reject, overriding stray Article JSON-LD", () => {
  for (const t of ["website", "profile", "product", "book", "game", "place", "video.other", "music.song"]) {
    assert.deepEqual(classifyPage({ ogType: t }), { article: false, signal: "nonarticle-og" }, t);
  }
  // a section page that mis-emits NewsArticle JSON-LD but declares og:type=website is still rejected
  assert.equal(classifyPage({ ogType: "website", ldTypes: ["NewsArticle"] }).article, false);
});

test("classifyPage — metadata-less pages reject as 'no-signal' (documented accepted limitation)", () => {
  assert.deepEqual(classifyPage({}), { article: false, signal: "no-signal" });
  assert.deepEqual(classifyPage({ ldTypes: ["WebPage", "Organization"] }), { article: false, signal: "no-signal" });
  assert.deepEqual(classifyPage({ ldTypes: ["QAPage"] }), { article: false, signal: "no-signal" });
});

// -------- Regression corpus: platform-accurate signal profiles across every requested class -------- //
// Outcomes mirror the compatibility report (docs/EXTENSION_ARTICLE_CAPTURE.md). Signals are what each
// PLATFORM emits on a typical article page; this pins precision (no non-article leaks) and recall
// (every metadata-emitting class captured) so a future detector edit can't silently regress either.
const CORPUS = [
  // class, label, signals, expectArticle
  ["major-intl", "BBC/Reuters/Guardian/NYT/WaPo/CNN/AJE…", { ogType: "article", ldTypes: ["NewsArticle"] }, true],
  ["regional", "Boston Globe / Texas Tribune (Newspack)", { ogType: "article", ldTypes: ["Article"] }, true],
  ["regional", "Richmond Times-Dispatch (TownNews/BLOX)", { ogType: "article", ldTypes: ["NewsArticle"] }, true],
  ["substack", "ACX / Platformer / Free Press", { ogType: "article", ldTypes: ["NewsArticle"] }, true],
  ["ghost", "404 Media / Tangle / ghost.org", { ogType: "article", ldTypes: ["Article"] }, true],
  ["wordpress", "TechCrunch / WP+Yoast", { ogType: "article", ldTypes: ["Article", "WebPage"] }, true],
  ["wordpress", "WP+RankMath", { ogType: "article", ldTypes: ["NewsArticle"] }, true],
  ["medium", "Medium publications (no JSON-LD, og only)", { ogType: "article", ldTypes: [] }, true],
  ["multilingual", "Le Monde/Spiegel/El País/AJ-ar/Asahi", { ogType: "article", ldTypes: ["NewsArticle"] }, true],
  ["indie-blog", "Jekyll+seo-tag / Hugo PaperMod (BlogPosting)", { ogType: "article", ldTypes: ["BlogPosting"] }, true],
  ["indie-blog", "minimal blog stamping article:published_time", { hasArticlePublishedTime: true, hasHeadline: true }, true],
  // documented limitation — metadata-less pages (bare CMS / no-SEO blogs) are NOT captured:
  ["limitation", "Daring Fireball / bare Hugo / hand-rolled CMS", { hasArticleTag: true, hasHeadline: true }, false],
  // negatives — news-site non-articles:
  ["neg-news", "homepage / section / category", { ogType: "website" }, false],
  ["neg-news", "author / tag page", { ogType: "profile" }, false],
  ["neg-news", "on-site search results", { ogType: "website", ldTypes: ["SearchResultsPage"] }, false],
  // negatives — the "Should NOT ingest" web:
  ["neg-web", "Amazon product", { ogType: "product", ldTypes: ["Product"] }, false],
  ["neg-web", "YouTube watch", { ogType: "video.other", ldTypes: ["VideoObject"] }, false],
  ["neg-web", "YouTube home / X / Reddit / marketing", { ogType: "website" }, false],
  ["neg-web", "GitHub repo README", { ogType: "object", hasArticleTag: true, hasHeadline: true }, false],
  ["neg-web", "Stack Overflow question", { ogType: "website", ldTypes: ["QAPage"] }, false],
  ["neg-web", "Gmail / Docs / dashboard / bank / Wikipedia (no signal)", { hasHeadline: true }, false],
];

test("detector regression corpus — precision (no non-article leaks) and recall (every metadata class)", () => {
  const leaks = [], misses = [];
  for (const [cls, label, sig, expect] of CORPUS) {
    const got = isArticlePage(sig);
    if (got && !expect) leaks.push(`${cls}: ${label}`);
    if (!got && expect) misses.push(`${cls}: ${label}`);
  }
  assert.deepEqual(leaks, [], "non-article pages must never be classified as articles");
  assert.deepEqual(misses, [], "every metadata-emitting article class must be captured");
});

test("capture scope — HTTPS-only, with a short sensitive-origin exclude list", () => {
  assert.deepEqual(CAPTURE_MATCHES, ["https://*/*"]);           // no http://
  assert.ok(CAPTURE_EXCLUDES.every((p) => p.startsWith("https://")));
  assert.ok(CAPTURE_EXCLUDES.includes("https://mail.google.com/*"));
});

test("normalizeReadUrl — canonical-ish identity for local de-dup", () => {
  assert.equal(
    normalizeReadUrl("https://WWW.NYTimes.com/2024/us/politics/x/?utm=1#top"),
    "https://nytimes.com/2024/us/politics/x",
  );
  assert.equal(normalizeReadUrl("https://www.cnn.com/a/"), "https://cnn.com/a");
  assert.equal(normalizeReadUrl("javascript:alert(1)"), ""); // non-http scheme rejected
  assert.equal(normalizeReadUrl("not a url"), "");
});

test("shouldSend — TTL de-dup decision", () => {
  const now = 1_000_000;
  const ttl = 6 * 60 * 60 * 1000;
  assert.equal(shouldSend({}, "https://x.com/a", now, ttl), true); // never seen
  assert.equal(shouldSend({ "https://x.com/a": now }, "https://x.com/a", now, ttl), false); // fresh
  assert.equal(shouldSend({ "https://x.com/a": now - ttl }, "https://x.com/a", now, ttl), true); // expired
  assert.equal(shouldSend({}, "", now, ttl), false); // empty (unparseable) url
});

test("configStatus — reports the exact missing/invalid piece, never silently ok", () => {
  assert.equal(configStatus({ appUrl: "https://app.example.com", token: "ih_abc" }), "ok");
  assert.equal(configStatus({ appUrl: "http://localhost:3000", token: "ih_abc" }), "ok");
  assert.equal(configStatus({ token: "ih_abc" }), "no-url");
  assert.equal(configStatus({ appUrl: "https://app.example.com" }), "no-token");
  assert.equal(configStatus({ appUrl: "  ", token: "  " }), "no-url"); // whitespace-only
  assert.equal(configStatus({ appUrl: "not a url", token: "ih_abc" }), "bad-url");
  assert.equal(configStatus({ appUrl: "ftp://x.com", token: "ih_abc" }), "bad-url"); // wrong scheme
  assert.equal(configStatus({}), "no-url");
});

test("pruneCache — drops stale entries and caps size", () => {
  const now = 1_000_000;
  const ttl = 1000;
  const pruned = pruneCache({ fresh: now - 10, stale: now - 5000 }, now, ttl);
  assert.deepEqual(Object.keys(pruned), ["fresh"]);

  const many = {};
  for (let i = 0; i < 20; i++) many[`u${i}`] = now - i; // u0 newest
  const capped = pruneCache(many, now, 10 * 60 * 1000, 5);
  assert.equal(Object.keys(capped).length, 5);
  assert.ok(Object.keys(capped).includes("u0")); // newest retained
  assert.ok(!Object.keys(capped).includes("u19")); // oldest dropped
});

test("readsErrorReason — distinguishes token / url / backend failures", () => {
  assert.equal(readsErrorReason(401), "bad-token"); // invalid or expired token
  assert.equal(readsErrorReason(403), "bad-token"); // unauthorized
  assert.equal(readsErrorReason(404), "wrong-url"); // reached a server, not InfoDiet
  assert.equal(readsErrorReason(500), "unavailable"); // backend up but erroring
  assert.equal(readsErrorReason(503), "unavailable");
  assert.equal(readsErrorReason(418), "status-418"); // unexpected status is surfaced verbatim
});

// ---- collectArticleMeta (Commit 18: the extension as a catalog producer) ----
test("collectArticleMeta picks og tags with fallbacks and validates the image URL", () => {
  const metas = {
    'meta[property="og:title"]': "  OG Title  ",
    'meta[property="og:description"]': "A one-line abstract",
    'meta[property="og:image"]': "https://cdn.example.com/hero.jpg",
    'meta[property="og:site_name"]': "The Example Times",
    'meta[property="article:published_time"]': "2026-07-10T08:00:00Z",
    'meta[name="author"]': "A. Reporter",
  };
  const m = collectArticleMeta((sel) => metas[sel] || null,
                               { docTitle: "Doc Title", docLang: "en-US" });
  assert.equal(m.title, "OG Title");
  assert.equal(m.description, "A one-line abstract");
  assert.equal(m.image, "https://cdn.example.com/hero.jpg");
  assert.equal(m.siteName, "The Example Times");
  assert.equal(m.publishedAt, "2026-07-10T08:00:00Z");
  assert.equal(m.author, "A. Reporter");
  assert.equal(m.language, "en-US");
});

test("collectArticleMeta falls back to document title and drops non-http images", () => {
  const m = collectArticleMeta((sel) =>
    sel === 'meta[property="og:image"]' ? "data:image/png;base64,xxxx" : null,
    { docTitle: "Fallback Headline", docLang: "" });
  assert.equal(m.title, "Fallback Headline");
  assert.equal(m.image, "");                      // data:/relative images are never forwarded
  assert.equal(m.description, "");
  assert.equal(m.language, "");
});

test("collectArticleMeta tolerates a page with no metadata at all", () => {
  const m = collectArticleMeta(() => null, {});
  assert.deepEqual(m, { title: "", description: "", image: "", siteName: "",
                        publishedAt: "", author: "", language: "" });
});
