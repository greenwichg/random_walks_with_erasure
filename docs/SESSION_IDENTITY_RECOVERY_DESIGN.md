# Design Proposal — Recovering a Session With No Engine Identity

**Status:** proposal, not implemented. Written as a follow-up to the onboarding-gate work
([`ONBOARDING.md`](ONBOARDING.md) §8), which surfaced the failure but deliberately left it alone:
it predates that work and affects every authenticated surface, not just onboarding.

**Goal:** whenever a valid authenticated session exists but its token carries no engine user id,
resolve one automatically, without the reader having to sign out and back in.

---

## 1. The failure

`lib/auth.ts` resolves the engine user id exactly once, during sign-in:

```ts
async jwt({ token, account, profile, user }) {
  if (account?.provider === "google") {
    const engineUserId = await upsertEngineUser({ ... });   // one engine call
    if (engineUserId != null) token.engineUserId = engineUserId;
  }
  if (typeof user?.engineUserId === "number") token.engineUserId = user.engineUserId;
  return token;
}
```

`account` is only present on the sign-in invocation (verified in
`next-auth/core/routes/callback.js`; later invocations pass `{ token }` alone). And
`upsertEngineUser` swallows every failure by design — `return null` on a non-2xx or a thrown fetch.

So if the engine is unreachable during those few hundred milliseconds — a deploy restarting the API
container is the realistic case — sign-in **succeeds**, a session cookie is issued, and
`token.engineUserId` is `undefined` **for the life of that session**. `engineAuthHeaders()` then
returns `{}`, every `/api/me/*` call is anonymous, and the engine answers 401. The reader is signed
in and every personalised surface is broken. No retry anywhere in the app can fix it, because nothing
ever tries to resolve the id again.

Measured blast radius: every route that calls `engineAuthHeaders()` — the dashboard, report,
history, recommendations, settings, saved, the Guide, and (as of the onboarding work) the app-shell
gate and `/signin/complete`.

### Why the dev provider is not affected

`CredentialsProvider.authorize()` calls `upsertEngineUser` and returns `null` when it fails, so a
dev sign-in **fails closed** — the session is never created. Only the Google path can produce a
session without an identity. This matters for §5: recovery only ever needs to handle `google`.

## 2. Proposed design

One new module, `web/lib/engine-identity.ts`, exporting a single memoized resolver:

```ts
export async function resolveEngineUserId(token: {
  engineUserId?: unknown; provider?: unknown; sub?: unknown; email?: unknown; name?: unknown;
}): Promise<number | null>;
```

Called from two places, both of which already exist:

| Call site | Why | What it gains |
|---|---|---|
| `callbacks.jwt` in `lib/auth.ts` | the only place that can write the id back into the token | a **durable** heal — the token is re-issued with the id and the session stops needing recovery |
| `engineAuthHeaders()` in `lib/engine-auth.ts` | the only place that turns a session into an attributed engine call | an **immediate** heal — the current server render is already correct, not just the next one |

Both are needed, and the reason is a NextAuth constraint worth stating precisely, because it is the
whole reason the design is shaped this way:

> `GET /api/auth/session` calls `callbacks.jwt`, re-encodes the token, and pushes a refreshed cookie
> (`next-auth/core/routes/session.js`). `getServerSession()` in a Server Component passes a **no-op**
> `res.setCookie` (`next-auth/next/index.js`), so the `jwt` callback still runs on every server-side
> read but any mutation is **discarded**.

So a token healed during a server render does not persist; it persists when the client's
`SessionProvider` next fetches `/api/auth/session` (on mount of any page, and on window focus). The
`jwt` call site makes the heal permanent; the `engineAuthHeaders` call site makes the current request
work in the meantime; the memo (§4) is what stops the second one from calling the engine per request.

### The identity key

Recovery must resolve the **same** engine user, never mint a second one. The engine's upsert is keyed
on `(provider, provider_account_id)` with a `UNIQUE` constraint (`uq_identity_provider_account`), and
email is stored as profile context only — explicitly *not* a join key
(`examples/store.py::upsert_user_by_identity`). So the recovery key must be the provider account id,
and nothing else.

For Google, `token.sub` **is** that value: the provider's `profile()` maps `id: profile.sub`
(`next-auth/providers/google.js`), and NextAuth sets `sub: user.id.toString()`
(`core/routes/callback.js`). Verified, not assumed — the whole design rests on it.

Even so, the proposal adds two claims to the token at sign-in rather than relying on that coincidence:

```ts
token.provider = account.provider;              // "google" | "dev"
token.providerAccountId = account.providerAccountId;
```

