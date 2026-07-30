# Onboarding Lifecycle & Architecture

How a reader becomes a Hidden View user, where that decision is made, and why it is made **there**
and nowhere else.

This document covers the *entry* lifecycle — visitor → account → onboarded. What happens after it,
the Estimate → Measured ladder, is [`PROGRESSIVE_JOURNEY.md`](PROGRESSIVE_JOURNEY.md).

---

## 1. The bug this architecture fixes

Onboarding had **two entry paths and one initializer**.

| Path | What it did | Left the reader |
|---|---|---|
| `/onboarding` → pick outlets → estimate → `/signin` | stashed the selection, signed in, flushed it to the account | onboarded ✅ |
| `/signin` directly | signed in, returned to `callbackUrl: "/"` | in the app with **no outlets and no reads** ❌ |

The second path is not hypothetical or rare. `/signin` is a public route and it is where three real
flows land:

- a **beta invite** ("here's the link, sign in"),
- an **`?error=AccessDenied` bounce** off the beta allowlist, which NextAuth sends to `/signin`,
- a **bookmark** or shared link, since the funnel's own last step is `/signin`.

`middleware.ts` guards the app pages, but `withAuth` only answers *"are you signed in?"*. Nothing
asked *"have you onboarded?"* — so a reader from path 2 reached every personalised surface with an
empty account. That is the state in which the Health Report, Reading History context,
Recommendations, the home health panel and the Guide each fell back to a *different* reader's
numbers. Those fallbacks are fixed at the source (see `is_sample` in `examples/api_fastapi.py`), but
the fallbacks were the symptom. **Reaching the app un-onboarded was the cause.**

## 2. The lifecycle

One path, whatever the sign-in route — OAuth, invite link, allowlist, direct `/signin`, or any auth
provider added later. Providers plug in below the gate, so a new one inherits it for free.

| State | Store says | Where the reader is sent | By |
|---|---|---|---|
| **Visitor**, no session | — | `/onboarding` (the funnel) | `middleware.ts` |
| **In the funnel**, anonymous | nothing yet | stays; selection stashed in the browser | `onboarding-flow.tsx` |
| **Just signed in** | row written *here*, if one was stashed | `/signin/complete`, then `/` | the landing step (§5) |
| **Signed in, no onboarding, no reads** | `onboarding: absent`, `reads: 0` | `/onboarding` | the gate in `app/(app)/layout.tsx` |
| **Onboarded**, `reads < 5` | row + `reads < 5` | the app, in **Estimate** mode | `PROGRESSIVE_JOURNEY.md` |
| **Established**, `reads ≥ 5` | row + `reads ≥ 5` | the app, in **Measured** mode | idem |
| **Reads but no row** (extension-first, or an account predating the gate) | `onboarding: absent`, `reads > 0` | the app — treated as onboarded | the gate's `reads` clause |

That last row is why the gate reads two facts instead of one. Someone whose reading arrived through
the browser extension has onboarded in substance; bouncing them into an outlet picker would be a
worse bug than the one being fixed.

## 3. Where each decision lives

| File | Answers | Notes |
|---|---|---|
| `web/middleware.ts` | *Are you signed in?* | Unchanged. Sends anonymous visitors to `/onboarding`. |
| `web/app/(app)/layout.tsx` | *Have you onboarded?* | **The gate.** The only place this is decided. |
| `web/lib/onboarding.ts` | the pending-selection stash | One key, one shape, one parser. Total and consuming (§5). |
| `web/app/signin/complete/page.tsx` | lands an anonymous selection post-auth | Where sign-in returns. Pass-through for returning readers. |
| `web/app/signin/page.tsx` | provider choice | Both providers use `callbackUrl: "/signin/complete"`. |
| `web/components/onboarding/onboarding-flow.tsx` | the funnel + **two** save paths | §6. |
| engine `GET /api/me` | the state the gate reads | `onboarding` + `reads`, one call. |
| engine `POST /api/me/onboarding` | the write | Recomputes and stores the estimate. Idempotent. |

## 4. Why the gate is a server component in the app-shell layout

