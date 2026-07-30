// auth-callbacks tests (node --test, type-stripped like the other lib tests).
//
// The `jwt` callback is where a session gets its engine identity, and now where it records WHICH
// identity it belongs to. Both matter beyond sign-in: `docs/SESSION_IDENTITY_RECOVERY_DESIGN.md`
// recovers a session that lost its engine id, and the claims pinned here are the key it recovers by.
//
// First test coverage this file has had, so a few properties that predate this commit are pinned too.
import { test } from "node:test";
import assert from "node:assert/strict";
import { decode, encode } from "next-auth/jwt";

import { jwtCallback, sessionCallback } from "./auth-callbacks.ts";

const SUB = "108461123456789012345";
const SECRET = "test-secret-at-least-32-characters-long!";

/** The `jwt` callback, with the argument shapes NextAuth actually passes. */
const jwt = jwtCallback!;

/** Stub the engine upsert so no test touches the network. */
async function withEngine(userId: number | null, fn: () => Promise<void>): Promise<void> {
  const g = globalThis as unknown as { fetch: unknown };
  const real = g.fetch;
  g.fetch = async () => ({ ok: userId !== null, json: async () => ({ userId }) });
  try {
    await fn();
  } finally {
    g.fetch = real;
  }
}

/** A fresh sign-in argument set per call.
 *
 *  Deliberately a factory: `jwt` MUTATES the token it is handed (which is what NextAuth expects), so a
 *  shared fixture would carry one test's `engineUserId` into the next — it did, while this file was
 *  being written, and the test that caught it was the one asserting the id stays unset. */
const googleSignIn = () => ({
  // What next-auth/core/routes/callback.js builds for an OAuth sign-in.
  account: { provider: "google", providerAccountId: SUB, type: "oauth" },
  profile: { email: "reader@example.com", name: "A Reader" },
  token: { name: "A Reader", email: "reader@example.com", sub: SUB },
});

test("a new Google session records the provider and the account id", async () => {
  await withEngine(42, async () => {
    const token = await jwt({ ...googleSignIn(), trigger: "signIn" } as never);
    assert.equal(token.provider, "google");
    assert.equal(token.providerAccountId, SUB);
    assert.equal(token.engineUserId, 42);
  });
});

test("the recorded account id is the value the engine keys the identity on", async () => {
  // Not merely "some id": recovery re-resolves through the same keyed upsert, so a claim that did not
  // match what sign-in sent would resolve to a different user or to nothing.
  let sentAccountId: string | undefined;
  const g = globalThis as unknown as { fetch: unknown };
  const real = g.fetch;
  g.fetch = async (_url: string, init: RequestInit) => {
    sentAccountId = (JSON.parse(init.body as string) as { providerAccountId: string }).providerAccountId;
    return { ok: true, json: async () => ({ userId: 42 }) };
  };
  try {
    const token = await jwt({ ...googleSignIn(), trigger: "signIn" } as never);
    assert.equal(token.providerAccountId, sentAccountId);
  } finally {
    g.fetch = real;
  }
});

test("a dev sign-in records the provider but NOT an account id", async () => {
  // NextAuth sets `account.providerAccountId` to `user.id` for the credentials provider, and
  // `authorize()` sets that to the engine user id — not the email the dev upsert keys on. Recording it
  // under this name would promise something it is not.
  const token = await jwt({
    token: { name: "Demo Reader", email: "demo@infodiet.local", sub: "31" },
    account: { provider: "dev", providerAccountId: "31", type: "credentials" },
    user: { id: "31", engineUserId: 31 },
    trigger: "signIn",
  } as never);
  assert.equal(token.provider, "dev");
  assert.equal(token.providerAccountId, undefined);
  assert.equal(token.engineUserId, 31);
});

test("an existing session without the claims passes through untouched", async () => {
  // Every request after sign-in: `account` is absent, so nothing is added and nothing is stripped.
  // This is what keeps sessions minted before this commit working.
  const legacy = { name: "A Reader", email: "reader@example.com", sub: SUB, engineUserId: 42 };
  const token = await jwt({ token: { ...legacy } } as never);
  assert.deepEqual(token, legacy);
  assert.equal("provider" in token, false);
  assert.equal("providerAccountId" in token, false);
});

test("a session that already has the claims keeps them across later invocations", async () => {
  const carried = {
    name: "A Reader", email: "reader@example.com", sub: SUB, engineUserId: 42,
    provider: "google", providerAccountId: SUB,
  };
  const token = await jwt({ token: { ...carried } } as never);
  assert.deepEqual(token, carried);
});

test("an engine that is down at sign-in leaves engineUserId unset — the claims are still recorded", async () => {
  // The failure recovery exists for. The claims are what make recovery possible later, so they must be
  // written even on the path where the engine call failed.
  await withEngine(null, async () => {
    const token = await jwt({ ...googleSignIn(), trigger: "signIn" } as never);
    assert.equal(token.engineUserId, undefined);
    assert.equal(token.provider, "google");
    assert.equal(token.providerAccountId, SUB);
  });
});

test("the claims survive JWT encode/decode, and the token stays well inside the cookie limit", async () => {
  const withClaims = {
    name: "A Reader", email: "reader@example.com",
    picture: "https://lh3.googleusercontent.com/a/ACg8ocKq7pQ2mZ9vXbY3nR4tU5wS6dF7gH8jK9lM0=s96-c",
    sub: SUB, engineUserId: 42, provider: "google", providerAccountId: SUB,
  };
  const { provider: _p, providerAccountId: _a, ...withoutClaims } = withClaims;

  const encoded = await encode({ token: withClaims, secret: SECRET });
  const baseline = await encode({ token: withoutClaims, secret: SECRET });

  const decoded = await decode({ token: encoded, secret: SECRET });
  assert.equal(decoded?.provider, "google");
  assert.equal(decoded?.providerAccountId, SUB);

  // A browser rejects a cookie over ~4096 bytes; NextAuth chunks beyond that, which is transparent but
  // worth never needing. The claims cost ~85 bytes of a ~3.5 KiB headroom.
  assert.ok(encoded.length < 4096, `session cookie is ${encoded.length} bytes`);
  assert.ok(encoded.length - baseline.length < 200,
    `claims added ${encoded.length - baseline.length} bytes`);

  // v4 encrypts the token (JWE, five parts) rather than merely signing it, so the account id is not
  // readable by anything without NEXTAUTH_SECRET.
  assert.equal(encoded.split(".").length, 5, "the session token must be encrypted, not just signed");
});

test("a legacy token decodes without the claims and stays valid", async () => {
  const legacy = { name: "A Reader", email: "reader@example.com", sub: SUB, engineUserId: 42 };
  const decoded = await decode({ token: await encode({ token: legacy, secret: SECRET }), secret: SECRET });
  assert.equal(decoded?.sub, SUB);
  assert.equal(decoded?.provider, undefined);
  assert.equal(decoded?.providerAccountId, undefined);
  // And `sub` — the fallback recovery keys on for exactly these tokens — is intact.
  assert.equal(decoded?.sub, SUB);
});

test("the claims stay on the JWT and never reach the browser session", async () => {
  const session = sessionCallback!;
  const result = await session({
    session: { user: { name: "A Reader", email: "reader@example.com" }, expires: "2099-01-01" },
    token: { sub: SUB, engineUserId: 42, provider: "google", providerAccountId: SUB },
  } as never);
  assert.equal((result as { engineUserId?: number }).engineUserId, 42);
  assert.equal("provider" in result, false);
  assert.equal("providerAccountId" in result, false);
});
