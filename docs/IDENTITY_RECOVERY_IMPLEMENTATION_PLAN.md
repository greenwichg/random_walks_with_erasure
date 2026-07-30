# Implementation Plan — Identity Recovery + Transaction-Retry Upsert

**All commits are implemented. Commit 6 was deleted** — proved redundant by execution trace before it
was written (see below); commits 5a–5c were added by a production-readiness review after commit 5
landed. This is the roadmap for two designs that are now shipped:

- [`IDENTITY_UPSERT_CONCURRENCY.md`](IDENTITY_UPSERT_CONCURRENCY.md) §4 — the engine-side upsert.
- [`SESSION_IDENTITY_RECOVERY_DESIGN.md`](SESSION_IDENTITY_RECOVERY_DESIGN.md) — the web-side recovery.

Acceptance is not a matter of judgement: `tests/concurrency/` encodes it. For commit 1 the oracle was an
executable reference implementation living in `test_identity_upsert.py`; it was deleted by the commit it
certified, exactly as planned.

---

## The commits

| # | Tier | Commit | Observable in production? | Revert |
|---|---|---|---|---|
| **1** | engine | Transaction-retry upsert (§4) + collapse the harness onto the real method — **done** | yes — losers resolve instead of erroring | `git revert`, behaviour-only |
| **2** | web | Extract `upsertEngineUser` into `lib/engine-identity.ts` — **done** | **no** — pure move | `git revert` |
| **3** | web | Add the memoized resolver + its unit tests, wired to nothing — **done** | **no** — dead code | `git revert` |
| **3.5** | web | Give `upsertEngineUser` a deadline — `lib/engine-timeout.ts`, shared with `backend.ts` — **done** | yes — a wedged engine now fails instead of hanging | `git revert` |
| **4** | web | Persist `provider` + `providerAccountId` claims at sign-in — **done** | **no** — nothing reads them yet | `git revert` |
| **5** | web | Call the resolver from `callbacks.jwt` — the **only** call site — behind a kill switch — **done** | yes — broken sessions start healing, current render included | env flag, then revert |
| **5a** | deploy | Plumb `RWE_IDENTITY_RECOVERY` + `RWE_BACKEND_TIMEOUT_MS` onto the `web` service — **done** | **no** — both render to today's behaviour | `git revert` |
| **5b** | web | Log recovery failures and denials; extract `hasEngineUserId()` — **done** | yes — two new log events | `git revert` |
| **5c** | test | Commit the session-recovery acceptance test — **done** | **no** — test-only | `git revert` |
| ~~**6**~~ | ~~web~~ | ~~Call the resolver from `engineAuthHeaders` (immediate heal)~~ — **deleted, redundant** | — | — |
| **7** | docs | Designs flipped to implemented; ops note; accepted-debt register — **done** | no | trivial |
| **S1** | web | Recovery-specific deadline (`recoveryTimeoutMs`) — **done**, from the readiness review | yes — a wedged engine costs a repair 2 s, not 6 | `git revert` |

Each is independently deployable. The ordering constraint is only that **1 should reach production no
later than 5**, because recovery multiplies concurrent first-sightings and 1 is what makes losing one
harmless.

**Commits 5a–5c were not in the original plan.** They came out of a production-readiness review run
against the shipped code, which found that the kill switch never reached the container (so the
documented rollback did nothing), that only recovery *successes* were logged (so a totally broken
recovery was indistinguishable from having nothing to recover), and that the end-to-end proof lived in
a scratchpad file that would not survive the session. The review's remaining findings are deferred, and
tracked as [`SESSION_IDENTITY_RECOVERY_DESIGN.md`](SESSION_IDENTITY_RECOVERY_DESIGN.md) §10 rather than
here — a plan that is finished should not also be a backlog.

### Why commit 6 was deleted

It was planned on a claim that turned out to be false. The reasoning was: `callbacks.jwt` can only make a
heal *durable*, because a server render cannot set a cookie — so something else must run inside
`engineAuthHeaders()` to make the *current* render correct. The first half is true. The second does not
follow, and tracing `next-auth` disproved it.