`app/(app)/layout.tsx` is the one thing every protected page already renders through. Putting the
check there means:

- **One choke point.** A new page under `(app)` is gated the day it is added; there is no list to
  remember to update.
- **No flash.** It is a server component, so the redirect happens before any HTML reaches the
  browser — not a rendered dashboard that jumps away.
- **A loop is structurally impossible.** `/onboarding` lives *outside* the `(app)` group, so the
  destination of the redirect cannot itself be gated. This is a property of the route tree, not a
  guard condition someone has to maintain.
- **One extra engine call.** `GET /api/me` is three keyed reads — the onboarding row by primary key,
  the latest report snapshot, and an indexed `COUNT` over `reads` — plus a JSON parse of that
  snapshot, of which the gate uses none. Next memoizes `fetch` within a render pass, so a page that
  also reads `/api/me` shares the response. The residual cost is one keyed query and a small parse on
  app pages that did not previously call it.

Alternatives, and why not:

| Instead | Why not |
|---|---|
| Extend `middleware.ts` | It runs before every request including assets, and would need an engine round trip per request to answer a question about stored state. The session is a stateless JWT; it does not carry onboarding status. |
| Check in each page | N places to forget, and the newest page is always the one that forgets. |
| A client-side `useEffect` redirect | A visible flash of a page the reader shouldn't see, and trivially bypassed with JS off. |
| Put it in the NextAuth `session`/`jwt` callback | Onboarding state changes *after* sign-in; a JWT minted at sign-in would be stale, and forcing a re-mint is a bigger mechanism than the gate. |

## 5. The ordering problem, and why it is solved with ordering

An anonymous visitor picks outlets **before** an account exists, so the selection is stashed in the
browser (`lib/onboarding.ts`) — and only the browser can read it back. The gate runs on the server.
Land sign-in on `/` and those two facts collide: the store still knows nothing about the reader, so
the gate sends someone who *just finished the funnel* back into it. That would have broken the
primary acquisition path, which is worse than the bug being fixed.

The fix is a step, not a mechanism. Both providers use `callbackUrl: "/signin/complete"`, a tiny
public page outside `(app)` that persists the stash and only then moves on. By the time any gated
page renders, the store is authoritative again.

The step is **check-then-write**: `GET /api/me` first, and the stash is landed only when
`needsOnboarding()` says the account has never been initialized. That predicate lives in
`lib/onboarding.ts` and is the *same function the gate calls*, so the two cannot form a differing
opinion about who is new — a landing step that thought an account was established while the gate
thought otherwise is exactly the shape a redirect loop takes.

What that buys, beyond working:

- **The gate has no exception.** It reads the store, full stop — no grace window, no client-set flag
  it has to take on trust, nothing to keep in sync with a second store.
- **It cannot loop.** The stash is *consumed* on success (`clearPendingOnboarding`). Even in the
  pathological case where the gate disagreed after a successful write, the second pass finds nothing
  to re-post and terminates on the funnel.
- **It is idempotent by check, not by luck.** A refresh mid-write, a duplicated tab, React's
  double-invoked effect in development, or a bookmarked URL all find the row already present and pass
  through. The write being an upsert is the second line of defence, not the first.
- **An established account is never overwritten.** A stash abandoned in this browser months ago would
  otherwise replace a real reader's outlets — and because the write also stores a fresh estimate
  snapshot, and `latest_report` returns the newest, it would demote a Measured report to an Estimate.
