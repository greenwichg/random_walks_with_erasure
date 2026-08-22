// Every branch of the authentication decision, with no server, database, or network in sight.
//
// The cases that matter are the ones a manual test never reaches: a revoked token, a token from
// another account, an engine that cannot answer, a token presented where only a session is allowed.
// Each is a probe stub here, which is the point of `decideAuth` being pure — the interesting inputs
// are trivially constructible, so there is no excuse for leaving them uncovered.
import { test } from "node:test";
import assert from "node:assert/strict";

import {
  bearerFromHeader,
  codeForFailure,
  decideAuth,
  statusForFailure,
  type AuthProbes,
  type TokenResolution,
} from "./auth-decision.ts";

const UID = 4242;
const OTHER_UID = 77;

/** Probes that record what they were asked, so a test can assert what was NOT looked up. */
function probes(
  opts: { session?: number | null; tokens?: Record<string, TokenResolution> } = {},
): AuthProbes & { sessionCalls: number; tokenCalls: string[] } {
  const state = {
    sessionCalls: 0,
    tokenCalls: [] as string[],
    sessionUserId: async () => {
      state.sessionCalls++;
      return opts.session ?? null;
    },
    resolveBearer: async (token: string) => {
      state.tokenCalls.push(token);
      return opts.tokens?.[token] ?? ({ status: "rejected" } as TokenResolution);
    },
  };
  return state;
}

const bearer = (token: string) => `Bearer ${token}`;

/* -- 1. the web: a valid session cookie ------------------------------------------------------- */

test("a valid session resolves to its engine user, via session", async () => {
  const outcome = await decideAuth(null, probes({ session: UID }));
  assert.deepEqual(outcome, { ok: true, userId: UID, via: "session" });
});

test("a session is resolved WITHOUT consulting the token resolver", async () => {
  // The regression this pins: the web must not acquire a dependency on the engine's token endpoint.
  // If a future edit reorders the ladder, every signed-in page gains an engine round trip it never
  // had, and an engine with a broken resolver starts breaking sign-in.
  const p = probes({ session: UID });
  await decideAuth(null, p);
  assert.deepEqual(p.tokenCalls, []);
});

test("a session WINS over a bearer token presented on the same request", async () => {
  // A mobile web view can carry both. Session-first is what `/api/me/reads` has always done, and
  // reversing it would silently re-attribute a signed-in reader's writes to whoever's token the
  // client happened to attach.
  const p = probes({ session: UID, tokens: { tok: { status: "ok", userId: OTHER_UID } } });
  const outcome = await decideAuth(bearer("tok"), p);
  assert.deepEqual(outcome, { ok: true, userId: UID, via: "session" });
  assert.deepEqual(p.tokenCalls, [], "the token was not even resolved");
});

/* -- 2. mobile / extension: a valid bearer token ---------------------------------------------- */

test("a valid bearer token resolves to its engine user, via bearer", async () => {
  const outcome = await decideAuth(
    bearer("good-token"),
    probes({ tokens: { "good-token": { status: "ok", userId: UID } } }),
  );
  assert.deepEqual(outcome, { ok: true, userId: UID, via: "bearer" });
});

test("the token names the user — never the caller", async () => {
  // The whole trust boundary in one assertion: the id comes back from the resolver, so a client
  // cannot assert an identity by presenting a token plus a user id of its choosing.
  const outcome = await decideAuth(
    bearer("someone-elses"),
    probes({ tokens: { "someone-elses": { status: "ok", userId: OTHER_UID } } }),
  );
  assert.equal(outcome.ok && outcome.userId, OTHER_UID);
});

test("the scheme is case-insensitive and the token is trimmed", async () => {
  for (const header of ["Bearer tok", "bearer tok", "BEARER tok", "  Bearer   tok  "]) {
    const outcome = await decideAuth(header, probes({ tokens: { tok: { status: "ok", userId: UID } } }));
    assert.equal(outcome.ok, true, `${JSON.stringify(header)} should parse`);
  }
});

/* -- 3. an invalid token ---------------------------------------------------------------------- */

test("a token the engine does not recognise is refused with 401", async () => {
  const outcome = await decideAuth(bearer("never-issued"), probes());
  assert.deepEqual(outcome, { ok: false, reason: "invalid-token" });
  assert.equal(statusForFailure("invalid-token"), 401);
  assert.equal(codeForFailure("invalid-token"), "unauthorized");
});

test("garbage in the Authorization header is refused, not ignored", async () => {
  // `Bearer <junk>` is a presented credential. Treating an unparseable one as "no credential" is
  // how a client ends up quietly served the anonymous answer instead of being told to re-authenticate.
  const outcome = await decideAuth(bearer("!!! not a token !!!"), probes());
  assert.deepEqual(outcome, { ok: false, reason: "invalid-token" });
});

/* -- 4. a revoked token ----------------------------------------------------------------------- */

