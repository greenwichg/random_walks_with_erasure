// engine-identity tests (node --test, type-stripped like the other lib tests).
//
// `upsertEngineUser` is the web tier's only way to turn a third-party identity into an engine user id,
// and it is called on the sign-in path where a wrong request means a session that can never be
// attributed. These pin the request it sends and the four ways it can decline to return an id. The
// helper moved out of lib/auth.ts unchanged; this is the first test coverage it has had.
import { mock, test } from "node:test";
import assert from "node:assert/strict";

import {
  __identityCacheStats,
  __resetIdentityCache,
  hasEngineUserId,
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

interface LogLine {
  event?: string;
  provider?: string;
  userId?: number;
  reason?: string;
  detail?: string;
  email?: string | null;
}

/** Replace fetch and CAPTURE the recovery log (rather than discarding it), so every test can assert on
 *  the engine call count and on what was written about it. */
async function withResolver(
  reply: () => unknown,
  fn: (state: { fetches: number; logs: LogLine[] }) => Promise<void>,
): Promise<void> {
  const g = globalThis as unknown as { fetch: unknown };
  const realFetch = g.fetch;
  const realWarn = console.warn;
  const realNow = Date.now;
  const state: { fetches: number; logs: LogLine[] } = { fetches: 0, logs: [] };

  __resetIdentityCache();
  g.fetch = async () => {
    state.fetches += 1;
    const value = reply();
    if (value instanceof Error) throw value;
    return value;
  };
  // Parsed, not stored raw: every recovery line must be machine-readable JSON, so a line that stopped
  // being parseable fails here rather than quietly degrading the operator's `docker logs | grep`.
  console.warn = (line: string) => { state.logs.push(JSON.parse(line) as LogLine); };

  try {
    await fn(state);
  } finally {
    g.fetch = realFetch;
    console.warn = realWarn;
    Date.now = realNow;
    __resetIdentityCache();
  }
}

/** The log lines for one event name. */
const only = (logs: LogLine[], event: string) => logs.filter((l) => l.event === event);

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

// --------------------------------------------------------------------------------------------------
// Recovery logging (commit 5b).
//
// Before these lines existed only success was logged, which inverted the useful signal: a recovery
// that failed for every reader — a wrong RWE_INTERNAL_SECRET is the realistic cause — produced logs
// byte-identical to having no broken sessions at all. You could ship the fix and never learn it had
// never worked. Three events, and the count matters as much as the content: one line per ATTEMPT,
// never per request, or a sick engine becomes a log flood on top of everything else.
// --------------------------------------------------------------------------------------------------

test("a successful recovery logs exactly one line, with the id and no email", async () => {
  await withResolver(() => ok({ userId: 42 }), async (state) => {
    await resolveEngineUserId(GOOGLE_TOKEN);
    assert.equal(state.logs.length, 1);
    assert.deepEqual(state.logs[0], {
      event: "engine_identity_recovered", provider: "google", userId: 42,
    });
    // Unlike a denial, nobody has to act on who this was; a rising rate is the signal, not the person.
    assert.equal("email" in state.logs[0]!, false, "the success line must not carry an email");
  });
});

test("the healthy path logs nothing at all", async () => {
  await withResolver(() => ok({ userId: 999 }), async (state) => {
    for (let i = 0; i < 20; i++) await resolveEngineUserId({ ...GOOGLE_TOKEN, engineUserId: 42 });
    assert.deepEqual(state.logs, [], "a token that already has an id is not a recovery");
  });
});

test("a failed recovery names the reason, and the reason distinguishes the causes", async () => {
  // The whole point of the line. http_401 means the shared secret is wrong; timeout means the engine
  // is wedged; unreachable means it is down. "Recovery failed" alone would not tell them apart.
  const cases: [() => unknown, string, string | undefined][] = [
    [() => notOk(401), "http_401", undefined],
    [() => notOk(403), "http_403", undefined],
    [() => notOk(500), "http_500", undefined],
    [() => notOk(503), "http_503", undefined],
    [() => ok({}), "malformed_response", undefined],
    [() => ok({ userId: "42" }), "malformed_response", undefined],
    [() => new Error("boom"), "unreachable", "Error"],
  ];
  for (const [reply, reason, detail] of cases) {
    await withResolver(reply, async (state) => {
      assert.equal(await resolveEngineUserId(GOOGLE_TOKEN), null);
      assert.equal(state.logs.length, 1, `${reason}: expected one line`);
      assert.equal(state.logs[0]!.event, "engine_identity_recovery_failed");
      assert.equal(state.logs[0]!.reason, reason);
      assert.equal(state.logs[0]!.provider, "google");
      if (detail !== undefined) assert.equal(state.logs[0]!.detail, detail);
      // A failure says nothing about who: the operator's action is on the engine, not the reader.
      assert.equal("email" in state.logs[0]!, false, `${reason}: must not carry an email`);
    });
  }
});

test("a transport failure carries the OS error code, not just `TypeError`", async () => {
  // undici reports a dead socket as `TypeError: fetch failed` and hides the useful part on `cause`.
  // ECONNREFUSED (engine restarting) vs ENOTFOUND (RWE_BACKEND_URL is wrong) is the whole diagnosis.
  const wrapped = Object.assign(new TypeError("fetch failed"), {
    cause: Object.assign(new Error("connect ECONNREFUSED 127.0.0.1:8000"), { code: "ECONNREFUSED" }),
  });
  await withResolver(() => wrapped, async (state) => {
    assert.equal(await resolveEngineUserId(GOOGLE_TOKEN), null);
    assert.equal(state.logs[0]!.reason, "unreachable");
    assert.equal(state.logs[0]!.detail, "ECONNREFUSED");
  });
});

test("a wedged engine logs `timeout`, distinct from a dead one", async () => {
  const g = globalThis as unknown as { fetch: unknown };
  const realFetch = g.fetch;
  const realWarn = console.warn;
  const logs: LogLine[] = [];
  __resetIdentityCache();
  console.warn = (line: string) => { logs.push(JSON.parse(line) as LogLine); };
  g.fetch = (_url: string, init: RequestInit) =>
    new Promise<Response>((_res, rej) => {
      init.signal?.addEventListener("abort", () => rej(new DOMException("aborted", "AbortError")));
    });
  try {
    await withShortDeadline(60, async () => {
      assert.equal(await resolveEngineUserId(GOOGLE_TOKEN), null);
      assert.equal(logs.length, 1);
      assert.equal(logs[0]!.event, "engine_identity_recovery_failed");
      assert.equal(logs[0]!.reason, "timeout");
    });
  } finally {
    g.fetch = realFetch;
    console.warn = realWarn;
    __resetIdentityCache();
  }
});

test("an allowlist denial logs the email, because someone has to act on it", async () => {
  // Same rationale as `beta_access_denied`: this reader is signed in and permanently un-attributed
  // until an operator adds them back or accepts that they are out. The engine is fine — no
  // `..._failed` line may be emitted, or the two situations become indistinguishable.
  await withAllowlist("someone-else@example.com", async () => {
    await withResolver(() => ok({ userId: 1 }), async (state) => {
      assert.equal(await resolveEngineUserId(GOOGLE_TOKEN), null);
      assert.equal(state.fetches, 0, "a denial must not reach the engine");
      assert.deepEqual(state.logs, [{
        event: "engine_identity_recovery_denied",
        provider: "google",
        email: "reader@example.com",
        reason: "not_allowlisted",
      }]);
    });
  });
});

test("the denial reason distinguishes a removed reader from a misconfigured gate", async () => {
  // `empty_allowlist` is fail-closed and denies EVERYONE — an operational emergency that looks
  // identical to `not_allowlisted` without this field.
  await withAllowlist("", async () => {
    await withResolver(() => ok({ userId: 1 }), async (state) => {
      await resolveEngineUserId(GOOGLE_TOKEN);
      assert.equal(state.logs[0]!.reason, "empty_allowlist");
    });
  });
  await withAllowlist("someone-else@example.com", async () => {
    await withResolver(() => ok({ userId: 1 }), async (state) => {
      await resolveEngineUserId({ ...GOOGLE_TOKEN, email: undefined });
      assert.equal(state.logs[0]!.reason, "no_email");
      assert.equal(state.logs[0]!.email, null);
    });
  });
});

// -- no duplicates -----------------------------------------------------------------------------
// One line per attempt. Every cache layer that suppresses an attempt must suppress its line too,
// otherwise the log volume tracks REQUESTS — and `callbacks.jwt` runs on every server render.

test("concurrent callers sharing one attempt produce ONE line, not one each", async () => {
  let release: (v: unknown) => void = () => {};
  const gate = new Promise((r) => { release = r; });
  await withResolver(() => ok({ userId: 42 }), async (state) => {
    const g = globalThis as unknown as { fetch: unknown };
    const counting = g.fetch as () => Promise<unknown>;
    g.fetch = async () => { await gate; return counting(); };

    const callers = Array.from({ length: 25 }, () => resolveEngineUserId(GOOGLE_TOKEN));
    release(null);
    await Promise.all(callers);

    assert.equal(state.fetches, 1);
    assert.equal(only(state.logs, "engine_identity_recovered").length, 1,
      `25 coalesced callers wrote ${state.logs.length} lines`);
  });
});

test("memo hits after a success log nothing further", async () => {
  await withResolver(() => ok({ userId: 42 }), async (state) => {
    for (let i = 0; i < 50; i++) await resolveEngineUserId(GOOGLE_TOKEN);
    assert.equal(state.fetches, 1);
    assert.equal(state.logs.length, 1, "50 server renders must not be 50 log lines");
  });
});

test("the backoff suppresses the failure line as well as the engine call", async () => {
  await withResolver(() => notOk(503), async (state) => {
    for (let i = 0; i < 30; i++) await resolveEngineUserId(GOOGLE_TOKEN);
    assert.equal(state.fetches, 1);
    assert.equal(state.logs.length, 1, "a sick engine must not also produce a log flood");

    advance(30 * 1000 + 1);                                   // BACKOFF_MS + 1ms
    await resolveEngineUserId(GOOGLE_TOKEN);
    assert.equal(state.logs.length, 2, "a genuinely new attempt does get its own line");
  });
});

test("a cached denial is not re-logged on every session read", async () => {
  await withAllowlist("someone-else@example.com", async () => {
    await withResolver(() => ok({ userId: 1 }), async (state) => {
      for (let i = 0; i < 30; i++) await resolveEngineUserId(GOOGLE_TOKEN);
      assert.equal(state.logs.length, 1, "the denial memo must suppress the line too");
    });
  });
});

test("recovery disabled by the kill switch logs nothing", async () => {
  const real = process.env.RWE_IDENTITY_RECOVERY;
  process.env.RWE_IDENTITY_RECOVERY = "0";
  try {
    await withResolver(() => ok({ userId: 42 }), async (state) => {
      await resolveEngineUserId(GOOGLE_TOKEN);
      assert.deepEqual(state.logs, [], "a disabled resolver is silent, not noisy about being off");
    });
  } finally {
    if (real === undefined) delete process.env.RWE_IDENTITY_RECOVERY;
    else process.env.RWE_IDENTITY_RECOVERY = real;
  }
});

test("every recovery line is parseable JSON carrying a distinct `event`", async () => {
  // The shape contract the operator's `docker logs deploy-web-1 | grep engine_identity` depends on.
  // `withResolver` already JSON.parses each line, so reaching here at all proves parseability.
  const seen = new Set<string>();
  await withResolver(() => ok({ userId: 42 }), async (state) => {
    await resolveEngineUserId(GOOGLE_TOKEN);
    state.logs.forEach((l) => seen.add(l.event!));
  });
  await withResolver(() => notOk(500), async (state) => {
    await resolveEngineUserId(GOOGLE_TOKEN);
    state.logs.forEach((l) => seen.add(l.event!));
  });
  await withAllowlist("nobody@example.com", async () => {
    await withResolver(() => ok({ userId: 1 }), async (state) => {
      await resolveEngineUserId(GOOGLE_TOKEN);
      state.logs.forEach((l) => seen.add(l.event!));
    });
  });
  assert.deepEqual([...seen].sort(), [
    "engine_identity_recovered",
    "engine_identity_recovery_denied",
    "engine_identity_recovery_failed",
  ], "the three outcomes must be distinguishable by event name alone");
});

// -- hasEngineUserId ---------------------------------------------------------------------------

test("hasEngineUserId is the single definition of 'this token already has an id'", async () => {
  // There were two, and they disagreed on any non-numeric value: the resolver refused to USE it while
  // the callback refused to RECOVER it, which is a session nothing can repair. Both now ask this.
  assert.equal(hasEngineUserId({ engineUserId: 42 }), true);
  assert.equal(hasEngineUserId({ engineUserId: 0 }), true, "0 is a number, however unlikely an id");
  for (const bad of [undefined, null, "42", "", true, {}, []]) {
    assert.equal(hasEngineUserId({ engineUserId: bad }), false, JSON.stringify(bad) ?? "undefined");
  }
});

test("the resolver and the predicate agree, so no token is both un-usable and un-recoverable", async () => {
  await withResolver(() => ok({ userId: 42 }), async (state) => {
    // A garbage id: the predicate says "no id", so recovery runs and returns a real one.
    assert.equal(hasEngineUserId({ engineUserId: "42" }), false);
    assert.equal(await resolveEngineUserId({ ...GOOGLE_TOKEN, engineUserId: "42" }), 42);
    assert.equal(state.fetches, 1);
  });
});

// --------------------------------------------------------------------------------------------------
// Caller-specific deadlines (S1).
//
// Recovery is awaited inside `getServerSession`, so its deadline is a reader waiting on a render for
// work they did not ask for; sign-in's deadline is the reader waiting for the thing they DID ask for.
// Same helper, two deadlines, chosen by caller.
//
// Driven with node:test mock timers rather than real ones: the difference under test is 2000ms vs
// 6000ms, and a suite that waited those out would be slower than everything else here combined. Ticking
// is also exact — "still pending at 2001ms" is an assertion, not a race against a machine's load.
// --------------------------------------------------------------------------------------------------

/** A fetch that never answers but honours abort — a wedged engine mid-request. */
function wedged(observed: { aborts: number }) {
  return (_url: string, init: RequestInit) =>
    new Promise<Response>((_res, rej) => {
      init.signal?.addEventListener("abort", () => {
        observed.aborts += 1;
        rej(new DOMException("aborted", "AbortError"));
      });
    });
}

/** Let queued microtasks run without advancing mocked time. */
const flush = () => new Promise((r) => setImmediate(r));

async function withMockTimers(fn: () => Promise<void>): Promise<void> {
  const realWarn = console.warn;
  console.warn = () => {};
  __resetIdentityCache();
  mock.timers.enable({ apis: ["setTimeout"] });
  try {
    await fn();
  } finally {
    mock.timers.reset();
    console.warn = realWarn;
    __resetIdentityCache();
  }
}

test("sign-in keeps the full default deadline — it is not shortened by recovery's", async () => {
  const g = globalThis as unknown as { fetch: unknown };
  const realFetch = g.fetch;
  const observed = { aborts: 0 };
  g.fetch = wedged(observed);
  try {
    await withMockTimers(async () => {
      let settled = false;
      // `upsertEngineUser` with no options is exactly what `callbacks.jwt` calls on sign-in.
      const p = upsertEngineUser(IDENTITY).then(() => { settled = true; });

      mock.timers.tick(2001);                       // past the RECOVERY deadline
      await flush();
      assert.equal(settled, false, "sign-in must NOT give up at recovery's 2s");
      assert.equal(observed.aborts, 0);

      mock.timers.tick(6000 - 2001 + 1);            // past the DEFAULT deadline
      await p;
      assert.equal(settled, true, "sign-in gives up at the 6s default");
      assert.equal(observed.aborts, 1);
    });
  } finally {
    g.fetch = realFetch;
  }
});

test("recovery gives up at the shorter deadline instead of holding the render", async () => {
  const g = globalThis as unknown as { fetch: unknown };
  const realFetch = g.fetch;
  const observed = { aborts: 0 };
  g.fetch = wedged(observed);
  try {
    await withMockTimers(async () => {
      let result: number | null | "pending" = "pending";
      const p = resolveEngineUserId(GOOGLE_TOKEN).then((v) => { result = v; });

      mock.timers.tick(1999);
      await flush();
      assert.equal(result, "pending", "not before its deadline");

      mock.timers.tick(2);
      await p;
      assert.equal(result, null, "recovery gives up at ~2s, four seconds before sign-in would");
      assert.equal(observed.aborts, 1, "and the request is actually aborted, not merely abandoned");

      // Unchanged by the shorter deadline: it is still a remembered failure, not a special case.
      const stats = __identityCacheStats();
      assert.equal(stats.entries, 1, "the backoff is recorded exactly as any other failure");
      assert.equal(stats.inflight, 0, "and the in-flight entry is released");
    });
  } finally {
    g.fetch = realFetch;
  }
});

test("a recovery that times out leaves sign-in behaviour untouched", async () => {
  // The deadlines must not be coupled through any shared state — no module-level "current timeout",
  // no leaked AbortController. A repair giving up early must be invisible to the next sign-in.
  const g = globalThis as unknown as { fetch: unknown };
  const realFetch = g.fetch;
  const observed = { aborts: 0 };
  try {
    await withMockTimers(async () => {
      g.fetch = wedged(observed);
      let recovered: number | null | "pending" = "pending";
      const recovery = resolveEngineUserId(GOOGLE_TOKEN).then((v) => { recovered = v; });
      mock.timers.tick(2001);
      await recovery;
      assert.equal(recovered, null, "recovery timed out");

      // Now a sign-in, against an engine that answers. It must succeed normally.
      g.fetch = async () => ok({ userId: 42 });
      assert.equal(await upsertEngineUser(IDENTITY), 42, "sign-in still resolves an id");

      // And a sign-in against a still-wedged engine must still get the FULL default deadline —
      // the earlier timeout must not have shortened anything.
      g.fetch = wedged(observed);
      let signInSettled = false;
      const signIn = upsertEngineUser(IDENTITY).then(() => { signInSettled = true; });
      mock.timers.tick(2001);
      await flush();
      assert.equal(signInSettled, false, "sign-in's deadline is still 6s, not recovery's 2s");
      mock.timers.tick(4000);
      await signIn;
      assert.equal(signInSettled, true);
    });
  } finally {
    g.fetch = realFetch;
  }
});

test("upsertEngineUser accepts a per-call deadline, and omitting it changes nothing", async () => {
  // The override is what makes the deadline caller-specific rather than global. Omitting it must be
  // byte-identical to before this existed, which is what keeps both sign-in paths untouched.
  const g = globalThis as unknown as { fetch: unknown };
  const realFetch = g.fetch;
  const observed = { aborts: 0 };
  g.fetch = wedged(observed);
  try {
    await withMockTimers(async () => {
      let result: number | null | "pending" = "pending";
      const p = upsertEngineUser(IDENTITY, { timeoutMs: 500 }).then((v) => { result = v; });
      mock.timers.tick(499);
      await flush();
      assert.equal(result, "pending");
      mock.timers.tick(2);
      await p;
      assert.equal(result, null, "the per-call deadline is honoured, and a timeout is still `null`");
    });

    await withMockTimers(async () => {
      let settled = false;
      const p = upsertEngineUser(IDENTITY, {}).then(() => { settled = true; });
      mock.timers.tick(2001);
      await flush();
      assert.equal(settled, false, "an empty options object must not become a zero deadline");
      mock.timers.tick(4000);
      await p;
      assert.equal(settled, true);
    });
  } finally {
    g.fetch = realFetch;
  }
});

// --------------------------------------------------------------------------------------------------
// refreshProfile (S2b).
//
// The engine refreshes a user's email and display name on every upsert that supplies them. Right for
// sign-in, whose profile is a freshly minted OAuth response; wrong for recovery, whose profile comes
// from a session token up to 30 days old and would otherwise write itself over whatever a newer
// sign-in already stored.
//
// The wire is the contract here, so these assert on the serialized body rather than on arguments.
// --------------------------------------------------------------------------------------------------

/** The parsed request body of the Nth engine call. */
function bodyOf(calls: Call[], n = 0): Record<string, unknown> {
  return JSON.parse(calls[n]!.init.body as string) as Record<string, unknown>;
}

test("a sign-in body does not carry refreshProfile at all", async () => {
  // Not "carries true" — ABSENT. `undefined` is dropped by JSON.stringify, so a sign-in request is
  // byte-identical to the one sent before this field existed. That is what keeps an engine which
  // predates it behaving identically, and what makes reverting either tier alone safe.
  await withFetch(() => ok({ userId: 42 }), async (calls) => {
    await upsertEngineUser(IDENTITY);
    const body = bodyOf(calls);
    assert.equal("refreshProfile" in body, false, `sign-in body was ${JSON.stringify(body)}`);
    assert.deepEqual(body, {
      provider: "google",
      providerAccountId: "108461123456789012345",
      email: "reader@example.com",
      displayName: "A Reader",
    }, "the sign-in request must be exactly what it was before S2b");
  });
});

test("the recovery body carries refreshProfile: false", async () => {
  await withResolver(() => ok({ userId: 42 }), async () => {
    const g = globalThis as unknown as { fetch: unknown };
    const seen: Record<string, unknown>[] = [];
    const counting = g.fetch as (u: string, i: RequestInit) => Promise<unknown>;
    g.fetch = async (u: string, i: RequestInit) => {
      seen.push(JSON.parse(i.body as string) as Record<string, unknown>);
      return counting(u, i);
    };

    assert.equal(await resolveEngineUserId(GOOGLE_TOKEN), 42);
    assert.equal(seen.length, 1);
    assert.equal(seen[0]!.refreshProfile, false, `recovery body was ${JSON.stringify(seen[0])}`);
    // The identity key is unchanged — recovery must still resolve the SAME user, only without
    // writing the profile.
    assert.equal(seen[0]!.providerAccountId, SUB);
  });
});

test("an explicit refreshProfile: true serializes as true, not as absent", async () => {
  // The pass-through has to work in both directions, or `false` arriving would be luck rather than
  // wiring.
  await withFetch(() => ok({ userId: 42 }), async (calls) => {
    await upsertEngineUser({ ...IDENTITY, refreshProfile: true });
    assert.equal(bodyOf(calls).refreshProfile, true);
  });
  await withFetch(() => ok({ userId: 42 }), async (calls) => {
    await upsertEngineUser({ ...IDENTITY, refreshProfile: false });
    assert.equal(bodyOf(calls).refreshProfile, false);
  });
});

test("the flag changes nothing else about the request", async () => {
  // Same URL, method, headers and cache policy; the body differs by one key. A regression that also
  // moved the identity key or dropped the secret would otherwise hide behind a passing flag test.
  const bodies: Record<string, unknown>[] = [];
  const metas: string[] = [];
  await withFetch(() => ok({ userId: 42 }), async (calls) => {
    await upsertEngineUser(IDENTITY);
    await upsertEngineUser({ ...IDENTITY, refreshProfile: false });
    for (const c of calls) {
      bodies.push(JSON.parse(c.init.body as string) as Record<string, unknown>);
      metas.push(`${c.url}|${c.init.method}|${c.init.cache}|${JSON.stringify(c.init.headers)}`);
    }
  }, "s3cret");
  assert.equal(metas[0], metas[1], "url, method, cache and headers must be identical");
  const { refreshProfile, ...withoutFlag } = bodies[1]!;
  assert.equal(refreshProfile, false);
  assert.deepEqual(withoutFlag, bodies[0], "the bodies differ by the flag and nothing else");
});