`getServerSession` → `AuthHandler` → `routes/session.js` calls `callbacks.jwt`, then builds the session
object **from that callback's return value** and assigns it to the response body. Visibility is
in-process and does not involve the cookie at all; only persistence does. Executed against the installed
`next-auth` with our real callbacks and recovery simulated, the token entering `callbacks.jwt` had no
`engineUserId` and the returned session body carried `engineUserId: 42` — while the three cookies the
route queued were handed to `getServerSession`'s no-op `setCookie` and discarded.

So a heal in `callbacks.jwt` is already visible to `engineAuthHeaders()` in the same request. A second
call site would resolve an id that the first call site had, moments earlier, already put on the token it
was about to read.

The remaining question was whether anything reaches `engineAuthHeaders()` *without* running
`callbacks.jwt`. `getServerSession` has three exits: no session cookie, an undecodable cookie, and a
non-JWT session strategy. The third is unreachable (`lib/auth.ts` pins `strategy: "jwt"` and there is no
adapter); the first two were measured returning `body={}` without entering `callbacks.jwt` — and both
occur precisely where **no decodable token exists**, so there is nothing for a resolver to recover
*from*. Nothing in `app/`, `lib/`, `components/` or `middleware.ts` imports `getToken` or
`next-auth/jwt`, so there is no raw-token bypass either. Full trace: recovery design §2a and §2b.

**What this changes for the plan.** Commit 6 carried the only hot-path risk in the whole sequence — a
resolver call inside `engineAuthHeaders()`, which runs in every route handler. Deleting it does not move
that risk to commit 5, it removes it: `callbacks.jwt` already runs once per `getServerSession`, so
commit 5 adds a branch to a callback that was executing anyway rather than a call to a function that was
not. The kill switch stays, now guarding one call site instead of two.

---

## Commit 1 — engine: transaction-retry upsert ✅ implemented

**Files**
- `examples/store.py` — add `OperationalError` to the `sqlalchemy.exc` import; add a private
  `Store._resolve_identity(..., *, create: bool) -> User | None`; rewrite `upsert_user_by_identity` as
  the two-attempt form in §4.
- `tests/concurrency/test_identity_upsert.py` — delete `upsert_reference` and `_attempt`, collapse
  `SUBJECTS` to the real method, remove `XFAIL_SHIPPED`.
- `docs/IDENTITY_UPSERT_CONCURRENCY.md` — status line; `docs/CONCURRENCY_TESTING.md` §5 — reference
  removed.

**Why the test change belongs in this commit and not a follow-up.** `test_I8_...[shipped-*]` is an
`xfail(strict=True)`. The moment `store.py` changes, that XPASSes and **fails the suite on purpose**.
Splitting them would leave commit 1 red on its own, which breaks the "independently testable" rule. The
tripwire was designed to force exactly this pairing.

**Behavioural change.** A caller that loses a first-sighting race resolves to the winner's user instead
of raising. Measured today: 15 concurrent first-sightings → 10 resolve, 5 raise `IntegrityError`. After:
15 resolve, 0 raise. Nothing else changes — the signature, the return type and the profile-refresh
semantics are identical, which matters because there are ~100 call sites (4 in production code, the rest
in tests) and none of them should need touching.

**Tests that must pass** — and did:
```
pytest tests/concurrency -q -m "slow or not slow"     # 27 passed, 0 xfailed
pytest tests -q --ignore=tests/test_plot_axis.py       # 2142 passed, 2 deselected
```
Acceptance, precisely: `test_I8_every_concurrent_caller_resolves` passes for the real method at N = 2
and 15, and `test_I1_I3_I4_concurrent_first_sighting_never_duplicates` reports **`errors == []`** rather
than the `{"IntegrityError"}` it used to tolerate. That assertion was tightened in the same commit — a
suite that still *permits* the old failure is not holding the new contract.

**Rollback.** `git revert`. No schema change, no API change, no data migration. The revert also restores
the xfail, so the suite stays coherent in both directions.

---

## Commit 2 — web: extract the engine upsert (pure move) ✅ implemented

