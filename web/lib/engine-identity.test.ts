// engine-identity tests (node --test, type-stripped like the other lib tests).
//
// `upsertEngineUser` is the web tier's only way to turn a third-party identity into an engine user id,
// and it is called on the sign-in path where a wrong request means a session that can never be
// attributed. These pin the request it sends and the four ways it can decline to return an id. The
// helper moved out of lib/auth.ts unchanged; this is the first test coverage it has had.
import { test } from "node:test";
import assert from "node:assert/strict";

import {
  __identityCacheStats,
  __resetIdentityCache,
  resolveEngineUserId,
  upsertEngineUser,
} from "./engine-identity.ts";

interface Call {
  url: string;
  init: RequestInit;
}

/** Run `fn` with `fetch` replaced, restoring the real one (and RWE_INTERNAL_SECRET) afterwards. */
async function withFetch(
  reply: () => unknown,
  fn: (calls: Call[]) => Promise<void>,
  secret?: string,
): Promise<void> {
  const g = globalThis as unknown as { fetch: unknown };
  const realFetch = g.fetch;
  const realSecret = process.env.RWE_INTERNAL_SECRET;
  const calls: Call[] = [];

  g.fetch = async (url: string, init: RequestInit) => {
    calls.push({ url, init });
    const value = reply();
    if (value instanceof Error) throw value;
    return value;
  };
  if (secret === undefined) delete process.env.RWE_INTERNAL_SECRET;
  else process.env.RWE_INTERNAL_SECRET = secret;

  try {
    await fn(calls);
  } finally {
    g.fetch = realFetch;
    if (realSecret === undefined) delete process.env.RWE_INTERNAL_SECRET;
    else process.env.RWE_INTERNAL_SECRET = realSecret;
  }
}

const ok = (body: unknown) => ({ ok: true, json: async () => body });
const notOk = (status: number) => ({ ok: false, status, json: async () => ({}) });

const IDENTITY = {
  provider: "google",
  providerAccountId: "108461123456789012345",
  email: "reader@example.com",
  displayName: "A Reader",
};

test("posts the identity to the engine, keyed on providerAccountId", async () => {
  await withFetch(() => ok({ userId: 42 }), async (calls) => {
    assert.equal(await upsertEngineUser(IDENTITY), 42);
    assert.equal(calls.length, 1);

    const { url, init } = calls[0]!;
    assert.ok(url.endsWith("/api/internal/users"), url);
    assert.equal(init.method, "POST");
    assert.equal(init.cache, "no-store");
    assert.equal((init.headers as Record<string, string>)["Content-Type"], "application/json");

    // The engine joins identities on (provider, provider_account_id) and never on email — see
    // docs/IDENTITY_UPSERT_CONCURRENCY.md I5. The body must carry the account id as its own field.
    assert.deepEqual(JSON.parse(init.body as string), {
      provider: "google",
      providerAccountId: "108461123456789012345",
      email: "reader@example.com",
      displayName: "A Reader",
    });
  });
});

test("null / absent profile fields are omitted from the body, not sent as null", async () => {
  // The engine treats a supplied value as "refresh this" and a missing one as "leave it alone", so
  // sending null would blank a returning reader's profile.
  await withFetch(() => ok({ userId: 7 }), async (calls) => {
    await upsertEngineUser({ provider: "dev", providerAccountId: "d-1", email: null });
    assert.deepEqual(JSON.parse(calls[0]!.init.body as string), {
      provider: "dev",
      providerAccountId: "d-1",
    });
  });
});

test("sends X-IH-Auth when the internal secret is configured", async () => {
  await withFetch(() => ok({ userId: 1 }), async (calls) => {
    await upsertEngineUser(IDENTITY);
    assert.equal((calls[0]!.init.headers as Record<string, string>)["X-IH-Auth"], "s3cret");
  }, "s3cret");
});

test("omits X-IH-Auth when no secret is configured (open dev engine)", async () => {
  await withFetch(() => ok({ userId: 1 }), async (calls) => {
    await upsertEngineUser(IDENTITY);
    assert.equal("X-IH-Auth" in (calls[0]!.init.headers as Record<string, string>), false);
  });
});

test("a non-2xx response resolves to null rather than throwing", async () => {
  // 401 is the shape a missing/incorrect internal secret takes in production.
  for (const status of [401, 403, 500, 503]) {
    await withFetch(() => notOk(status), async () => {
      assert.equal(await upsertEngineUser(IDENTITY), null, `status ${status}`);
    });
  }
});

