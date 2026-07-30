// Session recovery, end to end through the REAL next-auth session route (node --test).
//
// WHY THIS EXISTS, when engine-identity.test.ts and auth-callbacks.test.ts already cover the resolver
// and the callback in isolation: neither can show the hop between them. The whole design rests on one
// claim about machinery we do not own —
//
//     a mutation `callbacks.jwt` makes is visible to `engineAuthHeaders()` in the SAME request,
//     with no cookie written
//
// — because `next-auth/core/routes/session.js` builds the session object from the callback's RETURN
// VALUE and assigns it to the response body. That is why the plan's commit 6 (a second recovery call
// site inside `engineAuthHeaders`) was deleted. If the claim is false, recovery silently repairs only
// the NEXT request and every current render still 401s, and no unit test in this repo would notice.
//
// So this file drives the actual `AuthHandler`. Assertions here are about the integration only; the
// caching, backoff, reason codes and guard semantics are asserted where they live, and are not
// repeated.
//
// ---------------------------------------------------------------------------------------------
// DEPENDENCE ON NEXT-AUTH INTERNALS — read before "fixing" a failure here.
//
// `next-auth/core` is NOT in the package's `exports` map, so it cannot be imported by specifier; it is
// loaded below by resolving the package entry and walking to `core/index.js`. That is a deliberate
// reach into a private module, and it is the point: this is an ASSUMPTION DETECTOR in the sense of
// docs/CONCURRENCY_TESTING.md, not an ordinary regression test. It fails when the world moves.
//
// If a next-auth upgrade breaks the load or changes the shape asserted here, the correct response is
// to RE-VALIDATE THE DESIGN — re-read `core/routes/session.js`, confirm the session is still built
// from the callback's return value, and update docs/SESSION_IDENTITY_RECOVERY_DESIGN.md §2a — and only
// then adjust this file. Deleting the test, or loosening it until it passes, silently reinstates the
// question commit 6 existed to answer.
//
// Pinned: next-auth 4.x, `core/index.js` exporting `AuthHandler({ options, req })` and answering
// `action: "session"` with `{ body, cookies }`.
// ---------------------------------------------------------------------------------------------
import { test } from "node:test";
import assert from "node:assert/strict";
import { createRequire } from "node:module";
import path from "node:path";
import { encode } from "next-auth/jwt";

import { jwtCallback, sessionCallback } from "./auth-callbacks.ts";
import { __identityCacheStats, __resetIdentityCache } from "./engine-identity.ts";

const require_ = createRequire(import.meta.url);

const { AuthHandler } = ((): { AuthHandler: (arg: unknown) => Promise<AuthResponse> } => {
  try {
    const entry = require_.resolve("next-auth");
    return require_(path.join(path.dirname(entry), "core", "index.js"));
  } catch (err) {
    throw new Error(
      "Could not load next-auth's core AuthHandler, which this test drives on purpose (see the " +
        "header). next-auth's internal layout has probably changed. Re-validate " +
        "docs/SESSION_IDENTITY_RECOVERY_DESIGN.md §2a before touching this file. " +
        `Original error: ${(err as Error).message}`,
    );
  }
})();

interface AuthResponse {
  body?: { user?: unknown; expires?: string; engineUserId?: number };
  cookies?: { name: string; value: string }[];
}

const SECRET = "test-secret-at-least-32-characters-long!";
const SUB = "108461123456789012345";
const COOKIE = "next-auth.session-token";

/** `authOptions` as lib/auth.ts declares them, minus the providers — the session route never uses
 *  them, and importing lib/auth.ts would pull in CommonJS provider modules bare node cannot load. */
const authOptions = {
  secret: SECRET,
  session: { strategy: "jwt" as const },
  callbacks: { jwt: jwtCallback, session: sessionCallback },
  providers: [],
};

/**
 * One `getServerSession()` from a server component, mirroring the RSC branch of
 * `next-auth/next/index.js`: build `req` from the cookie jar, hand `AuthHandler` a `res` whose
 * `setCookie` is a **no-op**, and return the body.
 *
 * `cookiesQueued` is what that no-op throws away — the reason a heal is visible immediately but
 * persists only when a route that can actually set cookies (`/api/auth/session`) runs the same path.
 */
async function getServerSession(
  token: Record<string, unknown> | null,
  opts: { rawCookie?: string } = {},
): Promise<{ session: AuthResponse["body"]; cookiesQueued: number }> {
  const cookies: Record<string, string> = {};
  if (opts.rawCookie !== undefined) cookies[COOKIE] = opts.rawCookie;
  else if (token) cookies[COOKIE] = await encode({ token, secret: SECRET });

  const res = await AuthHandler({
    options: authOptions,
    req: { action: "session", method: "GET", cookies, headers: {}, query: {} },
  });
  return { session: res.body, cookiesQueued: (res.cookies ?? []).length };
}

/** What lib/engine-auth.ts derives from the session it just read. */
const engineAuthHeaders = (session: AuthResponse["body"]) =>
  typeof session?.engineUserId === "number" ? { "X-IH-User-Id": String(session.engineUserId) } : {};

