/**
 * Unit tests for the extension's pure logic (run: `node --test extension/`). These cover the
 * only real decisions the extension makes locally — article detection and local de-dup — while
 * the Chrome-API glue (content script, service worker, options) is exercised end-to-end against
 * the running stack (see extension/README.md → "End-to-end verification").
 */
const { test } = require("node:test");
const assert = require("node:assert");
const { isArticlePage, normalizeReadUrl, shouldSend, pruneCache } = require("./common.js");

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