test("a transport failure resolves to null rather than throwing", async () => {
  await withFetch(() => new Error("ECONNREFUSED"), async () => {
    assert.equal(await upsertEngineUser(IDENTITY), null);
  });
});

test("a 2xx without a numeric userId resolves to null", async () => {
  for (const body of [{}, { userId: "42" }, { userId: null }, null]) {
    await withFetch(() => ok(body), async () => {
      assert.equal(await upsertEngineUser(IDENTITY), null, JSON.stringify(body));
    });
  }
});

// --------------------------------------------------------------------------------------------------
// resolveEngineUserId — identity recovery (SESSION_IDENTITY_RECOVERY_DESIGN.md §3–§5).
//
// Nothing calls it in production yet; these tests are the whole of its exercise. Each one names the
// property it pins, because the cache layers are performance measures and must never become the reason
// an answer is correct.
// --------------------------------------------------------------------------------------------------

const SUB = "108461123456789012345";
const GOOGLE_TOKEN = { provider: "google", providerAccountId: SUB, email: "reader@example.com" };

/** Replace fetch + silence the recovery log; returns the call count so tests can assert on it. */
async function withResolver(
  reply: () => unknown,
  fn: (state: { fetches: number }) => Promise<void>,
): Promise<void> {
  const g = globalThis as unknown as { fetch: unknown };
  const realFetch = g.fetch;
  const realWarn = console.warn;
  const realNow = Date.now;
  const state = { fetches: 0 };

  __resetIdentityCache();
  g.fetch = async () => {
    state.fetches += 1;
    const value = reply();
    if (value instanceof Error) throw value;
    return value;
  };
  console.warn = () => {};

  try {
    await fn(state);
  } finally {
    g.fetch = realFetch;
    console.warn = realWarn;
    Date.now = realNow;
    __resetIdentityCache();
  }
}

const REAL_NOW = Date.now;

/** Move the clock forward for every subsequent `Date.now()` in the module under test. */
function advance(ms: number): void {
  const base = Date.now();
  Date.now = () => base + ms;
}

test("a token that already has an engine id resolves with ZERO engine calls", async () => {
  // The healthy path, which is essentially all traffic. A regression here is a per-request engine call
  // in production, and only a call count would catch it.
  await withResolver(() => ok({ userId: 999 }), async (state) => {
    assert.equal(await resolveEngineUserId({ ...GOOGLE_TOKEN, engineUserId: 42 }), 42);
    assert.equal(state.fetches, 0);
    assert.equal(__identityCacheStats().entries, 0, "the healthy path must not even populate the cache");
  });
});

test("a google token without an id resolves through the keyed upsert", async () => {
  await withResolver(() => ok({ userId: 42 }), async (state) => {
    assert.equal(await resolveEngineUserId(GOOGLE_TOKEN), 42);
    assert.equal(state.fetches, 1);
  });
});

test("a legacy token with no provider claim falls back to `sub`", async () => {
  await withResolver(() => ok({ userId: 7 }), async (state) => {
    assert.equal(await resolveEngineUserId({ sub: SUB, email: "reader@example.com" }), 7);
    assert.equal(state.fetches, 1);
  });
});

test("a non-google provider never attempts — a dev token's `sub` is an engine id, not an account id", async () => {
  await withResolver(() => ok({ userId: 1 }), async (state) => {
    assert.equal(await resolveEngineUserId({ provider: "dev", sub: "31", email: "d@x.io" }), null);
    assert.equal(state.fetches, 0);
  });
});

test("no usable account id means no attempt", async () => {
  await withResolver(() => ok({ userId: 1 }), async (state) => {
    for (const token of [{}, { sub: "" }, { providerAccountId: "" }, { sub: 12345 }]) {
      assert.equal(await resolveEngineUserId(token), null, JSON.stringify(token));
    }
    assert.equal(state.fetches, 0);
  });
});