/** A session whose sign-in could not reach the engine: claims present, no engine id. */
const brokenToken = () => ({
  name: "A Reader", email: "reader@example.com", sub: SUB,
  provider: "google", providerAccountId: SUB,
});

/** Run with the engine stubbed and the identity cache clear either side. */
async function withEngine(
  reply: () => unknown,
  fn: (state: { fetches: number; bodies: Record<string, unknown>[] }) => Promise<void>,
): Promise<void> {
  const g = globalThis as unknown as { fetch: unknown };
  const realFetch = g.fetch;
  const realWarn = console.warn;
  const state: { fetches: number; bodies: Record<string, unknown>[] } = { fetches: 0, bodies: [] };

  __resetIdentityCache();
  g.fetch = async (_url: string, init?: RequestInit) => {
    state.fetches += 1;
    if (init?.body) state.bodies.push(JSON.parse(init.body as string) as Record<string, unknown>);
    const value = reply();
    if (value instanceof Error) throw value;
    return value;
  };
  console.warn = () => {};                                   // recovery log lines, asserted elsewhere
  try {
    await fn(state);
  } finally {
    g.fetch = realFetch;
    console.warn = realWarn;
    __resetIdentityCache();
  }
}

const ok = (userId: number) => ({ ok: true, json: async () => ({ userId }) });
const down = (status: number) => ({ ok: false, status, json: async () => ({}) });

// ==================================================================================================

test("THE CLAIM: a healed id is in the session body of the same request that healed it", async () => {
  // If this fails, commit 6 was not redundant and every server render for a broken session still 401s
  // until the client refetches. Nothing else in the suite would tell you.
  await withEngine(() => ok(42), async (state) => {
    const token = brokenToken();
    assert.equal("engineUserId" in token, false, "precondition: the token has no id");

    const { session } = await getServerSession(token);

    assert.equal(session?.engineUserId, 42, "the session route must expose the healed id");
    assert.deepEqual(engineAuthHeaders(session), { "X-IH-User-Id": "42" },
      "and engineAuthHeaders must derive the attribution header from it, on THIS request");
    assert.equal(state.fetches, 1);
  });
});

test("visibility does not depend on the cookie: the route queues cookies the RSC path discards", async () => {
  // The other half of §2a, and the reason the heal is not durable from a server render. The route
  // wants to write the re-encoded token; `getServerSession`'s no-op `setCookie` drops it. Persistence
  // therefore waits for /api/auth/session — which SessionProvider fetches on mount.
  await withEngine(() => ok(42), async () => {
    const { session, cookiesQueued } = await getServerSession(brokenToken());
    assert.ok(cookiesQueued > 0, "the route re-encodes the token and queues it");
    assert.equal(session?.engineUserId, 42, "yet the id is already visible, cookie or no cookie");
  });
});

test("a request with no session cookie never reaches the callback, so nothing could recover it", async () => {
  // Exit A of §2b. Asserted because it is half the proof that ONE call site suffices: the paths that
  // skip `callbacks.jwt` are exactly the paths with no token, where a second call site would have
  // nothing to work from either.
  await withEngine(() => ok(42), async (state) => {
    const { session } = await getServerSession(null);
    assert.deepEqual(session, {}, "no cookie ⇒ empty session body");
    assert.equal(state.fetches, 0, "and no recovery was attempted, because there is no identity");
    assert.equal(__identityCacheStats().entries, 0);
  });
});

test("an undecodable cookie never reaches the callback either, and is cleared", async () => {
  // Exit B: expired, tampered, or encrypted under a rotated NEXTAUTH_SECRET.
  await withEngine(() => ok(42), async (state) => {
    const { session, cookiesQueued } = await getServerSession(null, { rawCookie: "not-a-real-jwe" });
    assert.deepEqual(session, {}, "undecodable ⇒ empty session body");
    assert.equal(state.fetches, 0);
    assert.ok(cookiesQueued > 0, "and the route clears the bad cookie chunks");
  });
});

test("a healthy session goes through the whole route without touching the engine", async () => {
  // `callbacks.jwt` runs on EVERY getServerSession, so a regression here is one engine call per server
  // render for every signed-in reader. The route-level call count is the only honest check.
  await withEngine(() => ok(999), async (state) => {
    for (let i = 0; i < 10; i++) {
      const { session } = await getServerSession({ ...brokenToken(), engineUserId: 42 });
      assert.equal(session?.engineUserId, 42, "the token's own id is passed through untouched");
    }
    assert.equal(state.fetches, 0);
  });
});