**Files** — new `web/lib/engine-identity.ts` exporting `upsertEngineUser` (moved verbatim from
`lib/auth.ts`); `web/lib/auth.ts` imports it; `web/lib/engine-identity.test.ts` (new) + its entry in
`package.json`'s `test` script.

**Behavioural change: none.** This is the seam commit. Its whole job is to let commit 3's diff be about
recovery logic instead of about moving code around.

**Tests** — the new unit test asserts what the helper already does: the request is keyed on
`providerAccountId` (never email), `X-IH-Auth` is sent when `RWE_INTERNAL_SECRET` is set and omitted
otherwise, and a non-2xx or thrown fetch resolves to `null`. Plus `npm run typecheck`, `lint`, `build`.

**Reviewer's question for this commit**: *did the extraction change anything?* Provably no: the moved
function body is byte-identical to what was removed (checked by diffing the slice against `HEAD`, with
`export` as the only permitted difference), both call sites are untouched, and the production build's
bundle sizes are unchanged — including `Middleware` at 50.4 kB, which confirms nothing leaked into the
edge bundle.

**Rollback.** `git revert`.

---

## Commit 3 — web: the resolver, wired to nothing ✅ implemented

**Files** — `web/lib/engine-identity.ts` gains `resolveEngineUserId(token)`, the process-level memo
(10 min TTL), in-flight coalescing, the 30 s negative backoff, provider gating, the legacy-token `sub`
fallback, and the `isEmailAllowed` re-check. `web/lib/engine-identity.test.ts` gains the nine tests from
the recovery design §8.

**Behavioural change: none — nothing calls it.** This is the commit that keeps the system out of a
partially-correct state: the logic arrives complete and tested before anything depends on it.

**Tests** — design §8 tests 1–9, notably: a token with a numeric `engineUserId` triggers **zero** fetches
(asserted by call count, because a regression there is a per-request engine call in production); two
concurrent calls produce one fetch; a second call inside the TTL produces none; a failure suppresses the
next attempt inside the backoff window; 401/500/timeout each return `null` without throwing.

**Rollback.** `git revert` — or leave it; it is unreachable.

**Measured** (100k healthy resolutions, 2 000 concurrent callers on one identity, 25 000 distinct
identities): 0.43 µs per healthy call with zero engine calls and zero cache entries; 2000:1 coalescing;
99% hit rate at 10 identities × 100 requests; the cache holds at its 1 000-entry ceiling at ~201 bytes
per entry (197 KiB total); 10 000 requests against a dead engine produce **one** call.

**One detail the design did not specify:** it called for a memo "holding a resolved id for a bounded
TTL", but a TTL alone does not bound memory — an entry nobody reads again is never evicted. The
implementation sweeps expired entries on write and caps the map at 1 000 identities, dropping the oldest
beyond that. Dropping an entry costs one engine call and never correctness, so this fills in an
unspecified detail rather than changing an approved one.

---

## Commit 3.5 — web: a deadline for engine calls ✅ implemented

Not in the original plan. The commit-3 review found that `upsertEngineUser` used bare `fetch`, which has
no request timeout of its own — so a wedged engine left the resolver's in-flight promise unsettled, with
no backoff recorded, and every coalesced caller attached to it. Measured before the fix: still pending
after 1500 ms, 1 in-flight entry, 0 memo entries.

**Files** — new `web/lib/engine-timeout.ts` (`engineTimeoutMs`, `fetchWithTimeout`), used by both
`lib/backend.ts` and `lib/engine-identity.ts`; `web/lib/engine-timeout.test.ts`.

**Why a new module rather than reusing `backend.ts`'s `withTimeout`.** That function is module-private
and does three things beyond timing out: it merges `X-Forwarded-For`, forces `cache: "no-store"`, and
swallows failures into `null`. Adopting it wholesale would have started forwarding the client IP on the
sign-in upsert, which feeds the engine's per-IP rate limiting — a behaviour change outside this fix. The
*primitive* is shared instead; the policy stays at each call site.

**The deadline both aborts and races.** Aborting frees the socket, which is what a cooperating transport
needs; the race is what makes "the caller always settles" independent of whether the transport honours
the signal. A helper whose entire purpose is that nobody is left attached to a pending promise should
not delegate that guarantee to the thing that is misbehaving.