/** Run `fn` with the beta gate on and `allowed` as the entire allowlist. */
async function withAllowlist(allowed: string, fn: () => Promise<void>): Promise<void> {
  const realEnabled = process.env.BETA_ACCESS_ENABLED;
  const realList = process.env.BETA_ALLOWLIST;
  process.env.BETA_ACCESS_ENABLED = "1";
  process.env.BETA_ALLOWLIST = allowed;
  try {
    await fn();
  } finally {
    if (realEnabled === undefined) delete process.env.BETA_ACCESS_ENABLED;
    else process.env.BETA_ACCESS_ENABLED = realEnabled;
    if (realList === undefined) delete process.env.BETA_ALLOWLIST;
    else process.env.BETA_ALLOWLIST = realList;
  }
}

test("an email the allowlist no longer accepts never reaches the engine", async () => {
  // Recovery is the deferred second half of a sign-in, so it re-runs sign-in's gate. Without this, a
  // revoked reader's stale session could mint an engine account.
  await withAllowlist("someone-else@example.com", async () => {
    await withResolver(() => ok({ userId: 1 }), async (state) => {
      assert.equal(await resolveEngineUserId(GOOGLE_TOKEN), null);
      assert.equal(state.fetches, 0);
    });
  });
});

test("a denial is REMEMBERED, so the allowlist is not re-read on every session read", async () => {
  // The property, and the one trade it makes. `isEmailAllowed` does an uncached `readFileSync`, and the
  // caller — `callbacks.jwt` — runs on every `getServerSession`. A denial checked per call and left
  // unrecorded means synchronous file I/O on every server render, always returning null, for the 30-day
  // life of the token. So the denial is a negative cache entry like any other failure (§4).
  //
  // Asserted by observable consequence rather than by counting syscalls: re-allowing the address has no
  // effect until the backoff window expires, which can only be true if the memo answered without
  // re-running the check.
  await withAllowlist("someone-else@example.com", async () => {
    await withResolver(() => ok({ userId: 1 }), async (state) => {
      assert.equal(await resolveEngineUserId(GOOGLE_TOKEN), null);
      assert.equal(__identityCacheStats().entries, 1, "the denial must be recorded, not merely returned");

      process.env.BETA_ALLOWLIST = "reader@example.com";        // the operator approves them
      for (let i = 0; i < 10; i++) assert.equal(await resolveEngineUserId(GOOGLE_TOKEN), null);
      assert.equal(state.fetches, 0, "the cached denial must answer without re-reading the allowlist");

      // And the trade: approval takes effect within one backoff window, not instantly. That is the
      // whole cost, and it is well inside what BETA_ACCESS_ENABLED already implies.
      advance(30 * 1000 + 1);
      assert.equal(await resolveEngineUserId(GOOGLE_TOKEN), 1);
      assert.equal(state.fetches, 1);
    });
  });
});

test("a denial is cached under its own identity and cannot deny anyone else", async () => {
  await withAllowlist("b@example.com", async () => {
    await withResolver(() => ok({ userId: 22 }), async (state) => {
      const denied = { provider: "google", providerAccountId: "acct-a", email: "a@example.com" };
      const allowed = { provider: "google", providerAccountId: "acct-b", email: "b@example.com" };

      assert.equal(await resolveEngineUserId(denied), null);
      assert.equal(await resolveEngineUserId(allowed), 22, "one reader's denial must not block another");
      assert.equal(state.fetches, 1);
    });
  });
});

// --------------------------------------------------------------------------------------------------
// The kill switch. `RWE_IDENTITY_RECOVERY=0` must reproduce pre-recovery behaviour exactly, without a
// rebuild — the rollback path in IDENTITY_RECOVERY_IMPLEMENTATION_PLAN.md's commit 5.
// --------------------------------------------------------------------------------------------------

async function withRecoveryFlag(value: string | undefined, fn: () => Promise<void>): Promise<void> {
  const real = process.env.RWE_IDENTITY_RECOVERY;
  if (value === undefined) delete process.env.RWE_IDENTITY_RECOVERY;
  else process.env.RWE_IDENTITY_RECOVERY = value;
  try {
    await fn();
  } finally {
    if (real === undefined) delete process.env.RWE_IDENTITY_RECOVERY;
    else process.env.RWE_IDENTITY_RECOVERY = real;
  }
}

test("RWE_IDENTITY_RECOVERY=0 disables recovery and restores pre-recovery behaviour", async () => {
  for (const off of ["0", "false", "no", "off", "OFF", " 0 "]) {
    await withRecoveryFlag(off, async () => {
      await withResolver(() => ok({ userId: 42 }), async (state) => {
        assert.equal(await resolveEngineUserId(GOOGLE_TOKEN), null, `flag=${JSON.stringify(off)}`);
        assert.equal(state.fetches, 0);
        assert.equal(__identityCacheStats().entries, 0, "a disabled resolver must not cache anything");
      });
    });
  }
});

