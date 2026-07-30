# Design — Recovering a Session With No Engine Identity

**Status:** **implemented.** `callbacks.jwt` in `lib/auth-callbacks.ts` calls `resolveEngineUserId`, and
has done since commit 5 of [`IDENTITY_RECOVERY_IMPLEMENTATION_PLAN.md`](IDENTITY_RECOVERY_IMPLEMENTATION_PLAN.md);
the kill switch reaches the container (5a), the three log events exist (5b), and the same-request
visibility claim in §2a is asserted by a committed test rather than argued (5c). Known gaps and
deliberate omissions are listed in §10. Written as a follow-up to the onboarding-gate work
([`ONBOARDING.md`](ONBOARDING.md) §8), which surfaced the failure but deliberately left it alone:
it predates that work and affects every authenticated surface, not just onboarding.

**Goal:** whenever a valid authenticated session exists but its token carries no engine user id,
resolve one automatically, without the reader having to sign out and back in.

**Sequencing:** [`IDENTITY_RECOVERY_IMPLEMENTATION_PLAN.md`](IDENTITY_RECOVERY_IMPLEMENTATION_PLAN.md)
breaks this and the engine-side upsert into reviewable commits, with the rollback strategy and
deployment risks for each.

**Provenance markers.** Claims about how NextAuth behaves are load-bearing here and were originally
written from reading the source. Where a claim has since been checked by *running* the code, it carries
`[T]`; where it is still architectural reasoning, `[R]`. Two `[R]` claims were disproved when they were
finally traced (§2, §3), which is why the distinction is marked rather than assumed.

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

## 2. The design

One module, `web/lib/engine-identity.ts` — which now exists and already owns `upsertEngineUser`
(commit 2 of the plan moved it out of `lib/auth.ts`). It gains a single memoized resolver:

```ts
export async function resolveEngineUserId(token: {
  engineUserId?: unknown; provider?: unknown; sub?: unknown; email?: unknown; name?: unknown;
}): Promise<number | null>;
```

**Called from exactly one place: `callbacks.jwt` in `lib/auth-callbacks.ts`.** `[T]`

An earlier revision of this section called for a second call site in `engineAuthHeaders()`, on the
reasoning that the `jwt` callback can only make a heal *durable* and something else was needed to make
the *current* server render correct. Tracing the code disproved the second half — see §2a. One call
site delivers both.

### 2a. Why one call site is enough — traced, not reasoned `[T]`

The claim to establish is that a mutation made inside `callbacks.jwt` is visible to
`engineAuthHeaders()` **in the same request**, with no cookie written. The path, with line numbers from
the installed `next-auth`:

| # | Function entered | File:line |
|---|---|---|
| 1 | `engineAuthHeaders()` | `lib/engine-auth.ts:22` |
| 2 | `getServerSession(authOptions)` | `next-auth/next/index.js:100` |
| 3 | RSC branch builds `req` from `next/headers`, and `res = { getHeader(){}, setCookie(){}, setHeader(){} }` | `next/index.js:102–122` |
| 4 | `AuthHandler({ options, req: { action: "session" … } })` | `next/index.js:129` |
| 5 | `switch (action) → case "session"` → `routes.session(...)` | `core/index.js:134–135` |
| 6 | session route | `core/routes/session.js:8` |
| 6a | `if (!sessionToken) return response` | `session.js:~44` — exit A |
| 6b | `jwt.decode(...)`, throws → `catch` → `sessionStore.clean()` | `session.js:~49, ~88` — exit B |
| 6c | **`callbacks.jwt({ token: decodedToken })`** | `session.js:53` |
| 6d | **`callbacks.session({ session: {user:…}, token })`** — `token` is 6c's **return value** | `session.js:~66` |
| 6e | `response.body = updatedSession` | `session.js:~71` |
| 6f | `jwt.encode` → `sessionStore.chunk` → `response.cookies.push(...)` | `session.js:73–81` |
| 7 | `if (session.cookies) cookies.push(...)` | `core/index.js:138` |
| 8 | `cookies.forEach(c => setCookie(res, c))` — the **no-op** from step 3 | `next/index.js:143` |
| 9 | `return body` | `next/index.js:148` |
| 10 | reads `session.engineUserId` → `X-IH-User-Id` | `lib/engine-auth.ts:25` |

