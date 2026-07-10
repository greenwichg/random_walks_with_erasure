/**
 * Unit tests for the extension's pure logic (run: `node --test extension/`). These cover the
 * only real decisions the extension makes locally — article detection and local de-dup — while
 * the Chrome-API glue (content script, service worker, options) is exercised end-to-end against
 * the running stack (see extension/README.md → "End-to-end verification").
 */
const { test } = require("node:test");
const assert = require("node:assert");
const { isArticlePage, collectArticleMeta, normalizeReadUrl, shouldSend, pruneCache, configStatus,
        readsErrorReason } = require("./common.js");

test("isArticlePage — positive signals", () => {
  assert.equal(isArticlePage({ ogType: "article" }), true);
  assert.equal(isArticlePage({ ogType: "Article" }), true); // case-insensitive
  assert.equal(isArticlePage({ ldTypes: ["NewsArticle"] }), true);
  assert.equal(isArticlePage({ ldTypes: ["ReportageNewsArticle"] }), true);
  assert.equal(isArticlePage({ ldTypes: ["Report"] }), true);
  assert.equal(isArticlePage({ hasArticleTag: true, hasHeadline: true }), true);
});

test("isArticlePage — section/front pages are rejected", () => {
  assert.equal(isArticlePage({ ogType: "website" }), false);
  assert.equal(isArticlePage({ ldTypes: ["WebSite", "Organization"] }), false);
  assert.equal(isArticlePage({ hasArticleTag: true, hasHeadline: false }), false); // <article> alone
  assert.equal(isArticlePage({}), false);
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