test("the switch never disables the token's own id — that is not recovery", async () => {
  // Disabling recovery must not sign anyone out or de-attribute a working session.
  await withRecoveryFlag("0", async () => {
    await withResolver(() => ok({ userId: 999 }), async (state) => {
      assert.equal(await resolveEngineUserId({ ...GOOGLE_TOKEN, engineUserId: 42 }), 42);
      assert.equal(state.fetches, 0);
    });
  });
});

test("recovery is on by default and stays on for any value that is not a documented off", async () => {
  for (const on of [undefined, "1", "true", "yes", "on", ""]) {
    await withRecoveryFlag(on, async () => {
      await withResolver(() => ok({ userId: 42 }), async (state) => {
        assert.equal(await resolveEngineUserId(GOOGLE_TOKEN), 42, `flag=${JSON.stringify(on)}`);
        assert.equal(state.fetches, 1);
      });
    });
  }
});

test("concurrent callers for one identity coalesce into a single engine call", async () => {
  let release: (v: unknown) => void = () => {};
  const gate = new Promise((r) => { release = r; });

  await withResolver(() => ok({ userId: 42 }), async (state) => {
    const g = globalThis as unknown as { fetch: unknown };
    const counting = g.fetch as () => Promise<unknown>;
    g.fetch = async () => { await gate; return counting(); };   // hold every call open

    const callers = Array.from({ length: 25 }, () => resolveEngineUserId(GOOGLE_TOKEN));
    assert.equal(__identityCacheStats().inflight, 1, "25 callers must share one in-flight promise");
    release(null);

    const ids = await Promise.all(callers);
    assert.deepEqual([...new Set(ids)], [42], "every caller gets the same id");
    assert.equal(state.fetches, 1, `expected 1 engine call for 25 callers, saw ${state.fetches}`);
    assert.equal(__identityCacheStats().inflight, 0, "the in-flight entry must be released");
  });
});

test("a resolved id is reused inside the TTL and re-fetched after it", async () => {
  await withResolver(() => ok({ userId: 42 }), async (state) => {
    assert.equal(await resolveEngineUserId(GOOGLE_TOKEN), 42);
    assert.equal(state.fetches, 1);

    for (let i = 0; i < 5; i++) await resolveEngineUserId(GOOGLE_TOKEN);
    assert.equal(state.fetches, 1, "inside the TTL the memo must answer");

    advance(10 * 60 * 1000 + 1);                                 // MEMO_TTL_MS + 1ms
    assert.equal(await resolveEngineUserId(GOOGLE_TOKEN), 42);
    assert.equal(state.fetches, 2, "after the TTL it must ask again");
  });
});

test("a failure is remembered as a backoff, not as an answer, and expires on its own schedule", async () => {
  await withResolver(() => notOk(503), async (state) => {
    assert.equal(await resolveEngineUserId(GOOGLE_TOKEN), null);
    assert.equal(state.fetches, 1);

    for (let i = 0; i < 10; i++) {
      assert.equal(await resolveEngineUserId(GOOGLE_TOKEN), null);
    }
    assert.equal(state.fetches, 1, "a sick engine must not be retried per call");

    advance(30 * 1000 + 1);                                      // BACKOFF_MS + 1ms
    assert.equal(await resolveEngineUserId(GOOGLE_TOKEN), null);
    assert.equal(state.fetches, 2, "after the backoff window it tries once more");
  });
});

test("the backoff is shorter than the success TTL, so an outage recovers faster than it is cached", async () => {
  await withResolver(() => notOk(503), async (state) => {
    await resolveEngineUserId(GOOGLE_TOKEN);
    advance(60 * 1000);                                          // past BACKOFF_MS, inside MEMO_TTL_MS
    await resolveEngineUserId(GOOGLE_TOKEN);
    assert.equal(state.fetches, 2, "a failure must not be held for the success TTL");
  });
});

test("every engine failure mode resolves to null without throwing", async () => {
  for (const reply of [() => notOk(401), () => notOk(500), () => new Error("timeout"),
                       () => ok({}), () => ok(null)]) {
    await withResolver(reply, async () => {
      assert.equal(await resolveEngineUserId(GOOGLE_TOKEN), null);
    });
  }
});

