# Implementation Plan — Identity Recovery + Transaction-Retry Upsert

**Commits 1–3 and 3.5 are implemented; commits 4–7 are not.** This is the roadmap for two designs that are already
reviewed:

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
| **4** | web | Persist `provider` + `providerAccountId` claims at sign-in | **no** — nothing reads them yet | `git revert` |
| **5** | web | Call the resolver from `callbacks.jwt` (durable heal), behind a kill switch | yes — broken sessions start healing | env flag, then revert |
| **6** | web | Call the resolver from `engineAuthHeaders` (immediate heal) | yes — hot path gains a guarded call | env flag, then revert |
| **7** | docs | Flip both designs from proposal to implemented; ops note | no | trivial |

Six of the seven are independently deployable. The ordering constraint is only that **1 should reach
production no later than 5**, because recovery multiplies concurrent first-sightings and 1 is what makes
losing one harmless.

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

## Commit 4 — web: persist the identity claims at sign-in

**Files** — `web/lib/auth.ts` (`callbacks.jwt` writes `token.provider` and `token.providerAccountId` when
`account` is present); `web/types/next-auth.d.ts` (augment the `JWT` interface alongside the existing
`engineUserId?: number`).

**Behavioural change: none observable.** New tokens carry two extra claims (~50 bytes, far below the
4096-byte chunking threshold); nothing reads them until commit 5. Old tokens are untouched and remain
valid — the resolver's `sub` fallback covers them.

**Why before commit 5.** So that by the time recovery is live, every token minted after this deploy
already carries the exact key, and only pre-existing sessions rely on the fallback. Reversing the order
would work but would widen the window in which the fallback is the only path.

**Tests** — new `web/lib/auth.test.ts`: the `jwt` callback writes both claims on the sign-in invocation
and preserves them on later invocations. Typecheck covers the augmentation.

**Rollback.** `git revert`. Tokens minted meanwhile keep two claims nothing reads — harmless.

---

## Commit 5 — web: durable heal in `callbacks.jwt`

**Files** — `web/lib/auth.ts` (call the resolver when `token.engineUserId` is missing);
`web/lib/engine-identity.ts` (read the `RWE_IDENTITY_RECOVERY` kill switch); tests.

**Behavioural change.** A session that lacks an engine identity is repaired on the next
`/api/auth/session` fetch — which `SessionProvider` issues on mount of any page — and the healed id then
rides in the cookie for the session's lifetime.

**Tests** — design §8 tests 10–12: the callback calls the resolver when the id is missing, does not when
it is present, and writes the result to the token.

**Rollback.** `RWE_IDENTITY_RECOVERY=0` in `deploy/.env` + `docker compose up -d web` restores today's
behaviour without a rebuild. `git revert` if the flag is not enough.

---

## Commit 6 — web: immediate heal in `engineAuthHeaders`

**Files** — `web/lib/engine-auth.ts`; test.

**Behavioural change.** The *current* server render is attributed correctly, not just the next one. This
is the only commit that touches a hot path: `engineAuthHeaders()` runs in every route handler and in the
app-shell onboarding gate.

**Why last, and why separate.** The memo makes it at most one engine call per identity per 10 minutes per
process, and the healthy path is a single `typeof` check — but "at most" is an argument, and this is the
commit where an argument meets production traffic. Separating it means you can keep the durable heal and
drop the immediate one without unwinding anything else.

**Tests** — a healthy session produces zero extra fetches; a session without an id produces exactly one,
and the resulting headers carry `X-IH-User-Id`.

**Rollback.** Same kill switch, then revert. Reverting 6 alone leaves 5 working.

---

## Commit 7 — docs

Flip both design documents from proposal to implemented, record the observed recovery-log volume after a
day in production, and add the ops note: what `engine_identity_recovered` means, how to disable recovery,
and what a *rising* rate indicates (sign-in-time engine unavailability, not a recovery problem).

---

## Migration and deployment risks

| Risk | Assessment | Mitigation |
|---|---|---|
| **Schema migration** | None. No table, column, index or constraint changes anywhere in this work. | — |
| **Deploy replaces both containers** | `update.sh` runs `dc up -d`; engine and web restart together. Commits 1 and 5 can therefore land in one deploy. | Deploy in branch order; nothing requires a staged rollout. |
| **The deploy itself creates the bug being fixed** | Restarting the API is exactly when a sign-in can miss its engine upsert. The first deploy carrying recovery cannot heal a session broken *by that same deploy* until the client refetches `/api/auth/session` — which happens on the next page load. | Expected, self-correcting, worth knowing before someone reports it. |
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
   any failure, which reproduces today's behaviour exactly.
4. **Commit 6** adds a second call site to the same already-tested resolver.

There is no intermediate state in which a token carries a claim something misreads, or in which the
upsert is half-migrated. The two tiers are independent: web commits work against an unmodified engine
(they just have more losers to recover from), and commit 1 works with an unmodified web tier.

## Acceptance criteria, from the existing harness

Commit 1 is done when, with `upsert_reference` deleted:

- `pytest tests/concurrency -q -m "slow or not slow"` → **35 passed, 0 xfailed** (the xfail is gone
  because the behaviour it described is gone).
- `test_I1_I3_I4_concurrent_first_sighting_never_duplicates` asserts `errors == []` at N = 2, 8, 15.
- Every premise test in `test_storage_premises.py` is **untouched**. If commit 1 needs one of them
  changed, the design changed too and this plan is stale — stop and re-read
  [`CONCURRENCY_TESTING.md`](CONCURRENCY_TESTING.md) §4.
- `pytest tests -q` green on 3.11 and 3.12.

The web commits are done when the twelve tests in the recovery design §8 pass and
`npm run typecheck && npm run lint && npm run check:i18n && npm test && npm run build` is clean.

## Where implementation will differ from the design, and why

| # | Design says | Plan does | Why |
|---|---|---|---|
| 1 | "One new module, `lib/engine-identity.ts`, exporting a single memoized resolver." | The module also owns `upsertEngineUser`, moved out of `lib/auth.ts`. | That helper is module-private in `auth.ts` today and both the sign-in path and the resolver need it. Exporting it from `auth.ts` would leave the dependency pointing the wrong way — the auth layer would own a helper the identity module depends on. |
| 2 | No feature flag mentioned. | `RWE_IDENTITY_RECOVERY` (default on, `0` disables), read at call time. | Commit 6 touches a hot path. The repo already uses this idiom (`RWE_BACKUP_COMPRESS`, `RWE_ALLOW_MOCK_FALLBACK`), and it turns a rollback from "rebuild and redeploy" into "edit `.env`, restart web". |
| 3 | Both call sites presented together. | Two commits (5 and 6). | The durable heal is low-risk and the immediate heal is not. Splitting them makes the risky half independently revertable. |
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
