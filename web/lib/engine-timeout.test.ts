// engine-timeout tests (node --test, type-stripped like the other lib tests).
//
// The deadline every engine call gets. Node's fetch has none of its own, so without this a wedged
// server holds a promise open for minutes — and `lib/engine-identity.ts` coalesces callers onto that
// promise, which turns one stalled request into a stalled set of them.
import { test } from "node:test";
import assert from "node:assert/strict";

import { engineTimeoutMs, fetchWithTimeout } from "./engine-timeout.ts";

/** A fetch that never answers but honours the abort signal — how undici behaves against a wedged
 *  server that has accepted the connection. */
function hangingFetch(observed: { aborted: boolean }) {
  return (_url: string, init: RequestInit) =>
    new Promise<Response>((_resolve, reject) => {
      init.signal?.addEventListener("abort", () => {
        observed.aborted = true;
        reject(new DOMException("This operation was aborted", "AbortError"));
      });
    });
}

async function withFetch(impl: unknown, fn: () => Promise<void>): Promise<void> {
  const g = globalThis as unknown as { fetch: unknown };
  const real = g.fetch;
  g.fetch = impl;
  try {
    await fn();
  } finally {
    g.fetch = real;
  }
}

test("engineTimeoutMs: the default, and RWE_BACKEND_TIMEOUT_MS when it is usable", () => {
  const real = process.env.RWE_BACKEND_TIMEOUT_MS;
  try {
    delete process.env.RWE_BACKEND_TIMEOUT_MS;
    assert.equal(engineTimeoutMs(), 6000);

    process.env.RWE_BACKEND_TIMEOUT_MS = "1500";
    assert.equal(engineTimeoutMs(), 1500);

    // An empty or nonsense value must not become a zero-millisecond deadline: `Number("")` is 0, and a
    // 0 ms deadline aborts every engine call instantly, which presents exactly like a total outage.
    for (const bad of ["", "   ", "abc", "0", "-5", "NaN"]) {
      process.env.RWE_BACKEND_TIMEOUT_MS = bad;
      assert.equal(engineTimeoutMs(), 6000, `RWE_BACKEND_TIMEOUT_MS=${JSON.stringify(bad)}`);
    }
  } finally {
    if (real === undefined) delete process.env.RWE_BACKEND_TIMEOUT_MS;
    else process.env.RWE_BACKEND_TIMEOUT_MS = real;
  }
});

test("a hung connection is aborted at the deadline instead of pending indefinitely", async () => {
  const observed = { aborted: false };
  await withFetch(hangingFetch(observed), async () => {
    const started = Date.now();
    await assert.rejects(
      () => fetchWithTimeout("http://engine.invalid/api/internal/users", { method: "POST" }, 60),
      // The deadline rejects and the transport's abort rejection land in the same tick, so either may
      // win the race. Both mean "we gave up"; the caller (upsertEngineUser) maps both to null.
      (err: Error) => ["AbortError", "TimeoutError"].includes(err.name),
    );
    assert.ok(observed.aborted, "the signal must reach the request, not just the caller");
    assert.ok(Date.now() - started < 2000, "it must not wait for undici's own multi-minute timeout");
  });
});

test("a fast response is untouched, and its timer is cleared", async () => {
  // If the timer leaked, node would stay alive holding it; the suite finishing is the check.
  await withFetch(async () => ({ ok: true, json: async () => ({ userId: 1 }) }), async () => {
    const res = await fetchWithTimeout("http://engine.invalid/x", undefined, 50_000);
    assert.equal((res as unknown as { ok: boolean }).ok, true);
  });
});

test("a transport error surfaces as itself, not as a timeout", async () => {
  await withFetch(async () => { throw new Error("ECONNREFUSED"); }, async () => {
    await assert.rejects(() => fetchWithTimeout("http://engine.invalid/x", undefined, 5_000),
      (err: Error) => err.message === "ECONNREFUSED");
  });
});

test("the deadline is per call, so a slow call cannot poison the next one", async () => {
  const observed = { aborted: false };
  await withFetch(hangingFetch(observed), async () => {
    await assert.rejects(() => fetchWithTimeout("http://engine.invalid/x", undefined, 40));
  });
  await withFetch(async () => ({ ok: true, json: async () => ({ userId: 9 }) }), async () => {
    const res = await fetchWithTimeout("http://engine.invalid/x", undefined, 40);
    assert.equal((res as unknown as { ok: boolean }).ok, true);
  });
});

test("a transport that IGNORES the abort signal still settles at the deadline", async () => {
  // The failure this helper exists to make impossible. A stub that never listens for `abort` models
  // any transport that does not cooperate — and the caller must still settle, because coalesced
  // callers in lib/engine-identity.ts are all attached to this one promise.
  const uncooperative = () => new Promise<Response>(() => {});
  await withFetch(uncooperative, async () => {
    const started = Date.now();
    await assert.rejects(
      () => fetchWithTimeout("http://engine.invalid/x", undefined, 60),
      (err: Error) => err.name === "TimeoutError",
    );
    assert.ok(Date.now() - started < 2000, `settled in ${Date.now() - started}ms`);
  });
});

test("a late rejection after the deadline does not surface as an unhandled rejection", async () => {
  // The race settles on the deadline; the transport rejects afterwards. Node would warn (and in some
  // configurations exit non-zero) if that rejection had no handler.
  let rejectLate: (err: Error) => void = () => {};
  const late = () => new Promise<Response>((_resolve, reject) => { rejectLate = reject; });
  await withFetch(late, async () => {
    await assert.rejects(() => fetchWithTimeout("http://engine.invalid/x", undefined, 40));
    rejectLate(new Error("socket closed, long after we gave up"));
    await new Promise((r) => setTimeout(r, 50));            // let any unhandled rejection surface
  });
});
