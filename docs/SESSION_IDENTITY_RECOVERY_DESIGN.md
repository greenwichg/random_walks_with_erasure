# Design Proposal — Recovering a Session With No Engine Identity

**Status:** proposal, not implemented. Written as a follow-up to the onboarding-gate work
([`ONBOARDING.md`](ONBOARDING.md) §8), which surfaced the failure but deliberately left it alone:
it predates that work and affects every authenticated surface, not just onboarding.

**Goal:** whenever a valid authenticated session exists but its token carries no engine user id,
resolve one automatically, without the reader having to sign out and back in.

**Sequencing:** [`IDENTITY_RECOVERY_IMPLEMENTATION_PLAN.md`](IDENTITY_RECOVERY_IMPLEMENTATION_PLAN.md)
breaks this and the engine-side upsert into seven reviewable commits, with the rollback strategy and
deployment risks for each.

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
session without an identity. Recovery therefore only ever needs to handle `google` — which is what
makes the legacy-token fallback in §2 and the deploy-skew case in §9a safe.

## 2. Proposed design

One module, `web/lib/engine-identity.ts` — which now exists and already owns `upsertEngineUser`
(commit 2 of the plan moved it out of `lib/auth.ts`). It gains a single memoized resolver:

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

Recovery must resolve the **same** engine user, never mint a second one. That is the store's guarantee,
specified in [`IDENTITY_UPSERT_CONCURRENCY.md`](IDENTITY_UPSERT_CONCURRENCY.md) — invariants **I1** (one user per `(provider, provider_account_id)`), **I2**
(idempotency) and **I5** (email is never an identity key). What this design owes that contract is the
**correct key**: the provider account id, and nothing else.

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
fallback is safe for exactly the reason in §1, *Why the dev provider is not affected*: a `dev` token
whose `sub` is an engine user id can never lack `engineUserId`, so it can never enter the recovery
path. Recovery bails on any token whose provider is not `google`.

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
and the deployment runs one web container. Nothing needs to be shared or persisted — §9 works through
what changes, and what does not, if that stops being true.

**Explicitly rejected:** carrying the backoff stamp in the token. It would be discarded on exactly
the reads that need it (server-side, §2), so it would look like it worked while doing nothing.

## 5. Security implications