Additive and free (they ride in the existing cookie). Recovery then uses them when present, and falls
back to `token.sub` **only when `token.provider` is absent and the token predates this change**. That
fallback is safe for exactly the reason in §1.1: a `dev` token whose `sub` is an engine user id can
never lack `engineUserId`, so it can never enter the recovery path. Recovery bails on any token whose
provider is not `google`.

## 3. When recovery is attempted

| Condition | Behaviour |
|---|---|
| `typeof token.engineUserId === "number"` | **No attempt.** A single type check; zero added cost on every healthy request. This is the overwhelming majority. |
| id missing, provider resolves to `google`, key available | Attempt, subject to the memo and backoff in §4. |
| id missing, provider is `dev` or unknown, or no key | **No attempt.** Return `null`; behave exactly as today. |
| id missing and the email no longer passes the beta allowlist | **No attempt** (§5). |
| No session at all | Not reached — both call sites already require a token/session. |

Recovery is never triggered by a reader-visible action, has no UI, and needs no new route. It is a
repair on a path the request was taking anyway.

## 4. Caching behaviour

Three layers, in order of how long they hold:

1. **The token itself** — the real cache. Once `callbacks.jwt` writes `engineUserId` and the client
   refetches `/api/auth/session`, the id rides in the signed cookie for the session's lifetime and
   the guard in §3 short-circuits forever after. No expiry of our own; it dies with the session.
2. **A process-level memo** — `Map<string, number>` keyed by `${provider}:${providerAccountId}`,
   holding a resolved id for a bounded TTL (proposed: 10 minutes). This is what makes the
   `engineAuthHeaders` call site affordable: without it, every server render between the failed
   sign-in and the next client session fetch would issue its own upsert.
3. **In-flight coalescing** — `Map<string, Promise<number | null>>`, so N concurrent requests for the
   same identity share **one** engine call. A page render that fans out to several route handlers is
   the normal case, not the exception.

Negative results are cached too, as a **backoff stamp** rather than a value: after a failed attempt,
suppress further attempts for that identity for a short window (proposed: 30 s, no exponential
growth — the failure this recovers from is an outage measured in seconds). Without this, an engine
that is down turns every page view into a retry storm against a service that is already unwell.

State is per-process and lost on restart. That is correct: this is a cache, never a source of truth,
and the deployment runs one web container. Nothing needs to be shared or persisted.

**Explicitly rejected:** carrying the backoff stamp in the token. It would be discarded on exactly
the reads that need it (server-side, §2), so it would look like it worked while doing nothing.

## 5. Security implications

| Concern | Answer |
|---|---|
| **Can a client forge an identity?** | No. Every input (`sub`, `provider`, `providerAccountId`, `email`) comes from the JWT, signed with `NEXTAUTH_SECRET`. Anyone able to alter those already owns the session outright. Recovery reads no request body, query param, or header. |
| **Can recovery attach a session to the wrong account?** | No, provided the key is the provider account id. The engine joins on `(provider, provider_account_id)`; email is refreshed but never matched. This is the single most important invariant in the design and gets its own test (§8). |
| **Can it be used to hijack by email?** | No — see above. If a future change ever made the engine resolve identities by email, a Google session could claim a `dev` account with the same address. The test in §8 exists to make that change fail loudly. |
| **Does it bypass the beta allowlist?** | It must not. `callbacks.signIn` enforces the allowlist at sign-in only, so recovery is effectively the deferred second half of a sign-in that already passed. The proposal nonetheless **re-checks `isEmailAllowed(token.email)`** before attempting, so a reader removed from the allowlist cannot have an engine account created for them by a stale session. `loadAllowlist` is a small `readFileSync` on a path already read per sign-in; on the recovery path it runs at most once per backoff window. |
| **Trust boundary to the engine** | Unchanged. Recovery calls the existing `POST /api/internal/users` with `X-IH-Auth` when `RWE_INTERNAL_SECRET` is set, exactly as sign-in does. No new endpoint, no new surface, no widening of `/api/internal/*`. |
| **Does it leak anything into logs?** | One OBS1-style structured line per recovery — `{"event":"engine_identity_recovered","provider":"google","userId":N}`. Email is omitted; the existing `beta_access_denied` line logs an email because an operator must know who to approve, and no such need exists here. |
| **Can it create accounts in a loop?** | No. The upsert creates at most one identity row per `(provider, account id)`; repeated calls resolve. See §7. |

## 6. Performance impact

Baseline from the capacity work: t3.medium, 2 vCPU, **0.40 vCPU sustainable** on CPU credits,
currently measured at ~0.19 vCPU busy.