**Rollback.** `git revert`. Reverting restores bare `fetch` in both call sites.

## Commit 4 — web: persist the identity claims at sign-in ✅ implemented

**Files** — `web/lib/auth.ts` (`callbacks.jwt` writes `token.provider` and `token.providerAccountId` when
`account` is present); `web/types/next-auth.d.ts` (augment the `JWT` interface alongside the existing
`engineUserId?: number`).

**Behavioural change: none observable.** New tokens carry two extra claims (~50 bytes, far below the
4096-byte chunking threshold); nothing reads them until commit 5. Old tokens are untouched and remain
valid — the resolver's `sub` fallback covers them.

**Why before commit 5.** So that by the time recovery is live, every token minted after this deploy
already carries the exact key, and only pre-existing sessions rely on the fallback. Reversing the order
would work but would widen the window in which the fallback is the only path.

**Tests** — new `web/lib/auth-callbacks.test.ts`, nine of them. Getting there needed a **pure move**
that was not in this plan: `lib/auth.ts` builds the providers at module load, and
`next-auth/providers/*` are CommonJS, so importing that module from bare `node --test` fails on the
CJS/ESM default interop that webpack and tsc paper over — the callbacks were untestable where they sat.
They now live in `lib/auth-callbacks.ts`, unchanged, and `lib/auth.ts` references them. Commit 5's
required callback tests would have hit the same wall.

**Two implementation details worth the reviewer's eye.** `providerAccountId` is recorded for Google
only: NextAuth sets `account.providerAccountId` to `user.id` for the credentials provider, which
`authorize()` sets to the *engine user id*, while the dev upsert keys on the email — so recording it
under that name would promise something it is not. `provider` is recorded for both, which is what lets
a reader of a dev token know not to treat it as Google. And the claims are written even when the engine
call failed, since that is precisely the session recovery will later need to repair.

**Rollback.** `git revert`. Tokens minted meanwhile keep two claims nothing reads — harmless.

---

## Commit 5 — web: heal in `callbacks.jwt` (the only call site) ✅ implemented

**Files** — `web/lib/auth-callbacks.ts` (call the resolver when `token.engineUserId` is missing **and
`account` is absent**); `web/lib/engine-identity.ts` (read the `RWE_IDENTITY_RECOVERY` kill switch);
`web/lib/auth-callbacks.test.ts`.

Note the file: the callbacks moved out of `lib/auth.ts` in commit 4 so they could be unit-tested without
loading the CommonJS provider modules. `lib/auth.ts` is untouched by this commit.

**Behavioural change.** A session that lacks an engine identity is repaired on the next
`getServerSession` — *both* halves of that, which is the point commit 6 existed to cover and does not
need to:

- **The current request is already correct.** The session object is built from `callbacks.jwt`'s return
  value, in-process, so `engineAuthHeaders()` reads the healed id on the same render (§2a).
- **The heal becomes durable** on the next response that can actually set cookies — the
  `/api/auth/session` fetch `SessionProvider` issues on mount and on window focus — after which the id
  rides in the signed cookie for the session's lifetime.

**Three things the design added after the walkthrough, all of which land here:**

1. **The `!account` guard** — recovery must not run on the sign-in invocation. Without it, a sign-in that
   just failed to reach the engine would immediately make a *second* upsert attempt, milliseconds later,
   with no memo entry yet to suppress it. Stated as an invariant in recovery design §3.
2. **Allowlist denials are memoized** — `isEmailAllowed` does an uncached `readFileSync`, and
   `callbacks.jwt` runs on every session read, so an unrecorded denial means synchronous file I/O on
   every server render for the 30-day life of the token. The denial writes a negative memo entry like any
   other failure (design §4). *This is resolver-side and is already implemented in commit 3; the test
   pinning it lands here alongside the caller.*
3. **The kill switch**, `RWE_IDENTITY_RECOVERY`, read at call time.

**Tests** — design §8 tests 10–13: the callback calls the resolver when the id is missing, does not when
it is present, writes the result to the token, and — test 13 — **does not call the resolver on the
sign-in invocation even when the id is missing**, asserted by resolver call count so that removing the
guard fails the suite.