test("two identities never share a cache entry — no caller can receive another user's id", async () => {
  const byAccount: Record<string, number> = { "acct-a": 11, "acct-b": 22 };
  const g = globalThis as unknown as { fetch: unknown };
  const realFetch = g.fetch;
  const realWarn = console.warn;
  __resetIdentityCache();
  console.warn = () => {};
  let fetches = 0;

  g.fetch = async (_url: string, init: RequestInit) => {
    fetches += 1;
    const body = JSON.parse(init.body as string) as { providerAccountId: string };
    return ok({ userId: byAccount[body.providerAccountId] });    // the engine answers per identity
  };

  try {
    const a = { provider: "google", providerAccountId: "acct-a", email: "a@example.com" };
    const b = { provider: "google", providerAccountId: "acct-b", email: "b@example.com" };

    assert.equal(await resolveEngineUserId(a), 11);
    assert.equal(await resolveEngineUserId(b), 22);
    assert.equal(fetches, 2, "distinct identities must each be asked for");

    // Interleaved re-reads, both served from cache, must not cross.
    for (let i = 0; i < 10; i++) {
      assert.equal(await resolveEngineUserId(a), 11);
      assert.equal(await resolveEngineUserId(b), 22);
    }
    assert.equal(fetches, 2);
    assert.equal(__identityCacheStats().entries, 2);

    // And concurrently, which is where a shared in-flight promise would leak one into the other.
    const mixed = await Promise.all([
      resolveEngineUserId(a), resolveEngineUserId(b), resolveEngineUserId(a), resolveEngineUserId(b),
    ]);
    assert.deepEqual(mixed, [11, 22, 11, 22]);
  } finally {
    g.fetch = realFetch;
    console.warn = realWarn;
    __resetIdentityCache();
  }
});

test("the cache is bounded: many identities cannot grow it without limit", async () => {
  // A TTL alone does not evict — an entry nobody reads again would live forever. This is the test that
  // would fail if the sweep or the ceiling were removed.
  await withResolver(() => ok({ userId: 1 }), async () => {
    const { maxEntries } = __identityCacheStats();
    for (let i = 0; i < maxEntries + 250; i++) {
      await resolveEngineUserId({ provider: "google", providerAccountId: `acct-${i}`,
                                 email: `r${i}@example.com` });
    }
    const { entries } = __identityCacheStats();
    assert.ok(entries <= maxEntries, `cache holds ${entries}, ceiling is ${maxEntries}`);
    assert.equal(__identityCacheStats().inflight, 0, "no in-flight entry may be left behind");
  });
});

test("expired entries are swept, not merely ignored", async () => {
  await withResolver(() => ok({ userId: 1 }), async () => {
    for (let i = 0; i < 50; i++) {
      await resolveEngineUserId({ provider: "google", providerAccountId: `old-${i}`,
                                 email: `r${i}@example.com` });
    }
    assert.equal(__identityCacheStats().entries, 50);

    advance(10 * 60 * 1000 + 1);                                 // everything above is now expired
    await resolveEngineUserId({ provider: "google", providerAccountId: "fresh",
                               email: "fresh@example.com" });
    assert.equal(__identityCacheStats().entries, 1,
      "the write-time sweep must reclaim expired entries, not leave them for a read that never comes");
  });
});

// --------------------------------------------------------------------------------------------------
// Timeout behaviour (commit 3.5). Before `upsertEngineUser` had a deadline, a wedged engine left the
// in-flight promise pending, recorded no backoff, and every later caller coalesced onto it — measured
// as "STILL PENDING after 1500ms, 1 in-flight entry, 0 memo entries". These pin the fix.
// --------------------------------------------------------------------------------------------------

/** A fetch that never answers but honours the abort signal, like a wedged server mid-request. */
function hanging(observed: { aborts: number }) {
  return (_url: string, init: RequestInit) =>
    new Promise<Response>((_resolve, reject) => {
      init.signal?.addEventListener("abort", () => {
        observed.aborts += 1;
        reject(new DOMException("This operation was aborted", "AbortError"));
      });
    });
}