| Case | Cost |
|---|---|
| Healthy session (essentially all traffic) | One `typeof` check per session read. Unmeasurable. |
| Healed session, after the client refetches | Same as healthy — the guard short-circuits. |
| Broken session, engine up | One `POST /api/internal/users` — a single indexed lookup on `identities` — coalesced across concurrent requests and then memoized for the TTL. Bounded by **one call per identity per 10 minutes per process**. |
| Broken session, engine down | One attempt per identity per 30 s backoff window, coalesced. This is *lower* than today's implicit cost, where every per-user route already round-trips to the engine and gets a 401. |
| Cold start after an outage (worst case) | Every affected identity attempts once. With a closed beta the population is small; the coalescing map bounds concurrency per identity, and the backoff bounds repeats. |

No change to the request path of a healthy user, and no new background work. The `jwt` callback
already runs on every session read; this adds a branch to it.

## 7. Idempotency

- **The engine upsert is idempotent by construction** — `upsert_user_by_identity` selects the
  identity and returns its user; only a first sighting inserts. Repeated recovery yields the same id
  and creates nothing.
- **Recovery is therefore safe to attempt any number of times**, from any number of processes, tabs,
  or concurrent requests. The memo and coalescing are performance measures, not correctness measures —
  which is the property to preserve: correctness must not depend on the cache being hit.
- **One race is worth checking during implementation.** `upsert_user_by_identity` does
  `SELECT` → `INSERT` inside one session; two simultaneous first-sightings of the same identity could
  collide on `uq_identity_provider_account` and raise `IntegrityError`. Under SQLite's single writer
  this is a narrow window, and it exists today on the sign-in path, but recovery makes concurrent
  first-sightings more likely (a fan-out page render on a session that never got an id). The follow-up
  should either catch the unique violation and re-select, or confirm the window is unreachable. This
  is the one place the proposal touches engine code.
- **Healing the token is idempotent**: writing the same `engineUserId` again is a no-op, and a token
  that already has one never enters the path.

## 8. Required tests

**`web/lib/engine-identity.test.ts`** (new, `node --test`, fetch stubbed):

1. A token with a numeric `engineUserId` triggers **zero** engine calls — the guard, asserted by call
   count, because a regression here is a per-request engine call in production.
2. A Google token without an id resolves and returns the engine id.
3. The upsert body is keyed on `providerAccountId`, never on email — assert the exact payload.
4. A legacy token (no `provider` claim) falls back to `sub`; a token with `provider: "dev"` does not
   attempt at all.
5. Two concurrent calls for the same identity produce **one** fetch (coalescing).
6. A second call inside the TTL produces no fetch; after the TTL, one.
7. A failed attempt returns `null` and suppresses the next attempt inside the backoff window; after
   it, one attempt.
8. An engine 401/500/timeout each return `null` without throwing — callers must keep behaving as they
   do today.
9. An email that fails `isEmailAllowed` produces no attempt.

**`web/lib/auth.test.ts`** (new — there is no test for `lib/auth.ts` today):

10. `callbacks.jwt` writes `provider` and `providerAccountId` at sign-in.
11. `callbacks.jwt` on a later invocation (no `account`) with a missing id calls the resolver and
    writes the result to the token.
12. `callbacks.jwt` with an id present does not call the resolver.

**Engine (`tests/test_api_fastapi.py` / `tests/test_store.py`)**:

13. `upsert_user_by_identity` called twice with the same `(provider, account id)` returns one user and
    creates one identity row — extend the existing coverage to assert the row count.
14. The same email under two different providers yields **two** users. This is the anti-hijack test;
    it must fail if anyone ever makes the join key an email.
15. Concurrent first-sighting of one identity resolves to a single user (or the window is documented
    as unreachable, with the reasoning).

**e2e (`web/e2e/specs/auth.spec.ts`)**:

16. Sign in with the engine refusing `/api/internal/users`, then let it recover: the reader's next
    page load is personalised, with no sign-out. This is the scenario in prose form and the only test
    that proves the whole path.

## 9. Out of scope

- Re-checking the beta allowlist on *every* request (JWT sessions don't do this today; changing it is
  a separate policy decision).
- Any change to session lifetime, strategy, or cookie shape.
- Making `upsertEngineUser` fail sign-in when the engine is down. It is tempting — the dev provider
  already does exactly that — but it converts a recoverable degradation into an outage-shaped one
  ("you cannot sign in"), and it should be argued separately from recovery.
- Surfacing "your account is still connecting" in the UI. Recovery is meant to be invisible; if it
  isn't, that is the argument for the previous bullet, not for a banner.