**Rollback.** `RWE_IDENTITY_RECOVERY=0` in `deploy/.env` + `bash deploy/ops/restart.sh web` restores
today's behaviour without a rebuild. `git revert` if the flag is not enough.

> This was **not true when commit 5 shipped** — the variable was not on the `web` service in either
> compose file, and neither has an `env_file:`, so setting it in `deploy/.env` did nothing at all.
> Commit 5a wired it (and `RWE_BACKEND_TIMEOUT_MS`) onto the service and added the
> `web-identity-recovery-switch` rule to `deploy/deployment-rules.json` so the omission cannot recur.
> Nothing about the flag's *semantics* changed; it simply could not reach the container.

---

## Commit 5a — deploy: plumb the kill switch and the engine deadline ✅ implemented

**Files** — `deploy/docker-compose.yml`, `deploy/docker-compose.aws.yml` (two variables on the `web`
service), `deploy/deployment-rules.json` (a rule that fails the validator if they go missing),
`docs/PRODUCTION_ENVIRONMENT.md`.

**Behavioural change.** None. Both variables render to values that reproduce current behaviour when
unset: `RWE_IDENTITY_RECOVERY=1` (on, the code default) and `RWE_BACKEND_TIMEOUT_MS=""` (empty, which
`engineTimeoutMs()` already maps to 6000). No application file was touched.

**Why it is its own commit.** It is the rollback lever for commit 5, and a lever has to be in place
before the thing it disarms. It is also the only commit in this plan that changes nothing an automated
test could observe from inside the app, which is precisely why it needs a deployment-manifest rule
rather than a unit test.

**Rollback.** `git revert`. Reverting removes the variables from the service, which returns the stack to
its pre-5a state — recovery still on, still 6000 ms, still unswitchable.

---

## Commit 5b — web: make a failing recovery visible ✅ implemented

**Files** — `web/lib/engine-identity.ts` (`attemptEngineUpsert` + `logRecovery` + `hasEngineUserId`),
`web/lib/auth-callbacks.ts` (uses the predicate), both test files.

**Behavioural change.** Two new log events; no change to what recovery attempts or returns.
`upsertEngineUser` is now a wrapper over `attemptEngineUpsert`, which keeps the failure reason instead of
collapsing everything to `null` — same signature, same return type, both sign-in call sites untouched.

**Why it was needed.** Only successes were logged, which inverted the signal: a recovery failing for
every reader (a mismatched `RWE_INTERNAL_SECRET` being the realistic cause) produced logs identical to
having no broken sessions. You could ship the repair and never learn it had never worked.

**Tests** — 18 new. Each reason code; the denial's email and reason; and the property that makes the log
usable under load: **one line per attempt, never per request** — 15 session reads, 30 renders during an
outage, 25 coalesced callers and 30 cached denials each produce exactly one line.

**Rollback.** `git revert`. The events are additive; nothing parses them yet.

---

## Commit 5c — test: commit the acceptance harness ✅ implemented

**Files** — `web/lib/session-recovery.test.ts` (new), `web/package.json`.

**Behavioural change.** None — test-only.

**Why it was needed.** The end-to-end proof of design §2a lived in a scratchpad file outside the repo.
Everything committed stubbed `fetch` and tested the resolver and the callback separately, so nothing
exercised the hop between them — which is the claim the whole design rests on.

**Tests** — the harness's 32 checks reduced to 9, dropping what the unit suites already own. Verified by
**mutation**, not by passing: removing the recovery call fails 5 of 9; not writing the resolved id back
to the token fails 3; dropping the `!account` guard, breaking in-flight coalescing, and ignoring the kill
switch each fail exactly the test written for them.

**Note for future maintainers.** It reaches into `next-auth/core`, outside the package's `exports` map.
That is deliberate and documented in the file header: it is an assumption detector, so a failure after a
NextAuth upgrade means re-read `core/routes/session.js` and revalidate design §2a — not loosen the
assertion.

**Rollback.** `git revert`.

---

## Commit 7 — docs ✅ implemented