Step **6d passes `token`, not `decodedToken`**. The session object is constructed *downstream* of the
callback, and 6e assigns it to the body that step 9 returns. That is the whole mechanism: the mutation
is visible because the session is built from the mutated token, in-process, before anything is
serialised.

Run against that exact code with our real callbacks and recovery simulated `[T]`:

```
token BEFORE callbacks.jwt : {…,"provider":"google","providerAccountId":"1084…"}   ← no engineUserId
token AFTER  callbacks.jwt : {…,"engineUserId":42}
session body returned      : {"user":{…},"expires":…,"engineUserId":42}
cookies queued by the route: 3   ← discarded by the RSC no-op setCookie
=> engineAuthHeaders reads session.engineUserId = 42
```

**Visibility and persistence are decoupled.** Visibility is same-request and unconditional. Persistence
needs a response that can set cookies — which is why a token healed during a server render does not
stick, and persists instead when `SessionProvider` next fetches `/api/auth/session` (on mount of any
page, and on window focus, where `refetchOnWindowFocus` defaults to `true`). `[T]`

### 2b. Every path that reaches `engineAuthHeaders` without `callbacks.jwt` `[T]`

`engineAuthHeaders` has one statement before it reads the session, so this reduces to: can
`getServerSession` return without running `callbacks.jwt`? There are three exits, and only two are
reachable:

| Exit | Condition | Runs `callbacks.jwt`? | Could recovery help? |
|---|---|---|---|
| A | no session cookie | no — measured `body={}` | **No token exists.** Nothing to recover from. |
| B | cookie undecodable (expired, tampered, wrong secret) | no — measured `body={}`, 3 cookies cleared | Same: no token. |
| C | `sessionStrategy !== "jwt"` | n/a | Unreachable: `lib/auth.ts` sets `session: { strategy: "jwt" }` and there is no adapter. |

Both reachable skips occur precisely where there is no decodable token, so a second call site would be
equally powerless. Nothing in `app/`, `lib/`, `components/` or `middleware.ts` imports `getToken` or
`next-auth/jwt` directly, so there is no raw-token bypass. `[T]` The only other producer of
`X-IH-User-Id` is `engineHeadersForUserId`, on the browser-extension path, where the id comes from a
per-user API token and no JWT is involved.

### The identity key

Recovery must resolve the **same** engine user, never mint a second one. That is the store's guarantee,
specified in [`IDENTITY_UPSERT_CONCURRENCY.md`](IDENTITY_UPSERT_CONCURRENCY.md) — invariants **I1** (one user per `(provider, provider_account_id)`), **I2**
(idempotency) and **I5** (email is never an identity key). What this design owes that contract is the
**correct key**: the provider account id, and nothing else.

For Google, `token.sub` **is** that value: the provider's `profile()` maps `id: profile.sub`
(`next-auth/providers/google.js`), and NextAuth sets `sub: user.id.toString()`
(`core/routes/callback.js`). Verified, not assumed — the whole design rests on it.

Even so, two claims are written onto the token at sign-in rather than relying on that coincidence:

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
| **`account` is present** (this invocation *is* the sign-in) | **No attempt** — see the invariant below. |
| id missing, provider resolves to `google`, key available | Attempt, subject to the memo and backoff in §4. |
| id missing, provider is `dev` or unknown, or no key | **No attempt.** Return `null`; behave exactly as today. |
| id missing and the email no longer passes the beta allowlist | **No attempt**, and the denial is remembered (§4, §5). |
| No session at all | Not reached — the call site already requires a decodable token (§2b). |

Recovery is never triggered by a reader-visible action, has no UI, and needs no new route. It is a
repair on a path the request was taking anyway.

### INVARIANT: recovery never runs on the sign-in invocation `[T]`

**`callbacks.jwt` must attempt recovery only when `account` is absent.**

`account` is present on exactly one invocation per session — the sign-in — and that is the invocation
that has *just* run `upsertEngineUser` itself. If that call failed, `token.engineUserId` is unset, and
an unguarded recovery branch would fire immediately and make a **second upsert attempt milliseconds
after the first one failed**: two calls into an engine that is already failing, at the moment it is
failing, with no memo entry yet to suppress the second.