- **Nothing is unbounded.** The round trip is capped (12 s, above the engine's own 6 s timeout), so a
  hung server produces the retry card rather than a spinner that never resolves. Retries are capped
  too: after two, the funnel becomes the primary action, because a failure that survives two attempts
  is one the reader fixes by re-picking rather than by waiting.
- **Back cannot return to it.** The step `replace`s its own history entry, so the dashboard's Back
  goes where it went before this change existed. It is a full document load rather than
  `router.replace` on purpose: a client navigation may be served from the Router Cache, and a payload
  rendered before the row existed would redirect a reader who is now perfectly onboarded.
- **The first dashboard paint is already personalised.** The row exists before `/` renders. A
  background flush racing the first render would have shown an un-onboarded dashboard to the reader
  whose selection had just been accepted.
- **The failure path lands somewhere useful.** If the write fails, the reader is on a page that says
  so, with a retry and a link back to the funnel — and the stash is intact. Nobody is parked in an
  empty app waiting on a retry that may never come.
- **It is one file with one job.** For a returning reader with nothing stashed it is a pass-through:
  read, find nothing, continue to `/`.

The cost is one small static page (2.7 kB, prerendered) and a spinner between sign-in and the
dashboard for as long as the write takes. `<noscript>` refreshes to `/`, so a JS-less browser is
never stranded — though sign-in itself needs JS, so that case is belt-and-braces.

### Fails open — deliberately

The gate does **not** redirect when the engine is unreachable.

Compare with the **beta access gate**, which fails *closed*: there, letting the wrong person in is
the harm. Here the harm runs the other way — an engine blip that bounced every signed-in reader into
a funnel they finished months ago would be far worse than the bug being fixed. And a reader who does
slip through un-onboarded now sees honest empty states rather than someone else's data, because that
was fixed separately.

### Alternatives considered

An earlier revision of this change used a **marker cookie**: the funnel set a payload-free
`ih_pending_onboarding` flag and the gate skipped itself while it was present. It worked, and it was
tested, but it was the wrong shape — it made the gate depend on an unverifiable client claim, kept
two client stores in sync (the stash and the flag announcing the stash), needed a TTL to bound its own
failure, and left a reader whose flush failed browsing an empty app for up to half an hour. It was
compensating for the ordering problem rather than fixing it.

| Alternative | Why not |
|---|---|
| **Marker cookie** — client flag, gate skips while set | A second piece of state whose only job is to describe the first; an unverifiable claim (anyone can set it); a TTL that is load-bearing; a failed flush leaves the reader in an empty app; and the first dashboard render still races the write. |
| **Payload cookie** — put the selection itself in a cookie the gate reads | Same unverifiable-claim problem, plus reading preferences in a header on every request. The gate still cannot *persist* it (a layout cannot write), so a flush component is still needed. Strictly worse than the marker. |
| **Persist inside the NextAuth callback** (`events.signIn`) | Feasible — `callbacks.jwt` already upserts the engine user, so the id is available. Rejected: it puts an engine write in the auth critical path (a slow engine becomes slow sign-in), couples the most security-sensitive file in the web tier to onboarding, depends on v4 awaiting event handlers, and its failure path still needs a retry surface — which is the landing page, so the landing page would exist anyway. |
| **Selection in the `callbackUrl` query string** | Works (NextAuth's default `redirect` callback preserves same-origin paths verbatim), and needs no stash at all. Rejected: it writes a reader's publisher choices into URLs, browser history, and server access logs — a privacy regression for zero architectural gain over the landing page. |
| **Let the gate redirect to `/onboarding` and flush there** | No new state either, but the funnel is prerendered, so the reader sees a flash of the Welcome screen before hydration decides to resume — a "did it lose my picks?" moment at the most fragile point in the funnel. A dedicated landing step shows a purpose-built interstitial instead. |
| **Run the flush before the gate** | Not possible. The gate is a server redirect; it completes before any client JS for that navigation runs. Anything that "runs first" on the client means a client-side gate — flash, bypassable, and dependent on JS. |
| **Client-side gate** | Rejected for the same reasons plus the original bug: a redirect that a signed-in reader can skip is not a gate. |

## 6. Two persistence paths, because there are two situations

| Reader | Path | Why |
|---|---|---|
| **Anonymous** (unchanged in substance) | stash → `/signin` → `/signin/complete` → `POST /api/me/onboarding` | There is nowhere server-side to put a selection made before the account exists. |
| **Authenticated** (new) | `POST /api/me/onboarding` directly, then `/` | The account already exists, so the round trip through the stash is pure risk: a stale item could clobber the fresh choice, and this reader will not cross the sign-in step again, which is where the stash is read. |

The authenticated path exists because the gate creates a reader who is *signed in and on the
funnel* — a combination that could not happen before. If its `POST` fails, the flow stays on the
estimate screen, says so (`onboarding.saveFailed`), stashes the selection anyway, and re-enables the
button. It deliberately does **not** navigate to `/`: the gate would only send it back, which would
read as a loop.

## 7. What was deliberately not built

**No `skipped_at` (or any second onboarding state).** Checked against the code rather than assumed:
the funnel's "See a sample first" button calls `sample()`, which does
`setSelected(new Set(spread))` and builds an estimate from six pre-picked outlets — it *pre-fills*,
it does not skip. Every exit from `/onboarding` runs through the same `save()` carrying `outletIds`,
so "onboarded with no outlets" and "half-onboarded" are states no current journey can reach. A column
for them would be untested, unreachable state.

What would change that: a real *Skip* control that lands a reader in the app with no outlets, or a
multi-step funnel where a partial selection must survive a page close. Neither exists today.

**No new onboarding UI.** No tour, no coach marks, no tooltips. The verified problem was that a
signed-in reader could reach the app without an account being initialised — an architecture bug, not
a comprehension bug. Adding guidance on top of it would have decorated the failure.

## 8. Edge cases considered

| Case | Behaviour |
|---|---|
| Beta invite link → `/signin` → `/` | Gate → funnel. This is the path that was broken. |
| `?error=AccessDenied` bounce, then a successful sign-in | Same as above; no special case needed. |
| Anonymous funnel → Google → `/signin/complete` | The stash is persisted there, then `/` renders an onboarded reader (§5). |
| Reader clears storage between the funnel and sign-in | `/signin/complete` finds nothing, passes through to `/`, and the gate shows the funnel. Correct — there is no selection to honour. |
| Reader signs in with the stash from a funnel they abandoned days ago | It is persisted. That is the selection they made; the funnel is the only thing that writes it, and they can change outlets in Settings. |
| Two tabs, both signed in | `POST /api/me/onboarding` is an upsert, so a double landing is idempotent. |
| Sign-in of a returning reader | `/signin/complete` reads nothing and forwards to `/` — one client navigation on a 2.7 kB prerendered page. |
| Write fails at `/signin/complete` | The page says so, offers a retry, and links back to the funnel; the stash is untouched. Navigating to `/` instead hits the gate, which sends them to the funnel — the same place. |
| Someone opens `/signin/complete` while signed out | No session to attribute, so it forwards to `/` and the middleware takes over. |
| Extension-only reader (reads, no row) | Passes the gate on the `reads` clause. |
| Accounts created before the gate existed | Same clause: any reading at all is enough. An account with neither reads nor outlets sees the funnel once, which is the correct outcome. |
| Engine down | Fails open; the app renders with honest empty states. |
| Signed in, visits `/onboarding` directly | Sees the funnel and saves through the authenticated path. Nothing forces them out — `/onboarding` is public by design. |

### Reviewed: refresh, duplicate submit, Back, tabs, interrupted OAuth, retries

| Scenario | Behaviour |
|---|---|
| **Refresh while the spinner is up** | The write may already have landed; the reload's `/api/me` check sees the row, discards the stash, and passes through. No duplicate write. |
| **Refresh after success** | Stash is gone, so it is the returning-reader pass-through. |
| **React double-invoked effect** (`reactStrictMode: true`) | The first pass is aborted by cleanup; the second decides. Both would have been safe — the write is an upsert — but the check makes the second a no-op. |
| **Back from the dashboard** | Cannot reach the step: it `replace`d its entry. Back lands where it did before this change (`/signin` on the demo path, the provider on OAuth). |
| **Back to the *failure* card** (it does keep its entry) | Restored from bfcache with the card showing; "Try again" still works, and the stash is intact. |
| **Two tabs completing the funnel** | Both read the same stash; the first writes, the second's check finds the row and passes through. Last explicit save wins if the reader actually re-picks in the second tab, which is their own action. |
| **OAuth abandoned at the provider** | No session, so the step is never reached. The stash survives; the middleware sends them to the funnel. |
| **`?error=AccessDenied`** | NextAuth routes to `/signin`, not here. The stash survives for an eventual approved sign-in. |
| **Landing step opened while signed out** | No session to attribute, so it forwards to `/` and the middleware takes over. |
| **Network failure / 5xx on the write** | Retry card, stash intact, capped at two attempts before the funnel becomes the primary action. |
| **Hung server (no response at all)** | The 12 s abort turns it into the same retry card. |
| **A stash the registry no longer accepts** (renamed outlet) | The engine 400s, which the web route flattens to 503, so it presents as retryable. Two attempts later the funnel is the primary action, and completing it clears the poisoned stash via the authenticated save path. |

**One known gap, out of scope here.** If the engine is unreachable *during sign-in*, the `jwt` callback
never resolves `engineUserId`, so the session exists but cannot be attributed. Every per-user call
401s — the landing step's write included — and no retry can fix it, because `engineUserId` is only
resolved on the initial sign-in. Signing out and back in is the only recovery. This predates the
onboarding work and affects every authenticated surface, not just this step; the fix belongs in
`lib/auth.ts` (re-resolve the engine id in the `jwt` callback whenever the token lacks one, keyed off
the stored provider + `token.sub`), and should be its own change with its own tests.

## 9. Tests that hold it in place

| Test | Locks in |
|---|---|
| `tests/test_api_fastapi.py::test_me_carries_the_two_facts_the_onboarding_gate_reads` | `/api/me` carries `onboarding` **and** `reads`; a fresh account reports `reads: 0`, and reading alone clears the gate without an onboarding row. Fails on the pre-fix engine. |
| `web/lib/onboarding.test.ts` | The stash contract `/signin/complete` branches on: a round trip, that clearing is consuming (which is what makes the landing loop-proof), and that every malformed shape reads as "nothing to do" instead of throwing or returning junk. Plus `needsOnboarding()` — the predicate the gate and the landing step share, including that an absent key and an explicit `null` read alike, and that `reads` alone settles it. |
| `web/e2e/specs/auth.spec.ts` — *"signing in without onboarding lands on the funnel, not the app"* | The bypass itself: sign in at `/signin` with no prior funnel → `/onboarding`. |
| … *"completing the funnel then signing in lands in the app, not back at the funnel"* | The regression the landing step exists to prevent — it walks the funnel, signs in, and asserts the dashboard. |
| … *"an onboarded reader goes straight to the dashboard"* | The gate not over-firing — the failure mode worse than the bug. |
| … *"re-entering the sign-in landing step is a no-op for an onboarded reader"* | Idempotency from the direction that happens in the wild (refresh, duplicated tab, bookmark): check-then-write passes an established account through without re-posting or bouncing. |
| … Back assertion in the funnel spec | The landing step replaced its history entry, so Back cannot return to the interstitial. |
| `web/e2e/fixtures.ts` | `authedPage` is an **onboarded** reader, which is what all nine feature specs were always testing. |

The e2e suite needs a built web app and a live engine: `cd web && npm run e2e`.

## 10. What to measure before adding onboarding UI

The activation events already exist ([`PA1_PRODUCT_ANALYTICS.md`](PA1_PRODUCT_ANALYTICS.md)):
`onboarding_started` → `onboarding_step_completed` → `source_connected` → `signin_started` →
`account_created` → `article_read` → `health_report_viewed` (`mode=measured`).

Read the funnel *after* this fix, because until now the denominator was wrong: readers who arrived
through `/signin` were counted as accounts while never having connected a source. Specifically:

1. **`source_connected` ÷ `account_created`** should now be ~1.0. If it isn't, someone is still
   reaching the app un-onboarded and the gate has a hole.
2. **`onboarding_started` → `source_connected`** is the funnel's real drop-off. If readers abandon
   *inside* the picker, that is a UI problem and coach marks or tooltips are worth discussing.
3. **`article_read` after onboarding** — if readers onboard and never read, the gap is between
   onboarding and the first read, which no amount of onboarding UI fixes.

Only (2) argues for tour-style UX, and only with numbers attached.