Both design documents flipped from proposal to implemented; the operational section (log events, what
each `reason` means and what to do about it, how to disable recovery) added as design §5a; the
allowlist-shortcut invariant written down in §4; and the production-readiness review's deferred findings
and accepted debt recorded in §10 so they are decisions with reasons rather than omissions.

**Follow-ups still open:** S2 (`refreshProfile`, engine then web) and S4b (Playwright test 14),
both specified in [`SESSION_IDENTITY_RECOVERY_DESIGN.md`](SESSION_IDENTITY_RECOVERY_DESIGN.md) §10.
S1 is closed — see the commit table.

**Not done here:** recording the observed recovery-log volume after a day in production. That needs
production, and deploys are manual. It is the first thing to add once the stack has run with commit 5
for a day — the expected steady state is *zero* `engine_identity_*` lines, with a burst after each
deploy that restarts the engine.

---

## Migration and deployment risks

| Risk | Assessment | Mitigation |
|---|---|---|
| **Schema migration** | None. No table, column, index or constraint changes anywhere in this work. | — |
| **Deploy replaces both containers** | `update.sh` runs `dc up -d`; engine and web restart together. Commits 1 and 5 can therefore land in one deploy. | Deploy in branch order; nothing requires a staged rollout. |
| **The deploy itself creates the bug being fixed** | Restarting the API is exactly when a sign-in can miss its engine upsert. A session broken by a deploy is repaired on its **next server render**, and the repair becomes durable on the client's next `/api/auth/session` fetch. | Expected, self-correcting, worth knowing before someone reports it. |
| **Allowlist read on the auth path** | `callbacks.jwt` runs on every session read, so an un-memoized denial would mean a `readFileSync` per render for the life of the token. | The denial is memoized (design §4). The test for it is part of commit 5. |
| **Edge runtime** | `middleware.ts` imports only `withAuth`, so `lib/beta-access.ts` (which uses `node:fs`) is not in the edge bundle. **Importing the resolver from middleware would break the build.** | Do not. `withAuth` does not invoke `callbacks.jwt`, so there is no reason to. |
| **Allowlist read on the recovery path** | `isEmailAllowed` does a `readFileSync`; `BETA_ALLOWLIST_FILE` must be mounted in the web container. It is (the read-only `/app/data` mount added earlier). | Bounded by the backoff — at most once per identity per 30 s on the failing path. |
| **Recovery storm after an outage** | Every affected identity attempts once per process per TTL; coalescing bounds concurrency per identity. | The 30 s negative backoff is what keeps a sick engine from being hammered. Watch the log line. |
| **Engine-side race becomes routine** | Recovery multiplies concurrent first-sightings. Before commit 1, losers get a 500. | Ship 1 no later than 5 — the ordering above already does this. |
| **CI matrix** | The concurrency suite's fast probes run on Python 3.11 **and** 3.12. Commit 1 must be green on both; I validated locally on 3.11 only. | The first CI run on commit 1 is the check. A red `ID1`/`ID2` there is the detector working, not a broken commit. |
| **Rollback of a healed token** | A token healed by commit 5 keeps its `engineUserId` after a revert. Nothing reads it differently. | None needed; healing is not reversible and does not need to be. |

## No commit leaves a partially-correct state

The property to check per commit, and how each satisfies it:

1. **Commit 1** changes engine behaviour alone. The HTTP contract of `POST /api/internal/users` is
   unchanged — same request, same response, same status codes. The web tier cannot tell the difference
   except that it stops seeing 500s under concurrency.
2. **Commits 2–4** are behaviour-neutral in production by construction: a move, dead code, and two claims
   nothing reads.
3. **Commit 5** is the first observable web change, and it is fail-soft: the resolver returns `null` on
   any failure, which reproduces today's behaviour exactly. It is also the last one — with commit 6 gone,
   there is no state in which one call site heals and the other does not, and no possibility of the two
   disagreeing about when to attempt.

There is no intermediate state in which a token carries a claim something misreads, or in which the
upsert is half-migrated. The two tiers are independent: web commits work against an unmodified engine
(they just have more losers to recover from), and commit 1 works with an unmodified web tier.

## Acceptance criteria, from the existing harness

Commit 1 is done when, with `upsert_reference` deleted:

- `pytest tests/concurrency -q -m "slow or not slow"` → **27 passed, 0 xfailed** (the xfail is gone
  because the behaviour it described is gone). *This line said 35 until commit 5 measured it: 35 was a
  forecast written before commit 1, and deleting `upsert_reference` took its parameterised cases with
  it. The substantive criteria below are what the number was standing in for.*
- `test_I1_I3_I4_concurrent_first_sighting_never_duplicates` asserts `errors == []` at N = 2, 8, 15.
- Every premise test in `test_storage_premises.py` is **untouched**. If commit 1 needs one of them
  changed, the design changed too and this plan is stale — stop and re-read
  [`CONCURRENCY_TESTING.md`](CONCURRENCY_TESTING.md) §4.
- `pytest tests -q` green on 3.11 and 3.12.

The web commits are done when the tests in the recovery design §8 pass — 1–9 in
`lib/engine-identity.test.ts`, 10–13 in `lib/auth-callbacks.test.ts`, and the deadline tests in
`lib/engine-timeout.test.ts` — and
`npm run typecheck && npm run lint && npm run check:i18n && npm test && npm run build` is clean. Test 14
is the Playwright e2e, run separately with `npm run e2e`.

## Where implementation will differ from the design, and why

| # | Design says | Plan does | Why |
|---|---|---|---|
| 1 | "One new module, `lib/engine-identity.ts`, exporting a single memoized resolver." | The module also owns `upsertEngineUser`, moved out of `lib/auth.ts`. | That helper is module-private in `auth.ts` today and both the sign-in path and the resolver need it. Exporting it from `auth.ts` would leave the dependency pointing the wrong way — the auth layer would own a helper the identity module depends on. |
| 2 | No feature flag mentioned. | `RWE_IDENTITY_RECOVERY` (default on, `0` disables), read at call time. | Kept even after commit 6 was dropped: recovery is the one change here that puts an outbound engine call on a path that previously made none, and the repo already uses this idiom (`RWE_BACKUP_COMPRESS`, `RWE_ALLOW_MOCK_FALLBACK`). It turns a rollback from "rebuild and redeploy" into "edit `.env`, restart web". |
| 3 | ~~Both call sites presented together.~~ Superseded — the design now specifies **one** call site (§2), because the second was traced and found redundant. | One commit (5). | See *Why commit 6 was deleted*, above. The plan's original split into 5 and 6 was sound given what the design then claimed; it stopped being needed when the claim was checked. |
| 6 | Design §3's recovery condition, as first written, was "id missing". | `!account && token.engineUserId == null`. | Found by the state-machine walkthrough: on the sign-in invocation the unguarded form fires a second upsert milliseconds after sign-in's own failed one. Now an invariant in design §3 rather than a plan deviation, but recorded here because it is a change to what was approved. |
| 7 | Design §5 treated the allowlist re-check as a cheap guard. | The denial is memoized like any other negative result. | `isEmailAllowed` does an uncached `readFileSync` and `callbacks.jwt` runs per session read. Now design §4. |
| 4 | `_resolve_identity` shown as a free function. | A private method on `Store`. | It needs `self.session()`, and every other operation in that class is a method. |
| 5 | Recovery logging described as one structured line. | Same, but emitted from the **web** tier (`console.warn(JSON.stringify({event: "engine_identity_recovered", ...}))`), not the engine. | The engine cannot distinguish a recovery upsert from a sign-in upsert — they are the same call. Only the web tier knows why it is calling. |

Nothing in the algorithm itself differs. §4 as written in the contract is what commit 1 implements, and
`upsert_reference` in the harness is a working copy of it, so the diff between the two is the acceptance
test rather than a matter of interpretation.

## Not in this plan

- **The unique-index ops confirmation** (`PRAGMA index_list('identities')` on the live database). One
  line, worth running before commit 1, but it is an ops check rather than a commit.
- **R4** — real multi-process safety. Still modelled rather than proven; unchanged by this work.
- **Making sign-in fail when the engine is down.** Out of scope by the design's own §9, and it would
  remove the need for recovery entirely, so it deserves its own argument rather than riding along here.