That is not a hypothetical reading of the code — it is what the state-machine walkthrough found when
flow 3 (*sign-in, engine down*) was traced, and it is the reason this invariant is stated separately
rather than left implicit in the table above. The guard is one condition:

```ts
if (!account && token.engineUserId == null) { /* recover */ }
```

The cost of the guard is nil: on the sign-in invocation there is nothing to recover *to*. Sign-in
already had its attempt, and its failure is exactly the state the next request will repair.

## 4. Caching behaviour

Three layers, in order of how long they hold:

1. **The token itself** — the real cache. Once `callbacks.jwt` writes `engineUserId` and the client
   refetches `/api/auth/session`, the id rides in the signed cookie for the session's lifetime and
   the guard in §3 short-circuits forever after. No expiry of our own; it dies with the session.
2. **A process-level memo** — `Map<string, CacheEntry>` keyed by `${provider}:${providerAccountId}`,
   holding a resolved id for a bounded TTL (10 minutes), swept on write and capped at 1 000 identities
   so a TTL alone cannot leave it growing without limit. This is what keeps the cost bounded while a
   session is un-healed: `callbacks.jwt` runs on **every** `getServerSession`, so without it every
   server render between the failed sign-in and the next client session fetch would issue its own
   upsert. `[T]`
3. **In-flight coalescing** — `Map<string, Promise<number | null>>`, so N concurrent requests for the
   same identity share **one** engine call. A page render that fans out to several route handlers is
   the normal case, not the exception.

Negative results are cached too, as a **backoff stamp** rather than a value: after a failed attempt,
suppress further attempts for that identity for a short window (30 s, no exponential growth — the
failure this recovers from is an outage measured in seconds). Without this, an engine that is down
turns every page view into a retry storm against a service that is already unwell.

### Allowlist denials are memoized, and where the check sits matters

A denial is a negative result too, and it must be recorded as one.

The check itself is defence in depth: recovery is the deferred second half of a sign-in, so it re-runs
the gate that sign-in ran, and a reader removed from the allowlist since must not have an engine account
created for them by a stale session (§5). But `isEmailAllowed` calls `loadAllowlist`, which does an
**uncached `readFileSync`**. Placed before the memo and left unrecorded, that produces a session which
does synchronous file I/O *on every session read* — i.e. on every server render — always returning
`null`, for the entire 30-day life of the token. No engine calls, but unbounded repeated work on the
auth path.

So: **a denial writes a negative memo entry**, exactly like an engine failure, and the backoff window
suppresses the re-check. The check's stated purpose survives intact — a cached denial still prevents
account creation, which is the thing it exists to prevent — and the cost falls from once per request to
once per backoff window. An operator who adds someone to the allowlist sees it take effect within that
window rather than instantly, which is the one trade this makes and is well within what
`BETA_ACCESS_ENABLED` already implies.

This was found by walking the state machine rather than by writing the resolver, which is why it is
recorded here as design rather than as a code comment. `[R]` — the repeated `readFileSync` follows from
reading `loadAllowlist`; it has not been profiled.

### The allowlist check is skipped on the cached and coalesced paths — deliberately

Worth stating outright, because it is security-relevant and a reader who derives it independently may
"fix" it. Two paths return **before** `isEmailAllowed` runs:

- **a live positive memo entry** — the id was resolved for this identity inside the TTL;
- **a joined in-flight promise** — another caller is already resolving this exact identity.

Both are safe for the same reason, and it is the reason the cache is keyed the way it is: the key is
`(provider, providerAccountId)`, and a Google account id is **stable across email changes**. So a caller
that hits either path is the same *person* whose email was checked when the entry was created — not a
different reader inheriting someone else's decision. The one observable consequence is the trade §4
already names: a revocation takes effect at the next cache expiry rather than instantly.

What this must never become is a cache keyed on anything weaker. If a future change ever keys the memo
on email, on `sub` without the provider, or on a value a caller supplies, these two early returns stop
being safe and the check must move above them. That is the invariant to hold, not the position of the
check.

