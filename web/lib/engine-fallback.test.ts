/**
 * engine-fallback tests (node --test, type-stripped like the other lib tests). Proves the proxy
 * decision keeps 401 (auth), 503 (unavailable), and a transport failure distinct — the B3 fix that
 * stops Analytics/Profile from collapsing them — and serves a 2xx body (mock only on unavailability).
 */
import { test } from "node:test";
import assert from "node:assert/strict";

import { resolveEngineFallback } from "./engine-fallback.ts";

test("authenticated + healthy backend (a 2xx body) is served as data — even when empty", () => {
  assert.deepEqual(resolveEngineFallback({ status: 200, data: { overall: 72 } }, false),
    { kind: "data", data: { overall: 72 } });
  assert.deepEqual(resolveEngineFallback({ status: 200, data: {} }, false), { kind: "data", data: {} });
  assert.deepEqual(resolveEngineFallback({ status: 200, data: [] }, true), { kind: "data", data: [] });
});

test("anonymous / auth failure (engine 401 or 403) is unauthorized — never mock, never 503", () => {
  // An anonymous caller carries no user header, so the engine returns 401; the proxy surfaces that
  // as a 401 whether or not the dev mock is enabled (an auth failure is not an outage).
  for (const status of [401, 403]) {
    assert.deepEqual(resolveEngineFallback({ status, data: null }, false), { kind: "unauthorized" });
    assert.deepEqual(resolveEngineFallback({ status, data: null }, true), { kind: "unauthorized" });
  }
});

test("backend unavailable (transport failure, status 0) → mock in dev, 503 in prod", () => {
  assert.deepEqual(resolveEngineFallback({ status: 0, data: null }, true), { kind: "mock" });        // dev
  assert.deepEqual(resolveEngineFallback({ status: 0, data: null }, false), { kind: "unavailable" }); // prod
});

test("a 5xx engine error is an outage (mock in dev, 503 in prod), not an auth failure", () => {
  assert.deepEqual(resolveEngineFallback({ status: 500, data: null }, true), { kind: "mock" });
  assert.deepEqual(resolveEngineFallback({ status: 503, data: null }, false), { kind: "unavailable" });
});

test("the three failure modes are never collapsed: 401 ≠ transport ≠ 5xx, all distinct from data", () => {
  const prod = (r: { status: number; data: unknown }) => resolveEngineFallback(r as never, false).kind;
  assert.equal(prod({ status: 401, data: null }), "unauthorized"); // auth failure
  assert.equal(prod({ status: 0, data: null }), "unavailable");    // transport failure → 503
  assert.equal(prod({ status: 500, data: null }), "unavailable");  // upstream error → 503
  assert.equal(prod({ status: 200, data: { ok: true } }), "data"); // healthy
  // 401 must not be reachable as an outage response, and an outage must not be reachable as a 401
  assert.notEqual(prod({ status: 401, data: null }), prod({ status: 0, data: null }));
});