/** Run with a short engine deadline so the real abort path is exercised, not a stubbed rejection. */
async function withShortDeadline(ms: number, fn: () => Promise<void>): Promise<void> {
  const real = process.env.RWE_BACKEND_TIMEOUT_MS;
  process.env.RWE_BACKEND_TIMEOUT_MS = String(ms);
  try {
    await fn();
  } finally {
    if (real === undefined) delete process.env.RWE_BACKEND_TIMEOUT_MS;
    else process.env.RWE_BACKEND_TIMEOUT_MS = real;
  }
}

test("upsertEngineUser resolves to null when the engine wedges, exactly like any other failure", async () => {
  // The sign-in path reads this as "engine unavailable": the jwt callback leaves engineUserId unset and
  // the dev provider's authorize() fails the sign-in cleanly. Both are unchanged by the timeout.
  const observed = { aborts: 0 };
  const g = globalThis as unknown as { fetch: unknown };
  const real = g.fetch;
  g.fetch = hanging(observed);
  try {
    await withShortDeadline(60, async () => {
      assert.equal(await upsertEngineUser(IDENTITY), null);
      assert.equal(observed.aborts, 1, "the request must actually be aborted");
    });
  } finally {
    g.fetch = real;
  }
});

test("a timed-out recovery settles: in-flight cleared, backoff recorded, no permanent attachment", async () => {
  const observed = { aborts: 0 };
  const g = globalThis as unknown as { fetch: unknown };
  const realFetch = g.fetch;
  const realWarn = console.warn;
  __resetIdentityCache();
  console.warn = () => {};
  g.fetch = hanging(observed);

  try {
    await withShortDeadline(60, async () => {
      const started = Date.now();
      assert.equal(await resolveEngineUserId(GOOGLE_TOKEN), null);
      assert.ok(Date.now() - started < 2000, "the caller must not wait on a wedged engine");

      const stats = __identityCacheStats();
      assert.equal(stats.inflight, 0, "the in-flight entry must be released once the request settles");
      assert.equal(stats.entries, 1, "the failure must be remembered as a backoff");

      // And the backoff is honoured: further callers get null without touching the engine.
      const before = observed.aborts;
      for (let i = 0; i < 5; i++) assert.equal(await resolveEngineUserId(GOOGLE_TOKEN), null);
      assert.equal(observed.aborts, before, "the backoff must suppress retries against a wedged engine");
    });
  } finally {
    g.fetch = realFetch;
    console.warn = realWarn;
    __resetIdentityCache();
  }
});

test("concurrent callers share one timed-out request and all of them settle", async () => {
  // The failure mode this commit removes: 25 callers coalesced onto a promise that never settles.
  const observed = { aborts: 0 };
  const g = globalThis as unknown as { fetch: unknown };
  const realFetch = g.fetch;
  const realWarn = console.warn;
  __resetIdentityCache();
  console.warn = () => {};
  g.fetch = hanging(observed);

  try {
    await withShortDeadline(60, async () => {
      const callers = Array.from({ length: 25 }, () => resolveEngineUserId(GOOGLE_TOKEN));
      assert.equal(__identityCacheStats().inflight, 1, "they must share one request");

      const results = await Promise.all(callers);            // must not hang
      assert.deepEqual([...new Set(results)], [null]);
      assert.equal(observed.aborts, 1, "one shared request, one abort");
      assert.equal(__identityCacheStats().inflight, 0, "the shared entry must be released");
    });
  } finally {
    g.fetch = realFetch;
    console.warn = realWarn;
    __resetIdentityCache();
  }
});

test("recovery succeeds on a later attempt once the engine answers again", async () => {
  const observed = { aborts: 0 };
  const g = globalThis as unknown as { fetch: unknown };
  const realFetch = g.fetch;
  const realWarn = console.warn;
  __resetIdentityCache();
  console.warn = () => {};
  g.fetch = hanging(observed);

  try {
    await withShortDeadline(60, async () => {
      assert.equal(await resolveEngineUserId(GOOGLE_TOKEN), null);        // wedged
      advance(30 * 1000 + 1);                                             // past the backoff
      g.fetch = async () => ok({ userId: 42 });                           // engine comes back
      assert.equal(await resolveEngineUserId(GOOGLE_TOKEN), 42);
      assert.equal(__identityCacheStats().inflight, 0);
    });
  } finally {
    g.fetch = realFetch;
    console.warn = realWarn;
    Date.now = REAL_NOW;
    __resetIdentityCache();
  }
});