test("concurrent renders of one identity share a single upsert, and identities never cross", async () => {
  // A page fanning out to several route handlers is the normal case: each calls getServerSession, so
  // each runs the callback. They must coalesce — and coalescing must be keyed, or one reader's render
  // could be attributed to another.
  await withEngine(() => ok(0), async (state) => {
    const g = globalThis as unknown as { fetch: unknown };
    g.fetch = async (_url: string, init: RequestInit) => {
      state.fetches += 1;
      const { providerAccountId } = JSON.parse(init.body as string) as { providerAccountId: string };
      await new Promise((r) => setTimeout(r, 10));           // hold the window open
      return { ok: true, json: async () => ({ userId: Number(providerAccountId.slice(3)) }) };
    };

    const readers = [1, 2, 3];
    const results = await Promise.all(
      readers.flatMap((n) =>
        Array.from({ length: 12 }, () =>
          getServerSession({ ...brokenToken(), providerAccountId: `id-${n}`, sub: `id-${n}` })
            .then((r) => ({ n, id: r.session?.engineUserId })),
        ),
      ),
    );

    assert.equal(state.fetches, 3, `36 concurrent renders made ${state.fetches} upserts, expected 3`);
    for (const { n, id } of results) assert.equal(id, n, "every render got ITS OWN reader's id");
    assert.equal(__identityCacheStats().inflight, 0, "the in-flight map must drain");
  });
});

test("an engine that is down leaves the reader signed in, merely un-attributed", async () => {
  // Fail-soft, at the level that matters: recovery failing must not empty the session or sign anyone
  // out. This is today's behaviour, and recovery may not make it worse.
  await withEngine(() => down(503), async (state) => {
    const { session } = await getServerSession(brokenToken());
    assert.ok(session?.user, "the reader is still signed in");
    assert.equal(session?.engineUserId, undefined, "just without an engine id");
    assert.deepEqual(engineAuthHeaders(session), {}, "so calls go out anonymous, exactly as before");

    for (let i = 0; i < 10; i++) await getServerSession(brokenToken());
    assert.equal(state.fetches, 1, "and the backoff holds across renders, not just across resolver calls");
  });
});

test("a failed sign-in and the render that repairs it are two upserts, not three", async () => {
  // The `!account` guard end to end. Sign-in makes its own attempt; the next render makes recovery's.
  // A third call would mean the guard is gone and a failing engine is being hit twice per sign-in.
  await withEngine(() => down(503), async (state) => {
    await jwtCallback!({
      token: { name: "A Reader", email: "reader@example.com", sub: SUB },
      account: { provider: "google", providerAccountId: SUB, type: "oauth" },
      profile: { email: "reader@example.com", name: "A Reader" },
      trigger: "signIn",
    } as never);
    assert.equal(state.fetches, 1, "sign-in: one attempt, and recovery did not pile on");

    __resetIdentityCache();                                  // a later request, past the backoff
    await getServerSession(brokenToken());
    assert.equal(state.fetches, 2, "the repair attempt is the second, and there is no third");
  });
});

test("RWE_IDENTITY_RECOVERY=0 turns the whole route back into pre-recovery behaviour", async () => {
  // The rollback lever, exercised through the real path rather than against the resolver alone.
  const real = process.env.RWE_IDENTITY_RECOVERY;
  process.env.RWE_IDENTITY_RECOVERY = "0";
  try {
    await withEngine(() => ok(42), async (state) => {
      const broken = await getServerSession(brokenToken());
      assert.equal(broken.session?.engineUserId, undefined, "no repair");
      assert.ok(broken.session?.user, "but the reader keeps their session");
      assert.equal(state.fetches, 0);

      const healthy = await getServerSession({ ...brokenToken(), engineUserId: 42 });
      assert.equal(healthy.session?.engineUserId, 42,
        "and a session that already has an id is never de-attributed by the switch");
    });
  } finally {
    if (real === undefined) delete process.env.RWE_IDENTITY_RECOVERY;
    else process.env.RWE_IDENTITY_RECOVERY = real;
  }
});

test("the recovery upsert carries refreshProfile: false through the whole path", async () => {
  // S2b end to end. The unit test asserts the body the resolver builds; this asserts what actually
  // leaves the process when a real getServerSession drives a real callbacks.jwt — the only place a
  // wrapper, a re-serialisation or a stray spread could drop the flag between the two.
  await withEngine(() => ok(42), async (state) => {
    const { session } = await getServerSession(brokenToken());
    assert.equal(session?.engineUserId, 42, "precondition: recovery ran");
    assert.equal(state.bodies.length, 1);
    assert.equal(state.bodies[0]!.refreshProfile, false,
      `recovery sent ${JSON.stringify(state.bodies[0])}`);
    // Still the same identity key: this suppresses a profile write, never a different lookup.
    assert.equal(state.bodies[0]!.providerAccountId, SUB);
  });
});

test("a sign-in through the callback sends no refreshProfile, even alongside recovery", async () => {
  // Both callers in one process, so a module-level default leaking from one to the other would show.
  await withEngine(() => ok(42), async (state) => {
    await jwtCallback!({
      token: { name: "A Reader", email: "reader@example.com", sub: SUB },
      account: { provider: "google", providerAccountId: SUB, type: "oauth" },
      profile: { email: "reader@example.com", name: "A Reader" },
      trigger: "signIn",
    } as never);
    assert.equal("refreshProfile" in state.bodies[0]!, false,
      `sign-in sent ${JSON.stringify(state.bodies[0])}`);

    __resetIdentityCache();
    await getServerSession(brokenToken());
    assert.equal(state.bodies[1]!.refreshProfile, false, "and recovery still sends it");
  });
});