State is per-process and lost on restart. That is correct: this is a cache, never a source of truth,
and the deployment runs one web container. Nothing needs to be shared or persisted — §9 works through
what changes, and what does not, if that stops being true.

**Explicitly rejected:** carrying the backoff stamp in the token. It would be discarded on exactly
the reads that need it — a server render's cookie write is a no-op (§2a step 8, `[T]`) — so it would
look like it worked while doing nothing.

## 5. Security implications

| Concern | Answer |
|---|---|
| **Can a client forge an identity?** | No. Every input (`sub`, `provider`, `providerAccountId`, `email`) comes from the JWT, signed with `NEXTAUTH_SECRET`. Anyone able to alter those already owns the session outright. Recovery reads no request body, query param, or header. |
| **Can recovery attach a session to the wrong account?** | No, provided the key is the provider account id — guaranteed by **I1**/**I5** of the [`IDENTITY_UPSERT_CONCURRENCY.md`](IDENTITY_UPSERT_CONCURRENCY.md), which owns the constraint and its tests. |
| **Can it be used to hijack by email?** | No — **I5**. If a future change ever made the engine resolve identities by email, a Google session could claim a `dev` account with the same address; that contract's test 4 exists to make such a change fail loudly. |
| **Does it bypass the beta allowlist?** | It must not. `callbacks.signIn` enforces the allowlist at sign-in only, so recovery is effectively the deferred second half of a sign-in that already passed. Recovery nonetheless **re-checks `isEmailAllowed(token.email)`** before attempting, so a reader removed from the allowlist cannot have an engine account created for them by a stale session. `loadAllowlist` is a small `readFileSync` on a path already read per sign-in; the denial is memoized so it runs at most once per backoff window rather than once per server render (§4). |
| **Trust boundary to the engine** | Unchanged. Recovery calls the existing `POST /api/internal/users` with `X-IH-Auth` when `RWE_INTERNAL_SECRET` is set, exactly as sign-in does. No new endpoint, no new surface, no widening of `/api/internal/*`. |
| **Does it leak anything into logs?** | Three OBS1-style structured lines, one per *attempt* — never per request (§5a). The success and failure lines omit the email; the **denial** line carries it, for the same reason `beta_access_denied` does: that reader is signed in and permanently un-attributed until an operator acts, so "who" is the actionable part. No token, secret, or account id is ever logged. |
| **Can the memo serve one reader another reader's decision?** | No. The two paths that skip the allowlist re-check are keyed on `(provider, providerAccountId)`, which is stable across email changes, so a caller reaching them is the same person whose email was checked. Stated as an invariant in §4 because it constrains what the cache key may become. |
| **Can it create accounts in a loop?** | No. The upsert creates at most one identity row per `(provider, account id)`; repeated calls resolve. See §7. |

## 5a. Operating it — logs, and the switch

### The three events

All on stderr from the `web` container, one JSON object per line, one line per **attempt**.

```json
{"event":"engine_identity_recovered","provider":"google","userId":4211}
{"event":"engine_identity_recovery_failed","provider":"google","reason":"http_401"}
{"event":"engine_identity_recovery_failed","provider":"google","reason":"unreachable","detail":"ECONNREFUSED"}
{"event":"engine_identity_recovery_denied","provider":"google","email":"reader@example.com","reason":"not_allowlisted"}
```

```bash
docker logs deploy-web-1 2>&1 | grep engine_identity | tail -20
```

| Event | Means | Do what |
|---|---|---|
| `engine_identity_recovered` | A broken session was repaired. **This is the feature working.** | Nothing. A *rising* rate means sign-in-time engine unavailability — look at deploy timing and engine restarts, not at recovery. |
| `engine_identity_recovery_failed`, `reason: http_401` | The engine rejected the call. In production this is `RWE_INTERNAL_SECRET` mismatched between the `web` and `api` services. | Compare the two; they must be byte-identical. Until fixed, **no** session can be repaired. |
| `…_failed`, `reason: timeout` | The engine accepted the connection and did not answer within recovery's deadline — **2 s**, not the 6 s a sign-in gets (§5b). Wedged, not down. | Check engine health and load. Recovery retries after 30 s per identity. |
| `…_failed`, `reason: unreachable`, `detail: ECONNREFUSED` | The engine is down or restarting. | Expected briefly during a deploy; self-corrects. Sustained means the `api` service is not up. |
| `…_failed`, `detail: ENOTFOUND` | `RWE_BACKEND_URL` is wrong. | Configuration, not an outage. |
| `…_failed`, `reason: malformed_response` | A 2xx without a numeric `userId` — the internal contract broke. | Engine bug or a proxy rewriting the body. Escalate; this should be impossible. |
| `…_failed`, `reason: unexpected` | Something threw where nothing can throw — `attemptEngineUpsert` catches its own errors, so this is the belt-and-braces branch (§10, N5). | Should never appear. If it does, it is a bug in the web tier, not an engine problem. Escalate with the surrounding log lines. |
| `engine_identity_recovery_denied`, `reason: not_allowlisted` | A signed-in reader is no longer on the beta allowlist. Their session stays un-attributed. | Decide: add them back (`BETA_ALLOWLIST`, takes effect within one 30 s window) or leave them out. |
| `…_denied`, `reason: empty_allowlist` | The gate is on with nothing configured, so **everyone** is denied (fail-closed). | Operational emergency. Fix `BETA_ALLOWLIST` / `BETA_ALLOWLIST_FILE`. |
| `…_denied`, `reason: no_email` | The token carries no email to check. | Rare; a Google account without an email scope. |

**Silence is meaningful too.** No `engine_identity_*` lines at all means no session needed repairing —
the healthy steady state. It does *not* mean recovery is broken; a broken recovery is loud, which is the
whole reason the failure line exists.

**What is not instrumented.** There is no counter, gauge, or `/api/metrics` series for recovery — the
log line is the only signal, and answering "how many sessions are currently broken?" requires counting
lines. Accepted for now (§10); revisit if the recovered rate stops being near-zero.

### 5b. Two deadlines, chosen by caller

`RWE_BACKEND_TIMEOUT_MS` (default 6000) bounds every engine call. Recovery does **not** use it directly:

| Caller | Deadline | Why |
|---|---|---|
| Sign-in — `callbacks.jwt` with `account`, and the dev provider's `authorize()` | `engineTimeoutMs()`, 6 s | The reader is waiting for *this* call. A sign-in that takes six seconds is bad; a sign-in that fails because the engine needed five is worse. |
| Recovery — `resolveEngineUserId` | `recoveryTimeoutMs()` = `min(engineTimeoutMs(), 2000)` | Awaited inside `getServerSession`, so the deadline is a reader waiting on a render for work they never asked for. Giving up early costs only the repair, and the next request retries after the 30 s backoff. |

**Clamped, never flat.** Lowering `RWE_BACKEND_TIMEOUT_MS` below 2 s lowers recovery with it — otherwise
an operator tightening the engine deadline would leave the *repair* as the slowest thing in the request,
the opposite of what they asked for. Raising it above 2 s does not lengthen recovery.

**No environment variable of its own**, deliberately. It is a ratio to a knob that already exists rather
than an independent policy, and every new variable must be threaded onto the `web` service in both
compose files and guarded in `deployment-rules.json` or it silently does nothing — which is how the kill
switch shipped inert the first time (§5a). If tuning it ever becomes necessary, that plumbing is the
cost, and it should be paid deliberately.

The practical effect: a wedged engine costs an affected reader ~2 s on one render per 30 s, instead of
~6 s. A *dead* engine — the common case during a deploy — still fails in microseconds via
`ECONNREFUSED` and is unaffected by either number.

### 5c. Recovery resolves the id without refreshing the profile

Recovery's `email` and `displayName` come from the session token, so they are as old as the session —
up to 30 days. Sign-in's come from a freshly minted OAuth response. The engine cannot tell the two
apart, so the caller says which it is:

| Caller | `refreshProfile` | Effect |
|---|---|---|
| Sign-in (`callbacks.jwt` with `account`; the dev provider's `authorize()`) | **omitted** | The engine refreshes email and display name, exactly as before. The request is byte-identical to the pre-S2 one — `undefined` is dropped by `JSON.stringify`. |
| Recovery (`resolveEngineUserId`) | `false` | The id resolves; an **existing** user's stored profile is left alone. |

**Creation is not a refresh.** A first sighting can arrive through recovery — the reader signed in
during an outage, so no engine row was ever made — and it is still created with the profile it was
given. Suppressing that write would mint accounts with a null email that nothing would ever fill.

The failure this prevents: a reader broken on device A, who signs in on device B, changes their Google
display name, and returns to A weeks later. Recovery on A would have written the old name back over
the new one, silently, with nothing in the logs and no way to attribute it.

**Rolling-deployment safety, in both directions.** The engine defaults `refreshProfile` to `true`, so an
old web tier that never sends it behaves as before; and `UpsertUserRequest` keeps Pydantic's default
`extra="ignore"`, so a *new* web sending it to an engine that predates it is silently ignored rather
than 422'd. Reverting either tier alone therefore returns to current behaviour rather than breaking —
`test_internal_user_upsert_ignores_unknown_fields` pins the harder half of that.

Storage-level detail, including what this does to the concurrent-first-sighting retry (it becomes a
pure `SELECT` on this path, and stays write-capable on the default one):
[`IDENTITY_UPSERT_CONCURRENCY.md`](IDENTITY_UPSERT_CONCURRENCY.md).

### Turning it off

```bash
# in deploy/.env
RWE_IDENTITY_RECOVERY=0
bash deploy/ops/restart.sh web        # `dc up -d web` — re-reads the environment, no rebuild
```

Read at call time, so the restart is what applies it. `0`, `false`, `no`, `off` disable; anything else,
including empty, leaves it on.

Disabling restores pre-recovery behaviour **exactly**: a session that already carries an engine id keeps
using it, so nobody is signed out and no working session is de-attributed. Only the repair stops. Both
this variable and `RWE_BACKEND_TIMEOUT_MS` are wired onto the `web` service in *both* compose files —
`deploy/ops/validate-deployment.py` fails if either goes missing, because for the first release they
were absent and the documented rollback silently did nothing.

Reverting commit 5 is the fallback if the flag is somehow not enough; it needs a rebuild and redeploy.

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
| Denied by the allowlist | One `readFileSync` per backoff window, not per render — the denial is memoized (§4). |

No change to the request path of a healthy user, and no new background work. The `jwt` callback
already runs on every session read, and — with the single call site established in §2a — this adds
**one branch to one callback**, not a branch in two places that must agree.

Measured against the shipped resolver with the engine stubbed `[T]` (`node --expose-gc`, 100 000
healthy resolutions; the figures the table above asserts):

| Measurement | Result |
|---|---|
| Healthy session (`engineUserId` present) | 100 000 resolutions, **0** engine calls, **0** cache entries, 0.52 µs each |
| 2 / 25 / 250 / 2 000 concurrent callers, one identity | **1** engine call in every case — coalescing is per identity and does not degrade with fan-out |
| 50 identities × 40 concurrent callers | 50 calls, 40:1 — coalescing is keyed, not global; no caller received another identity's id |
| 10 identities × 100 requests inside the TTL | 10 calls, 99.0% hit |
| 1 000 identities × 10 requests | 1 000 calls, 90.0% hit, 1 000 entries — the ceiling, holding |
| 25 000 distinct identities seen | **1 000** entries (cap held), 196.8 KiB, 202 bytes/entry |
| 10 000 requests against a dead engine, one identity | **1** call — 9 999 suppressed by the backoff stamp |

The last two rows are the ones that matter operationally: memory is bounded by the cap rather than by
traffic, and a dead engine cannot be turned into a retry storm by page views.

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

> The per-file counts below are a snapshot and have gone stale twice — they drift on any commit that
> adds a test. Re-derive rather than trust:
> `cd web && node --test --experimental-strip-types lib/<name>.test.ts | grep '^# pass'`.
> The **named properties** are the contract; the numbers are only a reading aid.

**`web/lib/engine-identity.test.ts`** — written, 53 tests, `node --test` with `fetch` stubbed:

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
9. An email that fails `isEmailAllowed` produces no attempt, **and the denial is memoized** — a second
   denied call inside the window does no further `isEmailAllowed` work (§4).

**`web/lib/auth-callbacks.test.ts`** — written, 21 tests. Named for the module the callbacks were moved
into by commit 4 of the plan, not `auth.test.ts`: `lib/auth.ts` constructs providers at module load and
`next-auth/providers/*` is CommonJS, which bare `node --test` cannot import. The callbacks are the unit
under test, so they live where they can be tested.

10. `callbacks.jwt` writes `provider` and `providerAccountId` at sign-in. ✅
11. `callbacks.jwt` on a later invocation (**no `account`**) with a missing id calls the resolver and
    writes the result to the token. — commit 5
12. `callbacks.jwt` with an id present does not call the resolver. ✅
13. **`callbacks.jwt` on the sign-in invocation does not call the resolver even when the id is
    missing** — the §3 invariant, asserted by resolver call count so that removing the `!account`
    guard fails the suite rather than silently doubling the engine calls of a failing sign-in.
    — commit 5

**`web/lib/engine-timeout.test.ts`** — written, 8 tests. Recovery coalesces callers onto one in-flight
promise, so a promise that never settles is a shared stall; the deadline both aborts *and* races, and a
test drives a transport that ignores the abort signal to prove the race is what makes it unconditional.

**`web/lib/session-recovery.test.ts`** — written, 11 tests, and the only committed test that exercises
the hop *between* the resolver and the callback. It drives the real `AuthHandler` from
`next-auth/core` — a module outside the package's `exports` map, loaded on purpose — so §2a's claim
is asserted rather than argued: a heal made in `callbacks.jwt` is in the session body of the same
request, with the queued cookies discarded. It also covers §2b's two reachable exits, route-level
coalescing across identities, fail-soft on a dead engine, the `!account` guard end to end, and the kill
switch. Treat a failure after a NextAuth upgrade as **revalidate §2a**, not as a test to loosen —
it is an assumption detector in the sense of [`CONCURRENCY_TESTING.md`](CONCURRENCY_TESTING.md).

**Engine** — already written, in `tests/concurrency/`, and owned by
[`IDENTITY_UPSERT_CONCURRENCY.md`](IDENTITY_UPSERT_CONCURRENCY.md) §7 rather than duplicated here.
Recovery depends on the concurrent first-sighting properties (one user, no orphan, sequential
idempotency, same email under two providers → two users) and on the strict-xfail tripwire that will
announce when the upsert change lands. Run them with `pytest tests/concurrency -q`.

**e2e (`web/e2e/specs/auth.spec.ts`)**:

14. Sign in with the engine refusing `/api/internal/users`, then let it recover: the reader's next
    page load is personalised, with no sign-out. This is the scenario in prose form and the only test
    that proves the whole path — in particular the one thing the unit tests cannot show, that the heal
    is *durable* because `SessionProvider` refetches `/api/auth/session` on mount (§2a).

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
`/api/auth/session` fetch — the route whose response can actually set cookies, unlike a server render,
where the queued cookies are handed to a no-op (§2a step 8, `[T]`) — and from then on *every* instance
and *every* version reads the id straight off the token and never calls the resolver again. No
server-side coordination, no shared cache, no sticky sessions.

The same trace also settles a question this section would otherwise have to leave open: because the
heal is visible in-process the moment `callbacks.jwt` returns, a reader served by an instance whose
cookie write is discarded is **not** left broken for that render. That instance's render is already
correct; only the persistence waits for the client's next session fetch. Divergent caches therefore
cost redundant engine calls and nothing else — there is no window in which one instance shows a
personalised page and another shows a 401 for the same request. `[T]`

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


## 10. Known gaps and accepted debt

Everything below was found by a production-readiness review *after* the implementation landed, and each
was classified deliberately rather than left implicit. "Accepted" means someone decided, not that nobody
looked.

### Closed since the review

| # | Gap | How it was closed |
|---|---|---|
| **S1** | Recovery inherited the general 6 s engine deadline, so a **wedged** engine could hold a server render for that long. | `recoveryTimeoutMs()` in `lib/engine-timeout.ts` — `min(engineTimeoutMs(), 2000)`, passed per call. Sign-in keeps the 6 s default; the repair gives up at 2 s and retries after the 30 s backoff. See §5b. |
| **S2** | Recovery sent `email`/`displayName` from a token up to 30 days old, and the engine refreshed both — so a long-idle broken session could write a **stale profile over a newer one**. | `refreshProfile` on `POST /api/internal/users`, defaulting to `true`. Recovery sends `false`; both sign-in paths omit it and their request is byte-identical to before. Creation still writes the profile — creation is not a refresh. See §5c. |
| **S4b** | No end-to-end test drove a broken session through a **real** browser, so the repair's *durable* half — the re-issued cookie — was reasoned about, never observed. | `web/e2e/specs/identity-recovery.spec.ts`, three tests against the real stack: a broken session heals to the **same** engine account and the re-issued cookie carries the id; a legacy token with only `sub` heals through the fallback; a non-Google broken token is refused. Fixtures `mintBrokenSessionCookie` / `decodeSessionToken` in `e2e/helpers.ts`. Verified by mutation, not just by passing — see the residual below. |

### Deferred to a follow-up PR

*(Empty. S4b was the last entry and is now closed — see above, and the residual below.)*

#### The residual S4b did not close

The original entry asked for a test driving **a real engine refusing `/api/internal/users`**. What
shipped covers everything *around* that refusal — the durable heal, the legacy-token fallback, and the
non-Google refusal — but not a genuine engine-side failure, and the reason is structural rather than
an omission: the suite shares one web server and one engine across all specs, `ENGINE_BASE` and the
kill switch are both read from that process's environment, and the recovery call is server-to-server,
so `page.route` cannot intercept it. Failing the endpoint for one test would mean failing it for all
of them.

That branch is covered where it can be: `lib/session-recovery.test.ts` and `lib/engine-identity.test.ts`
drive `attemptEngineUpsert` against a stubbed fetch for each outcome (`http_401`, `timeout`,
`unreachable`, `malformed_response`), which is where the reason-classification logic actually lives.
What e2e adds and units cannot — the re-issued cookie — is now observed.

**The tests were verified by mutation, not by passing.** Running the suite with
`RWE_IDENTITY_RECOVERY=0` fails exactly the two heal tests and leaves the refusal test green; deleting
the `provider !== "google"` guard fails exactly the refusal test and leaves the two heal tests green.
A test that asserts an absence is the easiest kind to write vacuously, which is why that second
mutation matters more than the first.

### Accepted, with the reasoning

| # | Debt | Why it stays |
|---|---|---|
| **N2** | The memo key is a string join, `` `${provider}:${providerAccountId}` ``. | Unreachable collision: `provider` is a fixed set and a Google `sub` is numeric. `JSON.stringify([provider, id])` is better and costs nothing — **but it becomes required the moment a provider is added**, and it should land in that change rather than as churn now. |
| **N4** | Eviction is FIFO, and `Map.set` on an existing key keeps its original position — so a frequently refreshed identity can be evicted before colder, newer ones. | Costs one engine call when it happens, never correctness. True LRU means touching the map on every read: more work in the common case to optimise the rare one. |
| **N5** | The resolver's `.catch` is unreachable — `attemptEngineUpsert` swallows its own errors. | Removing it would make this module correct *by assumption about another module*. It is placed before the result handler precisely so it cannot produce a duplicate log line or a second memo write. Untestable by construction, and that is fine. |
| **R4** | Multi-process safety is modelled, not proven. | The deployment runs one web container (§9). §9b states exactly what N instances would cost: redundant engine calls, bounded and convergent — never disagreement about who a reader is. |
| — | No metrics series for recovery; the log line is the only signal. | Counting log lines answers "is it working". A gauge for "how many sessions are currently broken" would need state this design deliberately does not keep. Revisit if the recovered rate stops being near-zero. |

### Still out of scope, by decision

- Re-checking the beta allowlist on *every* request. JWT sessions don't, and §4's cached-path invariant
  is what makes the current behaviour defensible; changing it is a policy decision, not a bug fix.
- Any change to session lifetime, strategy, or cookie shape.
- Making `upsertEngineUser` fail sign-in when the engine is down. Tempting — the dev provider does
  exactly that — but it converts a recoverable degradation into an outage-shaped one ("you cannot sign
  in"), and deserves its own argument.
- Surfacing "your account is still connecting" in the UI. Recovery is meant to be invisible; if it
  isn't, that is the argument for the previous bullet, not for a banner.