test("a revoked token is refused — revocation is deletion, and deletion is `rejected`", async () => {
  // Revoking deletes the row (examples/store.py: revoke_token), so the engine's resolver answers
  // exactly as it does for a token that never existed. That equivalence is the reason a single
  // `rejected` covers revoked, unknown, and — should the token model ever grow expiry — expired.
  const tokens: Record<string, TokenResolution> = { live: { status: "ok", userId: UID } };
  assert.equal((await decideAuth(bearer("live"), probes({ tokens }))).ok, true);

  delete tokens.live; // ← revoke
  assert.deepEqual(await decideAuth(bearer("live"), probes({ tokens })), {
    ok: false,
    reason: "invalid-token",
  });
});

test("a rejected token never falls through to the anonymous answer", async () => {
  // The security-critical property of the whole design. `anonymous` is what makes a route serve its
  // public/demo body; a failed credential must never reach it, on any route, ever.
  const outcome = await decideAuth(bearer("revoked"), probes());
  assert.notEqual(outcome.ok === false && outcome.reason, "anonymous");
});

/* -- 5. missing authentication ---------------------------------------------------------------- */

test("no session and no header is `anonymous` — the route decides what that means", async () => {
  assert.deepEqual(await decideAuth(null, probes()), { ok: false, reason: "anonymous" });
  assert.deepEqual(await decideAuth(undefined, probes()), { ok: false, reason: "anonymous" });
  assert.deepEqual(await decideAuth("", probes()), { ok: false, reason: "anonymous" });
});

test("a non-bearer Authorization scheme is anonymous, not a failed token", async () => {
  // Basic/Digest are not credentials this app issues; nothing was presented that we could refuse.
  for (const header of ["Basic dXNlcjpwYXNz", "Digest username=x", "Bearer", "Bearer   "]) {
    const outcome = await decideAuth(header, probes());
    assert.deepEqual(outcome, { ok: false, reason: "anonymous" }, `${JSON.stringify(header)}`);
  }
});

test("statuses: anonymous → 401", () => {
  assert.equal(statusForFailure("anonymous"), 401);
  assert.equal(codeForFailure("anonymous"), "unauthorized");
});

/* -- 6. a route that accepts a session only --------------------------------------------------- */

test("a bearer token is refused with 403 where the route takes a session only", async () => {
  const p = probes({ tokens: { tok: { status: "ok", userId: UID } } });
  const outcome = await decideAuth(bearer("tok"), p, { acceptBearer: false });
  assert.deepEqual(outcome, { ok: false, reason: "bearer-not-accepted" });
  assert.equal(statusForFailure("bearer-not-accepted"), 403);
  assert.equal(codeForFailure("bearer-not-accepted"), "forbidden");
});

test("a session-only route does not resolve the token it is going to refuse", async () => {
  // Not an optimisation: resolving stamps `last_used_at`, so a refused request would leave a trace
  // that says the token was used — on a route where it was not, and never could be.
  const p = probes({ tokens: { tok: { status: "ok", userId: UID } } });
  await decideAuth(bearer("tok"), p, { acceptBearer: false });
  assert.deepEqual(p.tokenCalls, []);
});

test("a session-only route still accepts the session", async () => {
  const outcome = await decideAuth(null, probes({ session: UID }), { acceptBearer: false });
  assert.deepEqual(outcome, { ok: true, userId: UID, via: "session" });
});

test("acceptBearer defaults to true — a route opts OUT, never in", async () => {
  // Default-open is the right default here and only here: forgetting the option on a new route
  // makes it work for mobile, whereas default-closed would make it silently web-only, which is the
  // exact failure Phase 1 exists to end.
  const p = probes({ tokens: { tok: { status: "ok", userId: UID } } });
  assert.equal((await decideAuth(bearer("tok"), p, {})).ok, true);
  assert.equal((await decideAuth(bearer("tok"), p)).ok, true);
});

/* -- 7. the engine cannot say ----------------------------------------------------------------- */

test("an unreachable engine is 503, never 401", async () => {
  // The bug this prevents in production: every deploy restarts the engine for a second or two. If
  // that window answered 401, every mobile client mid-request would be told its credential is dead
  // — and a client that signs out on 401 would sign the reader out on every deploy.
  const outcome = await decideAuth(
    bearer("tok"),
    probes({ tokens: { tok: { status: "unavailable" } } }),
  );
  assert.deepEqual(outcome, { ok: false, reason: "engine-unavailable" });
  assert.equal(statusForFailure("engine-unavailable"), 503);
  assert.equal(codeForFailure("engine-unavailable"), "engine_unavailable");
});

/* -- the header parser ------------------------------------------------------------------------ */

test("bearerFromHeader: the one parser, and what it rejects", () => {
  assert.equal(bearerFromHeader("Bearer abc123"), "abc123");
  assert.equal(bearerFromHeader("bearer  abc123  "), "abc123");
  assert.equal(bearerFromHeader("Bearer a b c"), "a b c", "tokens are opaque — no inner parsing");
  assert.equal(bearerFromHeader(null), null);
  assert.equal(bearerFromHeader(undefined), null);
  assert.equal(bearerFromHeader(""), null);
  assert.equal(bearerFromHeader("Bearer"), null);
  assert.equal(bearerFromHeader("Bearer "), null);
  assert.equal(bearerFromHeader("Basic abc123"), null);
  assert.equal(bearerFromHeader("abc123"), null, "a bare token is not a bearer header");
});
