import { test } from "node:test";
import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import { join } from "node:path";
import { swShouldHandle, SW_PRECACHE, type SwRequestFacts } from "./sw-fetch-policy.ts";

/**
 * The service worker's `fetch` handler is the highest-risk code in the app: it sits in front of
 * every request the browser makes, and a mistake there does not break a feature, it breaks the
 * site — or worse, serves one reader something that belonged to another.
 *
 * So the policy is *decline by default*, and this file is the proof. Every case below is a request
 * the worker must NOT answer, plus the one narrow case it may.
 */

const ORIGIN = "https://hidden-view.com";
const nav = (over: Partial<SwRequestFacts> = {}): SwRequestFacts => ({
  url: `${ORIGIN}/report`,
  method: "GET",
  mode: "navigate",
  destination: "document",
  origin: ORIGIN,
  ...over,
});

test("the one thing it may handle: a same-origin GET navigation", () => {
  assert.equal(swShouldHandle(nav()), true);
  assert.equal(swShouldHandle(nav({ url: `${ORIGIN}/` })), true);
  assert.equal(swShouldHandle(nav({ mode: undefined, destination: "document" })), true);
});

test("never /api/* — the entire personalised surface", () => {
  // Recommendations, reading history, settings, notifications, and NextAuth's own routes all live
  // here. Caching any of it would be a data-leak bug; intercepting it at all is an availability bug.
  for (const path of [
    "/api/me",
    "/api/me/settings",
    "/api/me/notifications",
    "/api/recommendations",
    "/api/stories",
    "/api/auth/session",
    "/api/auth/callback/google",
    "/api/bootstrap",
    "/api",
  ]) {
    assert.equal(swShouldHandle(nav({ url: ORIGIN + path })), false, `${path} must pass through`);
  }
});

test("/apiary is not /api — the prefix check is on a path boundary", () => {
  // A sloppy `startsWith("/api")` would decline a legitimate page. Not a route today; the point is
  // that the rule is written on segment boundaries rather than string prefixes.
  assert.equal(swShouldHandle(nav({ url: `${ORIGIN}/apiary` })), true);
});

test("never a non-GET — those are writes", () => {
  for (const method of ["POST", "PUT", "PATCH", "DELETE", "HEAD", "post"]) {
    assert.equal(swShouldHandle(nav({ method })), false, `${method} must pass through`);
  }
});

test("never cross-origin", () => {
  // Publisher images, Google's OAuth endpoints, avatars. The worker must not sit between the
  // reader and any of them.
  for (const url of [
    "https://accounts.google.com/o/oauth2/v2/auth",
    "https://www.foxnews.com/politics/x",
    "http://hidden-view.com/report", // different scheme is a different origin
    "https://cdn.hidden-view.com/img.png",
  ]) {
    assert.equal(swShouldHandle(nav({ url })), false, `${url} must pass through`);
  }
});

test("never a request carrying credentials", () => {
  assert.equal(swShouldHandle(nav({ hasAuthorization: true })), false);
});

test("never a sub-resource — only whole-page navigations", () => {
  // Next.js fingerprints and far-future-caches its own assets over HTTP, which is better than
  // anything we would do here and does not go stale across a deploy.
  for (const destination of ["script", "style", "image", "font", "fetch", "audio"]) {
    assert.equal(
      swShouldHandle(nav({ mode: "cors", destination })),
      false,
      `${destination} must pass through`,
    );
  }
});

test("a malformed URL is declined rather than thrown on", () => {
  assert.equal(swShouldHandle(nav({ url: "not a url" })), false);
});

test("nothing personal is precached", () => {
  // The cache holds a static page and two icons. If this list ever grows to include a route that
  // renders a reader's data, that data is frozen in their browser until the next deploy.
  assert.deepEqual([...SW_PRECACHE], ["/offline", "/site.webmanifest", "/icon.svg"]);
  for (const path of SW_PRECACHE) {
    assert.equal(swShouldHandle(nav({ url: ORIGIN + path, mode: "cors", destination: "fetch" })), false);
  }
});

/**
 * `public/sw.js` is served verbatim — no bundler touches it — so it carries its own copy of this
 * logic. A copy that drifts is worse than no test at all, so read the worker and check its rules
 * are still the ones asserted above.
 */
test("the worker's own copy of the policy has not drifted", () => {
  const sw = readFileSync(join(import.meta.dirname, "..", "public", "sw.js"), "utf8");
  const fn = sw.slice(sw.indexOf("function shouldHandle"));
  assert.ok(fn.startsWith("function shouldHandle"), "sw.js must define shouldHandle");
  const body = fn.slice(0, fn.indexOf("\n}"));

  for (const [rule, needle] of [
    ["GET only", 'request.method !== "GET"'],
    ["same-origin only", "self.location.origin"],
    ["no /api/*", 'startsWith("/api/")'],
    ["no Authorization", 'get("authorization")'],
    ["navigations only", 'request.mode === "navigate"'],
  ] as const) {
    assert.ok(body.includes(needle), `sw.js lost its "${rule}" rule (${needle})`);
  }

  // And the handler must decline by NOT calling respondWith, rather than answering with a
  // pass-through fetch — an important difference: a pass-through still routes every request
  // through the worker thread, which is a latency and failure surface for no benefit.
  assert.match(
    sw,
    /if \(!shouldHandle\(event\.request\)\) return;/,
    "sw.js must return early — never respondWith — for a request it declines",
  );
});

test("the worker keeps a kill switch, and it is off", () => {
  const sw = readFileSync(join(import.meta.dirname, "..", "public", "sw.js"), "utf8");
  assert.match(sw, /const SELF_DESTRUCT = false;/, "the kill switch must exist and ship disabled");
  assert.match(sw, /self\.registration\.unregister\(\)/, "there must be an unregister path");
  assert.match(sw, /"ih-unregister"/, "the page must be able to tear the worker down without a deploy");
});

test("a shell-cache bump never evicts push's language store", () => {
  // `ih-prefs-v1` belongs to push and survives a push TTL of hours; deleting it on a shell version
  // bump would silently reset every reader's notification language to the default.
  const sw = readFileSync(join(import.meta.dirname, "..", "public", "sw.js"), "utf8");
  assert.match(sw, /startsWith\("ih-shell-"\)/, "cleanup must be scoped to shell caches by prefix");
  assert.ok(!/n !== SHELL_CACHE.*ih-prefs/s.test(sw), "ih-prefs must not be in the eviction set");
});