| Concern | Answer |
|---|---|
| **Can a client forge an identity?** | No. Every input (`sub`, `provider`, `providerAccountId`, `email`) comes from the JWT, signed with `NEXTAUTH_SECRET`. Anyone able to alter those already owns the session outright. Recovery reads no request body, query param, or header. |
| **Can recovery attach a session to the wrong account?** | No, provided the key is the provider account id — guaranteed by **I1**/**I5** of the [`IDENTITY_UPSERT_CONCURRENCY.md`](IDENTITY_UPSERT_CONCURRENCY.md), which owns the constraint and its tests. |
| **Can it be used to hijack by email?** | No — **I5**. If a future change ever made the engine resolve identities by email, a Google session could claim a `dev` account with the same address; that contract's test 4 exists to make such a change fail loudly. |
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

- **The engine upsert is idempotent, and that is a contract rather than an assumption** — **I2** of the
  [`IDENTITY_UPSERT_CONCURRENCY.md`](IDENTITY_UPSERT_CONCURRENCY.md). Repeated recovery yields the same id and creates nothing.
- **Recovery is therefore safe to attempt any number of times**, from any number of processes, tabs,
  or concurrent requests. The memo and coalescing are performance measures, not correctness measures —
  which is the property to preserve: correctness must not depend on the cache being hit.
- **This design depends on §3 and §4 of that contract** — concurrent first-sighting resolving to one
  user, and the loser re-reading rather than erroring. The change specified there is a **prerequisite**
  for recovery, because recovery makes concurrent first-sightings of a single identity materially more
  likely (§9c). It is the one place this work touches engine code, and it is specified, diagrammed and
  tested there rather than here.
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

**Engine** — already written, in `tests/concurrency/`, and owned by
[`IDENTITY_UPSERT_CONCURRENCY.md`](IDENTITY_UPSERT_CONCURRENCY.md) §7 rather than duplicated here.
Recovery depends on the concurrent first-sighting properties (one user, no orphan, sequential
idempotency, same email under two providers → two users) and on the strict-xfail tripwire that will
announce when the upsert change lands. Run them with `pytest tests/concurrency -q`.

**e2e (`web/e2e/specs/auth.spec.ts`)**:

16. Sign in with the engine refusing `/api/internal/users`, then let it recover: the reader's next
    page load is personalised, with no sign-out. This is the scenario in prose form and the only test
    that proves the whole path.

## 9. Mixed-version deployments and rolling releases

### The topology this ships onto today

Verified, not assumed: `deploy/docker-compose.aws.yml` defines **one** `web` service with no
`replicas` and no `deploy:` block, Caddy reverse-proxies to the single service name `web:3000`, and
`deploy/ops/update.sh` replaces it with `dc up -d` — Compose stops the old container and starts the
new one ("THE POINT OF NO RETURN", in the script's own words). There is a brief window with **no** web
instance, and never a window with two versions serving at once.

So on the current topology, mixed-version skew is **temporal, not spatial**: a token minted by the old
image is read by the new image after the restart. Everything below also covers the spatial case,
because the design should not have to be revisited if that changes.

### a. Signed in on an old instance, later served by a new one

This is the common case on every deploy, and it is the case the token-claim design has to survive.

| Direction | Token shape | New/old code's behaviour |
|---|---|---|
| **Old → new** (deploy) | minted without `provider` / `providerAccountId`; `engineUserId` present or not | Recovery falls back to `token.sub`, which for Google **is** the provider account id (§2). A `dev` token cannot reach the path (§1). Correct with no migration. |
| **New → old** (rollback) | carries the two extra claims | The old `jwt` callback returns the token it was handed, so unknown claims are preserved rather than stripped. An already-healed session keeps working; an unhealed one simply isn't repaired until the new image is back. |

Two properties make this work, and both are worth naming because a future change could remove them
without obviously breaking anything:

- **The claims are additive.** Nothing reads them as required; absence has a defined fallback. A token
  is never invalidated by a version change, so no session is ever forced to sign in again by a deploy.
- **Sign-in rebuilds the token; a session read preserves it.** NextAuth constructs
  `defaultToken = { name, email, picture, sub }` on the sign-in invocation only
  (`core/routes/callback.js`); later invocations pass the decoded token through. So a sign-in on an old
  instance produces an old-shaped token — the row above — and a session read on an old instance does
  not discard what a new instance added.

Cookie size is not a factor: the two claims add on the order of 50 bytes against NextAuth's 4096-byte
chunking threshold, and both versions' `sessionStore` read chunked cookies transparently, so even a
re-chunk would be invisible.

### b. Divergent in-memory caches

The memo and the coalescing map are per-process, so on N instances there are N of them, and a deploy
throws away the one it had. Neither affects correctness, for one reason stated as a rule in §7:

> The memo and coalescing are performance measures, not correctness measures. Correctness must not
> depend on the cache being hit.

What actually diverges is **cost**, bounded and stated exactly:

| Topology | Worst-case engine calls for one broken identity |
|---|---|
| 1 instance (today) | 1 per 10-minute TTL, or 1 per 30-second backoff window while the engine is down |
| N instances | N per TTL / N per backoff window — a cold cache per instance, not a cache that can be wrong |
| Immediately after a deploy | 1 per instance, because the memo starts empty |

Every instance resolves through the same keyed upsert, so they all converge on the same engine user id
(§7). A divergent cache can make the system do redundant work; it cannot make two instances disagree
about who the reader is.

The post-deploy empty cache is in fact the behaviour you want. A deploy restarts the API, which is
precisely when identity-less sessions get created — and it also clears any backoff stamp, so the first
request after the restart attempts recovery immediately instead of being suppressed by a stamp set
during the outage.

**The durable cache is not in any instance.** It is the signed session cookie. That is what makes this
topology-independent: the heal is written by whichever instance handled the client's
`/api/auth/session` fetch, and from then on *every* instance and *every* version reads the id straight
off the token and never calls the resolver again. No server-side coordination, no shared cache, no
sticky sessions.

### c. Concurrent recovery across instances

Concurrency is safe by construction *provided* one engine-side fix lands — and this is the section's
one real finding, not a reassurance.

Safe already:

- The upsert is idempotent and keyed on `(provider, provider_account_id)`, so simultaneous recoveries
  of the same identity from different instances resolve to the same user and create at most one
  identity row.
- Recovery writes nothing else. It does not touch onboarding, reads, reports, or settings, so there is
  no second write to order.
- Losing a race costs nothing: both callers get the same id.

The exception is the store's un-fixed first-sighting window, specified in [`IDENTITY_UPSERT_CONCURRENCY.md`](IDENTITY_UPSERT_CONCURRENCY.md) §3–§4. On one
instance, per-process coalescing collapses concurrent first-sightings to a single call, so the window is
narrow. **Across instances there is no coalescing**, so N instances recovering the same identity at once
means up to N simultaneous first-sightings — and until that contract's §4 lands, the loser's
`IntegrityError` reaches the engine's catch-all handler as a typed `500 internal_error`, which recovery
reads as a failure and backs off from. Nothing is corrupted — measured against the shipped method, 15
concurrent first-sightings leave exactly one user and one identity, because the loser's whole
transaction rolls back. The cost is that the loser is *delayed* by a backoff window it did not need,
and on a single instance today the same thing happens to two concurrent sign-ins.

So the fix is a **prerequisite**, and the topological reason is that it is what makes recovery
*effective* on more than one web instance: without it, N instances recovering one identity means N−1 of
them take an unnecessary failure path.

### Assumptions about deployment topology, stated

| Assumption | Depended on for | If it changes |
|---|---|---|
| `NEXTAUTH_SECRET` identical across instances | reading each other's tokens at all | Already required today; a mismatch invalidates every session, recovery or not. Not a new constraint. |
| No session affinity required | nothing | Correct as designed: all durable state is the signed cookie plus the engine. Recovery adds no server-side session state, so a load balancer may route freely. |
| One instance | **cost only** — the memo's hit rate | N instances multiply worst-case recovery calls by N. Bounded, idempotent, convergent. |
| Concurrent first-sighting is rare | the store's un-fixed first-sighting window ([`IDENTITY_UPSERT_CONCURRENCY.md`](IDENTITY_UPSERT_CONCURRENCY.md) §3) | Becomes likely with N instances. Fix required first — see above. |
| SQLite, single writer, one host | the engine, not this design | Horizontal scaling of the web tier is bounded by the engine's store long before it is bounded by anything here. Worth remembering when reading the N-instance column: it is contingency, not a roadmap. |

The honest summary: the design is correct on the current topology and stays correct on a rolling or
multi-instance one, because its durable state is a signed cookie and its engine write is an idempotent
keyed upsert. The only thing that must change before the topology does is the store's first-sighting
race, and that now has its own specification, diagrams and tests in [`IDENTITY_UPSERT_CONCURRENCY.md`](IDENTITY_UPSERT_CONCURRENCY.md).

## 10. Out of scope

- Re-checking the beta allowlist on *every* request (JWT sessions don't do this today; changing it is
  a separate policy decision).
- Any change to session lifetime, strategy, or cookie shape.
- Making `upsertEngineUser` fail sign-in when the engine is down. It is tempting — the dev provider
  already does exactly that — but it converts a recoverable degradation into an outage-shaped one
  ("you cannot sign in"), and it should be argued separately from recovery.
- Surfacing "your account is still connecting" in the UI. Recovery is meant to be invisible; if it
  isn't, that is the argument for the previous bullet, not for a banner.
